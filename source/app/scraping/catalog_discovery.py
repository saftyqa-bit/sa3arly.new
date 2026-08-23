from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from dataclasses import asdict
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree

from app.repository_provider import repository
from app.schemas import CatalogDiscoveryTaskPayload
from app.scraping.document import parse_document
from app.scraping.fetcher import FetchError, SafeFetcher
from app.scraping.normalization import normalize_text
from app.scraping.types import ProductCandidate
from app.settings import get_settings

logger = logging.getLogger(__name__)

RETRYABLE_FETCH_CODES = {
    "timeout",
    "network_error",
    "upstream_error",
    "rate_limited",
    "browser_error",
    "circuit_open",
}
RETRYABLE_INTERNAL_ERROR_CODES = {
    "internal_deadlockdetected",
    "internal_operationalerror",
    "internal_pooltimeout",
    "internal_serializationfailure",
}
TYPE_ERROR_SIGNATURES = (
    ("unexpected_keyword", "unexpected keyword argument"),
    ("positional_argument", "positional argument"),
    ("not_iterable", "not iterable"),
    ("not_subscriptable", "not subscriptable"),
    ("unhashable", "unhashable type"),
    ("unsupported_operand", "unsupported operand type"),
    ("cannot_unpack", "cannot unpack"),
    ("missing_argument", "required positional argument"),
)

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_",
    "source",
}
REJECTED_EXTENSIONS = {
    ".7z",
    ".avi",
    ".css",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".m4a",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".rar",
    ".svg",
    ".txt",
    ".webp",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}
REJECTED_PATH_PARTS = {
    "account",
    "about",
    "author",
    "blog",
    "cart",
    "checkout",
    "compare",
    "contact",
    "faq",
    "help",
    "login",
    "logout",
    "news",
    "privacy",
    "register",
    "returns",
    "search",
    "signin",
    "signup",
    "terms",
    "wishlist",
}
PRODUCT_PATH_HINTS = {
    "dp",
    "item",
    "items",
    "p",
    "product",
    "products",
    "product-detail",
    "productdetails",
    "sku",
}
GTIN_RE = re.compile(r"(?<!\d)(\d{8}|\d{12,14})(?!\d)")


def canonical_catalog_url(value: str) -> str:
    """Canonicalize a product URL without removing meaningful variant keys."""

    parsed = urlparse(value.strip())
    scheme = parsed.scheme.lower()
    # Preserve the requested hostname in the fetchable URL. Some retailers do
    # not serve the apex domain even though www and apex are equivalent for
    # allowlist comparison.
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    path = re.sub(r"/+", "/", unquote(parsed.path or "/"))
    path = path.rstrip("/") or "/"
    kept_query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=False):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
            continue
        kept_query.append((key, item))
    kept_query.sort()
    return urlunparse((scheme, host + port, path, "", urlencode(kept_query), ""))


def _same_allowed_host(url: str, allowed_hosts: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    allowed = {
        value.lower().removeprefix("www.").rstrip(".")
        for value in allowed_hosts
        if value
    }
    return bool(host) and (
        not allowed
        or any(host == item or host.endswith("." + item) for item in allowed)
    )


def title_from_url(url: str) -> str:
    path = unquote(urlparse(url).path).strip("/")
    if not path:
        return ""
    value = path.split("/")[-1]
    value = re.sub(r"\.(?:html?|aspx?|php)$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[-_+]+", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()


def likely_product_url(url: str, *, product_sitemap: bool = False) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    lowered_path = parsed.path.lower()
    if any(lowered_path.endswith(extension) for extension in REJECTED_EXTENSIONS):
        return False
    parts = {part for part in re.split(r"[/_.-]+", lowered_path) if part}
    if parts.intersection(REJECTED_PATH_PARTS):
        return False
    if product_sitemap:
        return lowered_path not in {"", "/"}
    if parts.intersection(PRODUCT_PATH_HINTS):
        return True
    # Commerce platforms commonly use a descriptive slug followed by a stable
    # numeric/model suffix without an explicit /product/ path.
    leaf = title_from_url(url)
    return len(leaf) >= 10 and bool(re.search(r"[a-z\u0600-\u06ff]", leaf, re.I)) and bool(
        re.search(r"\d|[-_]", parsed.path.split("/")[-1])
    )


def extract_robots_sitemaps(body: str, base_url: str) -> list[str]:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    out = []
    for line in body.splitlines():
        if line.lower().lstrip().startswith("sitemap:"):
            value = line.split(":", 1)[1].strip()
            if value.startswith("/"):
                value = origin + value
            if value.startswith(("http://", "https://")):
                out.append(value)
    return list(dict.fromkeys(out))


def parse_sitemap(body: str, final_url: str) -> tuple[list[str], list[ProductCandidate]]:
    """Return child sitemap URLs and candidate product URLs."""

    root = ElementTree.fromstring(body)

    def local(tag: str) -> str:
        return tag.split("}", 1)[-1].lower()

    root_type = local(root.tag)
    child_sitemaps: list[str] = []
    candidates: list[ProductCandidate] = []
    product_sitemap = "product" in urlparse(final_url).path.lower()

    if root_type == "sitemapindex":
        for element in root.iter():
            if local(element.tag) == "loc" and (element.text or "").strip():
                child_sitemaps.append((element.text or "").strip())
        return list(dict.fromkeys(child_sitemaps)), candidates

    for element in root.iter():
        if local(element.tag) != "url":
            continue
        data: dict[str, str] = {}
        for child in element.iter():
            key = local(child.tag)
            value = (child.text or "").strip()
            if value and key in {"loc", "title", "caption"}:
                data.setdefault(key, value)
        url = data.get("loc")
        if not url or not likely_product_url(url, product_sitemap=product_sitemap):
            continue
        candidates.append(
            ProductCandidate(
                title=data.get("title") or data.get("caption") or title_from_url(url),
                url=url,
                source_method="sitemap_product_url",
                text=json.dumps(data, ensure_ascii=False),
                raw=data,
            )
        )
    return child_sitemaps, candidates


def candidate_fingerprint(candidate: ProductCandidate) -> str | None:
    gtin = re.sub(r"\D", "", str(candidate.gtin or ""))
    if len(gtin) in {8, 12, 13, 14}:
        return "gtin:" + gtin
    title = normalize_text(candidate.title)
    brand = normalize_text(candidate.brand)
    sku = normalize_text(candidate.sku)
    if brand and sku and len(sku) >= 4:
        return "brand-sku:" + hashlib.sha1(f"{brand}|{sku}".encode()).hexdigest()[:24]
    if title:
        possible = GTIN_RE.search(title.replace(" ", ""))
        if possible:
            return "gtin:" + possible.group(1)
    return None


def _bounded_optional_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _internal_error_code(exc: Exception) -> str:
    error_type = re.sub(
        r"[^a-z0-9_]+",
        "_",
        type(exc).__name__.lower(),
    ).strip("_") or "unknown"
    if isinstance(exc, TypeError):
        message = str(exc).lower()
        for suffix, signature in TYPE_ERROR_SIGNATURES:
            if signature in message:
                return f"internal_typeerror_{suffix}"[:80]
    return f"internal_{error_type}"[:80]


def _retryable_internal_error(error_code: str) -> bool:
    """Retry transient database/runtime pressure without hiding code defects."""

    return error_code in RETRYABLE_INTERNAL_ERROR_CODES


def _candidate_dict(
    candidate: ProductCandidate,
    *,
    allowed_hosts: list[str],
    final_url: str,
) -> dict[str, Any] | None:
    if not candidate.url:
        return None
    url = canonical_catalog_url(candidate.url)
    if not _same_allowed_host(url, allowed_hosts):
        return None
    if not likely_product_url(
        url,
        product_sitemap=candidate.source_method == "sitemap_product_url",
    ):
        return None
    title = (candidate.title or title_from_url(url)).strip()
    if len(normalize_text(title)) < 3:
        return None
    gtin = re.sub(r"\D", "", str(candidate.gtin or "")) or None
    if gtin and len(gtin) not in {8, 12, 13, 14}:
        gtin = None
    item = asdict(candidate)
    item.update(
        {
            "source_url": url,
            "normalized_url": url,
            "title": title[:1000],
            "brand": _bounded_optional_text(candidate.brand, 250),
            "sku": _bounded_optional_text(candidate.sku, 250),
            "gtin": gtin,
            "fingerprint": candidate_fingerprint(candidate),
            "discovered_from": final_url,
        }
    )
    item["raw"] = item.get("raw") or {}
    return item


class CatalogDiscoveryEngine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.fetcher = SafeFetcher()

    def _reserve(self, payload: CatalogDiscoveryTaskPayload) -> None:
        max_wait = max(0.0, self.settings.max_store_request_wait_seconds)
        wait = repository.reserve_store_request_slot(
            payload.store_id,
            payload.requests_per_minute,
            max_wait_seconds=max_wait,
        )
        if wait > max_wait:
            raise FetchError(
                "Store circuit breaker is open",
                code="circuit_open",
                retry_after_seconds=min(
                    max(1, math.ceil(wait)), self.settings.max_retry_after_seconds
                ),
            )
        if wait > 0:
            time.sleep(wait)

    def _fetch_http(
        self,
        payload: CatalogDiscoveryTaskPayload,
        url: str,
        *,
        respect_robots: bool,
    ):
        self._reserve(payload)
        return self.fetcher.fetch_http(
            url,
            allowed_hosts=payload.allowed_hosts,
            respect_robots=respect_robots,
        )

    def _fetch_source(
        self,
        payload: CatalogDiscoveryTaskPayload,
        url: str,
        *,
        respect_robots: bool,
        allow_browser: bool,
    ):
        if not allow_browser:
            return self._fetch_http(payload, url, respect_robots=respect_robots)
        self._reserve(payload)
        return self.fetcher.fetch(
            url,
            allowed_hosts=payload.allowed_hosts,
            respect_robots=respect_robots,
            browser_required=payload.browser_required,
            browser_actions=payload.connector_config.get("browserActions") or [],
            browser_resource_hosts=list(
                dict.fromkeys(
                    [
                        *payload.allowed_hosts,
                        *(payload.connector_config.get("browserResourceHosts") or []),
                    ]
                )
            ),
        )

    def _source_urls(self, payload: CatalogDiscoveryTaskPayload) -> list[str]:
        parsed = urlparse(payload.source_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        configured = payload.connector_config.get("discoverySources") or []
        values = [payload.source_url]
        values.extend(str(value) for value in configured if value)
        values.extend(
            [
                origin + "/robots.txt",
                origin + "/sitemap.xml",
                origin + "/sitemap_index.xml",
                origin + "/product-sitemap.xml",
            ]
        )
        return list(dict.fromkeys(values))

    def _collect(self, payload: CatalogDiscoveryTaskPayload) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        queue = self._source_urls(payload)
        seen_sources: set[str] = set()
        seen_candidates: set[str] = set()
        candidates: list[dict[str, Any]] = []
        total_bytes = 0
        statuses: list[int] = []
        source_failures: dict[str, int] = {}
        sitemap_count = 0
        successful_fetches = 0

        while queue and len(seen_sources) < self.settings.catalog_discovery_max_source_fetches:
            source_url = queue.pop(0)
            source_key = canonical_catalog_url(source_url)
            if source_key in seen_sources or not _same_allowed_host(source_url, payload.allowed_hosts):
                continue
            seen_sources.add(source_key)
            is_robots = urlparse(source_url).path.rstrip("/").lower().endswith("robots.txt")
            try:
                result = self._fetch_source(
                    payload,
                    source_url,
                    respect_robots=False if is_robots else payload.respect_robots,
                    allow_browser=source_url == payload.source_url and not is_robots,
                )
            except FetchError as exc:
                source_failures[exc.code] = source_failures.get(exc.code, 0) + 1
                # A blocked or dynamic landing page must not prevent discovery
                # from trying robots.txt and the standard sitemap locations.
                # Report the failure only after every public source is exhausted.
                continue
            successful_fetches += 1
            total_bytes += int(result.response_bytes or 0)
            statuses.append(result.status_code)
            body = result.body or ""
            if not body:
                continue

            if is_robots:
                for sitemap in extract_robots_sitemaps(body, result.final_url):
                    if sitemap not in queue and sitemap_count < self.settings.catalog_discovery_max_sitemaps_per_store:
                        queue.append(sitemap)
                        sitemap_count += 1
                continue

            media = (result.content_type or "").lower()
            looks_xml = "xml" in media or body.lstrip().startswith("<?xml") or "<urlset" in body[:1000] or "<sitemapindex" in body[:1000]
            parsed_candidates: list[ProductCandidate]
            if looks_xml:
                try:
                    child_sitemaps, parsed_candidates = parse_sitemap(body, result.final_url)
                    for child in child_sitemaps:
                        if sitemap_count >= self.settings.catalog_discovery_max_sitemaps_per_store:
                            break
                        if child not in queue:
                            queue.append(child)
                            sitemap_count += 1
                except ElementTree.ParseError:
                    parsed_candidates = []
            else:
                document = parse_document(
                    body,
                    result.final_url,
                    result.content_type,
                    connector_config=payload.connector_config,
                )
                parsed_candidates = [*document.candidates, *document.links]

            for candidate in parsed_candidates:
                item = _candidate_dict(
                    candidate,
                    allowed_hosts=payload.allowed_hosts,
                    final_url=result.final_url,
                )
                if not item:
                    continue
                url_key = item["normalized_url"]
                if url_key in seen_candidates:
                    continue
                seen_candidates.add(url_key)
                candidates.append(item)
                if len(candidates) >= self.settings.catalog_discovery_max_candidates_per_store:
                    queue.clear()
                    break

        if not successful_fetches:
            code = next(iter(source_failures), "no_public_source")
            raise FetchError("No public catalog source could be read", code=code)
        return candidates, {
            "response_bytes": total_bytes,
            "http_status": statuses[0] if statuses else None,
            "source_fetches": successful_fetches,
            "source_failures": source_failures,
            "sitemaps_followed": sitemap_count,
        }

    def _open_store_circuit(self, store_id: str, exc: FetchError) -> int | None:
        if exc.code not in RETRYABLE_FETCH_CODES:
            return None
        if exc.retry_after_seconds is not None:
            delay = exc.retry_after_seconds
        elif exc.code == "rate_limited":
            delay = self.settings.store_rate_limit_cooldown_seconds
        else:
            delay = self.settings.store_transient_cooldown_seconds
        delay = min(max(1, int(delay)), self.settings.max_retry_after_seconds)
        repository.defer_store_requests(store_id, delay)
        return delay

    def _hydrate_product_candidates(
        self,
        payload: CatalogDiscoveryTaskPayload,
        candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Fetch prioritized product pages so new URLs acquire price evidence now."""

        selector = getattr(repository, "select_catalog_hydration_candidates", None)
        limit = max(0, self.settings.catalog_discovery_max_product_fetches_per_store)
        selected = (
            selector(payload.store_id, candidates, limit=limit)
            if callable(selector)
            else candidates[:limit]
        )
        hydrated_by_url: dict[str, dict[str, Any]] = {}
        fetched = 0
        failures = 0
        response_bytes = 0
        for original in selected:
            url = str(original.get("normalized_url") or "")
            if not url:
                continue
            try:
                result = self._fetch_source(
                    payload,
                    url,
                    respect_robots=payload.respect_robots,
                    allow_browser=payload.browser_required,
                )
            except FetchError:
                failures += 1
                continue
            fetched += 1
            response_bytes += int(result.response_bytes or 0)
            try:
                document = parse_document(
                    result.body or "",
                    result.final_url,
                    result.content_type,
                    connector_config=payload.connector_config,
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                failures += 1
                logger.warning(
                    "Catalog product page could not be parsed",
                    extra={"store_id": payload.store_id, "source_url": url},
                )
                continue
            page_items = [
                item
                for candidate in document.candidates
                if (
                    item := _candidate_dict(
                        candidate,
                        allowed_hosts=payload.allowed_hosts,
                        final_url=result.final_url,
                    )
                )
            ]
            if not page_items:
                continue
            exact = next(
                (
                    item
                    for item in page_items
                    if canonical_catalog_url(item["normalized_url"])
                    == canonical_catalog_url(result.final_url)
                ),
                None,
            )
            chosen = exact or max(
                page_items,
                key=lambda item: (
                    item.get("price") is not None,
                    bool(item.get("gtin")),
                    bool(item.get("brand")),
                ),
            )
            # The discovered store URL remains the observation identity even
            # when embedded structured data rewrites its canonical URL.
            chosen["source_url"] = url
            chosen["normalized_url"] = url
            hydrated_by_url[url] = chosen
        return [
            hydrated_by_url.get(str(item.get("normalized_url") or ""), item)
            for item in candidates
        ], {
            "product_pages_selected": len(selected),
            "product_pages_fetched": fetched,
            "product_page_failures": failures,
            "hydrated_candidates": len(hydrated_by_url),
            "product_page_response_bytes": response_bytes,
        }

    def process(
        self,
        payload: CatalogDiscoveryTaskPayload,
        *,
        cloud_tasks_retry_count: int = 0,
    ) -> dict[str, Any]:
        lock_slot = int(hashlib.sha1(payload.task_id.encode()).hexdigest(), 16) % max(
            1, payload.max_concurrency
        )
        with repository.store_advisory_lock(payload.store_id, lock_slot):
            claim = repository.start_catalog_discovery_task(
                payload.task_id,
                delivery_generation=payload.delivery_generation,
                allow_reclaim_running=cloud_tasks_retry_count > 0,
            )
            if claim in {"terminal", "running", "missing", "superseded"}:
                return {"task_id": payload.task_id, "status": f"already_{claim}"}
            try:
                candidates, metrics = self._collect(payload)
                candidates, hydration_metrics = self._hydrate_product_candidates(
                    payload,
                    candidates,
                )
                metrics.update(hydration_metrics)
                metrics["response_bytes"] = int(metrics.get("response_bytes") or 0) + int(
                    hydration_metrics.get("product_page_response_bytes") or 0
                )
                ingestion = repository.ingest_catalog_candidates(
                    payload,
                    candidates,
                )
                repository.finish_catalog_discovery_task(
                    payload.task_id,
                    status="success",
                    delivery_generation=payload.delivery_generation,
                    http_status=metrics.get("http_status"),
                    response_bytes=metrics.get("response_bytes", 0),
                    candidates_seen=len(candidates),
                    candidates_new=ingestion.get("candidates_new", 0),
                    mappings_created=ingestion.get("mappings_created", 0),
                    provisional_products=ingestion.get("provisional_products", 0),
                    verified_products=ingestion.get("verified_products", 0),
                    metrics={**metrics, **ingestion},
                )
                return {
                    "task_id": payload.task_id,
                    "status": "success",
                    "candidates_seen": len(candidates),
                    **ingestion,
                }
            except FetchError as exc:
                retryable = exc.code in RETRYABLE_FETCH_CODES
                retry_after = self._open_store_circuit(payload.store_id, exc) if retryable else None
                repository.finish_catalog_discovery_task(
                    payload.task_id,
                    status="retryable_failed" if retryable else "failed",
                    delivery_generation=payload.delivery_generation,
                    error_code=exc.code,
                    error_message=str(exc),
                    metrics={"retry_after_seconds": retry_after},
                )
                result = {
                    "task_id": payload.task_id,
                    "status": "retryable_failed" if retryable else "failed",
                    "error": exc.code,
                }
                if retry_after:
                    result["retry_after_seconds"] = retry_after
                return result
            except Exception as exc:
                error_code = _internal_error_code(exc)
                retryable = _retryable_internal_error(error_code)
                logger.exception(
                    "Unhandled catalog discovery failure",
                    extra={
                        "task_id": payload.task_id,
                        "store_id": payload.store_id,
                        "error_code": error_code,
                    },
                )
                repository.finish_catalog_discovery_task(
                    payload.task_id,
                    status="retryable_failed" if retryable else "failed",
                    delivery_generation=payload.delivery_generation,
                    error_code=error_code,
                    error_message=str(exc),
                )
                return {
                    "task_id": payload.task_id,
                    "status": "retryable_failed" if retryable else "failed",
                    "error": error_code,
                }
