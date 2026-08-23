from __future__ import annotations

import socket
import threading
import time
from urllib.parse import urljoin, urlparse

import httpx

from app.schemas import FetchResult
from app.scraping.fetcher import FetchError, parse_retry_after, public_url_fetch_error
from app.scraping.fetcher import SafeFetcher as LegacySafeFetcher
from app.scraping.robots import robots_can_fetch
from app.security import resolve_pinned_ip


class SafeFetcher(LegacySafeFetcher):
    """Security-hardened transport preserving the legacy fetcher public API."""

    _robots_cache: dict[str, tuple[float, str]] = {}
    _robots_lock = threading.Lock()

    def _robots_allowed(self, url: str, allowed_hosts: list[str]) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        cache_key = parsed.netloc.lower()
        now = time.time()

        with self._robots_lock:
            cached = self._robots_cache.get(cache_key)
            if cached and now - cached[0] < 6 * 3600:
                return robots_can_fetch(cached[1], self.settings.user_agent, url)

        robots_text = ""
        try:
            # Reuse the hardened pinned-IP transport for robots.txt too. A
            # separate validate-then-connect request would reopen a DNS
            # rebinding window before the real page fetch.
            result = self.fetch_http(
                robots_url,
                allowed_hosts=allowed_hosts,
                respect_robots=False,
            )
            if result.status_code < 400:
                robots_text = result.body or ""
        except Exception:
            # A missing or unreachable robots file is not a prohibition.
            robots_text = ""

        with self._robots_lock:
            self._robots_cache[cache_key] = (now, robots_text)
        return robots_can_fetch(robots_text, self.settings.user_agent, url)

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
                # Use the exact public IP approved during validation. Host and
                # SNI retain the original hostname for TLS and virtual hosting.
                try:
                    hostname, pinned_ip = resolve_pinned_ip(current, allowed_hosts)
                except socket.gaierror as exc:
                    raise FetchError(
                        "Store hostname could not be resolved",
                        code="network_error",
                    ) from exc
                except ValueError as exc:
                    raise public_url_fetch_error(exc) from exc
                pinned_url = httpx.URL(current).copy_with(host=pinned_ip)
                request_headers = dict(headers)
                request_headers["Host"] = hostname
                try:
                    # Use httpx's supported streaming request API so header and
                    # extension normalization happens before the transport
                    # boundary. The URL still targets the validated pinned IP;
                    # Host and SNI preserve the original public hostname.
                    with client.stream(
                        "GET",
                        pinned_url,
                        headers=request_headers,
                        extensions={"sni_hostname": hostname},
                    ) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise FetchError(
                                    "Redirect had no Location header",
                                    code="bad_redirect",
                                )
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
                            code = {
                                401: "unauthorized",
                                403: "blocked",
                                429: "rate_limited",
                            }[response.status_code]
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
                                code=(
                                    "not_found"
                                    if response.status_code == 404
                                    else "http_error"
                                ),
                                status_code=response.status_code,
                            )

                        chunks: list[bytes] = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > settings.max_response_bytes:
                                raise FetchError(
                                    "Response exceeded configured byte limit",
                                    code="response_too_large",
                                )
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
                    if http2:
                        raise
                    raise FetchError(
                        "Remote protocol error while contacting store",
                        code="network_error",
                    ) from exc
                except httpx.TimeoutException as exc:
                    raise FetchError(
                        "Store request timed out",
                        code="timeout",
                    ) from exc
                except httpx.RequestError as exc:
                    raise FetchError(
                        "Network error while contacting store",
                        code="network_error",
                    ) from exc
        raise FetchError("Too many redirects", code="too_many_redirects")
