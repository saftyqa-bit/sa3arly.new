from __future__ import annotations

import logging
import math
import socket
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.schemas import FetchResult
from app.security import validate_public_url
from app.settings import get_settings

logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "fetch_error",
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def public_url_fetch_error(exc: ValueError) -> FetchError:
    """Convert URL validation rejections into bounded, typed fetch failures."""

    code = (
        "host_not_allowed"
        if "outside the configured store allowlist" in str(exc)
        else "invalid_url"
    )
    return FetchError("Store URL failed public URL validation", code=code)


@dataclass(slots=True)
class CacheHeaders:
    etag: str | None = None
    last_modified: str | None = None


def validate_fetch_url(url: str, allowed_hosts: list[str]) -> str:
    """Translate expected URL/DNS rejection into stable scraper failures."""

    try:
        return validate_public_url(url, allowed_hosts)
    except socket.gaierror as exc:
        raise FetchError(
            "Store hostname could not be resolved",
            code="network_error",
        ) from exc
    except ValueError as exc:
        raise public_url_fetch_error(exc) from exc


def parse_retry_after(
    value: str | None,
    *,
    now: datetime | None = None,
    max_seconds: int = 3600,
) -> int | None:
    """Parse an HTTP Retry-After delta or date into bounded whole seconds."""

    if not value:
        return None
    raw = value.strip()
    seconds: float
    try:
        seconds = float(raw)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        seconds = (retry_at - reference).total_seconds()
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return min(max(1, math.ceil(seconds)), max(1, int(max_seconds)))


class SafeFetcher:
    _robots_cache: dict[str, tuple[float, RobotFileParser]] = {}
    _robots_lock = threading.Lock()

    def __init__(self) -> None:
        self.settings = get_settings()

    def _robots_allowed(self, url: str, allowed_hosts: list[str]) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        cache_key = parsed.netloc.lower()
        now = time.time()

        with self._robots_lock:
            cached = self._robots_cache.get(cache_key)
            if cached and now - cached[0] < 6 * 3600:
                return cached[1].can_fetch(self.settings.user_agent, url)

        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            validate_public_url(robots_url, allowed_hosts)
            with httpx.Client(timeout=8, headers={"User-Agent": self.settings.user_agent}) as client:
                response = client.get(robots_url)
            if response.status_code < 400:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])
        except Exception:
            # A missing/unreachable robots file is not interpreted as a prohibition.
            parser.parse([])

        with self._robots_lock:
            self._robots_cache[cache_key] = (now, parser)
        return parser.can_fetch(self.settings.user_agent, url)

    def _fetch_http_once(
        self,
        url: str,
        *,
        allowed_hosts: list[str],
        headers: dict[str, str],
        http2: bool,
    ) -> FetchResult:
        settings = self.settings
        current = url
        with httpx.Client(
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            headers=headers,
            follow_redirects=False,
            http2=http2,
        ) as client:
            for _ in range(settings.max_redirects + 1):
                validate_fetch_url(current, allowed_hosts)
                try:
                    with client.stream("GET", current) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise FetchError("Redirect had no Location header", code="bad_redirect")
                            current = urljoin(current, location)
                            continue

                        if response.status_code == 304:
                            return FetchResult(
                                url=url,
                                final_url=current,
                                status_code=304,
                                content_type=response.headers.get("content-type"),
                                etag=response.headers.get("etag"),
                                last_modified=response.headers.get("last-modified"),
                                response_bytes=0,
                                not_modified=True,
                            )

                        if response.status_code in {401, 403, 429}:
                            code = {401: "unauthorized", 403: "blocked", 429: "rate_limited"}[response.status_code]
                            raise FetchError(
                                f"HTTP {response.status_code} from store",
                                code=code,
                                status_code=response.status_code,
                                retry_after_seconds=(
                                    parse_retry_after(
                                        response.headers.get("retry-after"),
                                        max_seconds=settings.max_retry_after_seconds,
                                    )
                                    if response.status_code == 429
                                    else None
                                ),
                            )
                        if response.status_code >= 500:
                            raise FetchError(
                                f"Store returned HTTP {response.status_code}",
                                code="upstream_error",
                                status_code=response.status_code,
                            )
                        if response.status_code >= 400:
                            raise FetchError(
                                f"Store returned HTTP {response.status_code}",
                                code="not_found" if response.status_code == 404 else "http_error",
                                status_code=response.status_code,
                            )

                        chunks: list[bytes] = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > settings.max_response_bytes:
                                raise FetchError("Response exceeded configured byte limit", code="response_too_large")
                            chunks.append(chunk)
                        raw = b"".join(chunks)
                        encoding = response.encoding or "utf-8"
                        body = raw.decode(encoding, errors="replace")
                        return FetchResult(
                            url=url,
                            final_url=current,
                            status_code=response.status_code,
                            content_type=response.headers.get("content-type"),
                            body=body,
                            etag=response.headers.get("etag"),
                            last_modified=response.headers.get("last-modified"),
                            response_bytes=total,
                        )
                except httpx.RemoteProtocolError as exc:
                    # The caller retries this once with HTTP/1.1. If that also
                    # fails, it is classified as a normal transient network error.
                    if http2:
                        raise
                    raise FetchError(
                        "Remote protocol error while contacting store",
                        code="network_error",
                    ) from exc
                except httpx.TimeoutException as exc:
                    raise FetchError("Store request timed out", code="timeout") from exc
                except httpx.RequestError as exc:
                    raise FetchError("Network error while contacting store", code="network_error") from exc
        raise FetchError("Too many redirects", code="too_many_redirects")

    def fetch_http(
        self,
        url: str,
        *,
        allowed_hosts: list[str],
        respect_robots: bool,
        cache_headers: CacheHeaders | None = None,
    ) -> FetchResult:
        settings = self.settings
        validate_fetch_url(url, allowed_hosts)
        if respect_robots and settings.respect_robots_txt and not self._robots_allowed(url, allowed_hosts):
            raise FetchError("robots.txt disallows this URL", code="robots_disallowed")

        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
        }
        if cache_headers and cache_headers.etag:
            headers["If-None-Match"] = cache_headers.etag
        if cache_headers and cache_headers.last_modified:
            headers["If-Modified-Since"] = cache_headers.last_modified

        try:
            return self._fetch_http_once(
                url,
                allowed_hosts=allowed_hosts,
                headers=headers,
                http2=True,
            )
        except (httpx.RemoteProtocolError, TypeError) as exc:
            # Some real-world HTTP/2 stacks fail inside httpx/httpcore with a
            # TypeError before a response is available. A single idempotent
            # HTTP/1.1 retry preserves the pinned-IP SSRF protection while
            # avoiding a hard failure for those storefronts. Deterministic
            # TypeErrors still escape from the HTTP/1.1 attempt for diagnosis.
            logger.info(
                "HTTP/2 transport incompatibility; retrying store request with HTTP/1.1",
                extra={
                    "store_url": url,
                    "fallback_reason": type(exc).__name__,
                },
            )
            return self._fetch_http_once(
                url,
                allowed_hosts=allowed_hosts,
                headers=headers,
                http2=False,
            )

    def fetch_browser(
        self,
        url: str,
        *,
        allowed_hosts: list[str],
        respect_robots: bool,
        browser_actions: list[dict[str, Any]] | None = None,
        browser_resource_hosts: list[str] | None = None,
    ) -> FetchResult:
        settings = self.settings
        validate_fetch_url(url, allowed_hosts)
        if respect_robots and settings.respect_robots_txt and not self._robots_allowed(url, allowed_hosts):
            raise FetchError("robots.txt disallows this URL", code="robots_disallowed")

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError("Playwright is not installed in this service", code="browser_unavailable") from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=settings.user_agent,
                locale="ar-EG",
                timezone_id="Africa/Cairo",
                java_script_enabled=True,
            )
            page = context.new_page()
            request_count = 0
            resource_allowlist = browser_resource_hosts or allowed_hosts

            def route_handler(route):
                nonlocal request_count
                request_count += 1
                if request_count > 250:
                    route.abort()
                    return
                request_url = route.request.url
                if request_url.startswith(("data:", "blob:", "about:")):
                    route.continue_()
                    return
                if route.request.resource_type in {"image", "media", "font"}:
                    route.abort()
                    return
                try:
                    validate_public_url(request_url, resource_allowlist)
                except Exception:
                    route.abort()
                    return
                route.continue_()

            page.route("**/*", route_handler)
            try:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(settings.http_timeout_seconds * 1000),
                )
                if response and response.status in {401, 403, 429}:
                    code = {
                        401: "unauthorized",
                        403: "blocked",
                        429: "rate_limited",
                    }[response.status]
                    raise FetchError(
                        f"HTTP {response.status} from store browser response",
                        code=code,
                        status_code=response.status,
                    )
                if response and response.status >= 500:
                    raise FetchError(
                        f"Store returned HTTP {response.status} in browser",
                        code="upstream_error",
                        status_code=response.status,
                    )
                page.wait_for_timeout(1200)
                for configured_action in (browser_actions or [])[:10]:
                    if not isinstance(configured_action, dict):
                        continue
                    action = str(configured_action.get("action") or "").strip().lower()
                    selector = str(configured_action.get("selector") or "").strip()[:300]
                    optional = bool(configured_action.get("optional", True))
                    try:
                        configured_timeout = int(configured_action.get("timeout_ms") or 3000)
                    except (TypeError, ValueError):
                        configured_timeout = 3000
                    timeout_ms = min(max(configured_timeout, 100), 5000)
                    if not selector or action not in {"click", "wait_for_selector", "select_option"}:
                        continue
                    try:
                        if action == "click":
                            page.locator(selector).first.click(timeout=timeout_ms)
                        elif action == "wait_for_selector":
                            page.wait_for_selector(selector, timeout=timeout_ms)
                        elif action == "select_option":
                            value = str(configured_action.get("value") or "")[:200]
                            page.locator(selector).first.select_option(value=value, timeout=timeout_ms)
                        page.wait_for_timeout(min(timeout_ms, 1200))
                    except Exception:
                        if not optional:
                            raise
                        logger.info(
                            "Optional browser action did not complete",
                            extra={"action": action, "selector": selector},
                        )
                final_url = page.url
                validate_public_url(final_url, allowed_hosts)
                body = page.content()
                raw = body.encode("utf-8")
                if len(raw) > settings.max_response_bytes:
                    raise FetchError("Rendered response exceeded byte limit", code="response_too_large")
                return FetchResult(
                    url=url,
                    final_url=final_url,
                    status_code=response.status if response else 200,
                    content_type="text/html; rendered=playwright",
                    body=body,
                    response_bytes=len(raw),
                    used_browser=True,
                )
            except Exception as exc:
                if isinstance(exc, FetchError):
                    raise
                raise FetchError("Browser rendering failed", code="browser_error") from exc
            finally:
                context.close()
                browser.close()

    def fetch(
        self,
        url: str,
        *,
        allowed_hosts: list[str],
        respect_robots: bool,
        browser_required: bool,
        cache_headers: CacheHeaders | None = None,
        browser_actions: list[dict[str, Any]] | None = None,
        browser_resource_hosts: list[str] | None = None,
    ) -> FetchResult:
        if browser_required:
            return self.fetch_browser(
                url,
                allowed_hosts=allowed_hosts,
                respect_robots=respect_robots,
                browser_actions=browser_actions,
                browser_resource_hosts=browser_resource_hosts,
            )

        try:
            result = self.fetch_http(
                url,
                allowed_hosts=allowed_hosts,
                respect_robots=respect_robots,
                cache_headers=cache_headers,
            )
            if result.body and self.settings.enable_browser_fallback:
                body_lower = result.body[:200_000].lower()
                shell_markers = (
                    "__next_data__",
                    "enable javascript",
                    "please enable javascript",
                    "data-reactroot",
                )
                has_price_hint = any(x in body_lower for x in ("price", "جنيه", "egp"))
                if any(x in body_lower for x in shell_markers) and not has_price_hint:
                    return self.fetch_browser(
                        url,
                        allowed_hosts=allowed_hosts,
                        respect_robots=respect_robots,
                        browser_actions=browser_actions,
                        browser_resource_hosts=browser_resource_hosts,
                    )
            return result
        except FetchError as exc:
            if (
                self.settings.enable_browser_fallback
                and exc.code in {"blocked", "http_error", "browser_unavailable"}
                and exc.code != "robots_disallowed"
            ):
                return self.fetch_browser(
                    url,
                    allowed_hosts=allowed_hosts,
                    respect_robots=respect_robots,
                    browser_actions=browser_actions,
                    browser_resource_hosts=browser_resource_hosts,
                )
            raise
