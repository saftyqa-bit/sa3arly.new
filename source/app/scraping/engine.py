from __future__ import annotations

import hashlib
import logging
import math
import time
import traceback
from collections import Counter
from dataclasses import asdict
from typing import Any

from app.repository_provider import repository
from app.schemas import CashOfferExtract, ScrapeGroupPayload
from app.scraping.document import parse_document
from app.scraping.fetcher import CacheHeaders, FetchError, SafeFetcher
from app.scraping.installments import extract_installment_plans
from app.scraping.matching import choose_best_candidate
from app.scraping.types import ParsedDocument, ProductCandidate
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

# Parsed page-cache entries contain extraction results, not the original HTML.
# Increment this whenever a parser change makes older candidates unsafe to reuse
# after an HTTP 304 response. Version 2 invalidates entries created before the
# B.TECH Q11i SKU/price guard, which could contain a false EGP 3,005 candidate
# derived from model code A3005.
PARSED_PAGE_CACHE_SCHEMA_VERSION = 2


def _source_url_error(
    failure_reasons: Counter[str], *, all_mappings_failed: bool
) -> str | None:
    """Keep the useful root cause when one URL failed every mapping."""

    if not failure_reasons:
        return "no_mappings" if all_mappings_failed else None
    if not all_mappings_failed:
        return "partial_failures"
    if len(failure_reasons) == 1:
        return next(iter(failure_reasons))
    return "all_mappings_failed"


def _failure_summary(failure_reasons: Counter[str]) -> str | None:
    if not failure_reasons:
        return None
    return ", ".join(
        f"{code}={count}" for code, count in failure_reasons.most_common()
    )


def _candidate_to_dict(candidate: ProductCandidate) -> dict[str, Any]:
    return asdict(candidate)


def _cache_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return None


def _cache_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _candidate_from_dict(value: dict[str, Any]) -> ProductCandidate:
    allowed = ProductCandidate.__dataclass_fields__.keys()
    candidate = {k: v for k, v in value.items() if k in allowed}
    for field in (
        "title",
        "url",
        "currency",
        "availability",
        "seller_name",
        "sku",
        "gtin",
        "brand",
        "image_url",
        "source_method",
        "text",
    ):
        candidate[field] = _cache_text(candidate.get(field))
    for field in ("price", "old_price", "shipping_cost"):
        candidate[field] = _cache_number(candidate.get(field))
    candidate["title"] = candidate.get("title") or candidate.get("text") or ""
    candidate["source_method"] = candidate.get("source_method") or "parsed_cache"
    candidate["text"] = candidate.get("text") or ""
    candidate["raw"] = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else {}
    if not isinstance(candidate.get("free_shipping"), bool):
        candidate["free_shipping"] = None
    return ProductCandidate(**candidate)


def _internal_error_code(exc: Exception) -> str:
    if isinstance(exc, TypeError):
        return "internal_typeerror"
    if isinstance(exc, AttributeError):
        return "internal_attributeerror"
    if isinstance(exc, ValueError):
        return "internal_valueerror"
    if isinstance(exc, KeyError):
        return "internal_keyerror"
    return "internal_error"


def _internal_failure_location(exc: Exception) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "dependency/unknown:unknown:0"
    frame = frames[-1]
    filename = frame.filename.replace("\\", "/")
    if "/app/" in filename:
        safe_path = "app/" + filename.rsplit("/app/", 1)[1]
    else:
        safe_path = "dependency/" + filename.rsplit("/", 1)[-1]
    safe_path = "".join(
        character if character.isalnum() or character in "._-/" else "_"
        for character in safe_path
    )
    safe_function = "".join(
        character if character.isalnum() or character in "_<>-" else "_"
        for character in frame.name
    )
    return f"{safe_path}:{safe_function}:{max(0, int(frame.lineno))}"


def _document_to_cache(document: ParsedDocument) -> dict[str, Any]:
    return {
        "schema_version": PARSED_PAGE_CACHE_SCHEMA_VERSION,
        "final_url": document.final_url,
        "title": document.title,
        "visible_text": document.visible_text[:350_000],
        "candidates": [_candidate_to_dict(x) for x in document.candidates[:1000]],
        "links": [_candidate_to_dict(x) for x in document.links[:3000]],
        "raw_summary": document.raw_summary,
    }


def _cache_candidates(value: Any) -> list[ProductCandidate]:
    if not isinstance(value, list):
        return []
    return [
        _candidate_from_dict(candidate)
        for candidate in value
        if isinstance(candidate, dict)
    ]


def _document_from_cache(value: dict[str, Any]) -> ParsedDocument:
    raw_summary = value.get("raw_summary")
    return ParsedDocument(
        final_url=_cache_text(value.get("final_url")) or "",
        title=_cache_text(value.get("title")) or "",
        visible_text=_cache_text(value.get("visible_text")) or "",
        candidates=_cache_candidates(value.get("candidates")),
        links=_cache_candidates(value.get("links")),
        raw_summary=raw_summary if isinstance(raw_summary, dict) else {},
    )


def _cache_payload_reusable(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    if value.get("schema_version") != PARSED_PAGE_CACHE_SCHEMA_VERSION:
        return False
    if value.get("cache_truncated"):
        return False
    raw_summary = value.get("raw_summary") or {}
    return not bool(
        isinstance(raw_summary, dict) and raw_summary.get("cache_truncated")
    )


def _cached_payload_has_price(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    candidates = value.get("candidates")
    links = value.get("links")
    cached_items = [
        *(candidates if isinstance(candidates, list) else []),
        *(links if isinstance(links, list) else []),
    ]
    return any(
        isinstance(candidate, dict) and candidate.get("price") is not None
        for candidate in cached_items
    )


def _document_has_price(document: ParsedDocument) -> bool:
    return any(
        candidate.price is not None
        for candidate in [*document.candidates, *document.links]
    )


def _is_direct(url_type: str | None) -> bool:
    value = (url_type or "").lower()
    return "مباشر" in value or "product" in value or "منتج مباشر" in value


class ScrapeEngine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.fetcher = SafeFetcher()

    def _open_store_circuit(
        self, store_id: str, exc: FetchError
    ) -> int | None:
        if exc.code not in RETRYABLE_FETCH_CODES:
            return None
        if exc.code == "circuit_open":
            return exc.retry_after_seconds
        if exc.retry_after_seconds is not None:
            delay = exc.retry_after_seconds
        elif exc.code == "rate_limited":
            delay = self.settings.store_rate_limit_cooldown_seconds
        else:
            delay = self.settings.store_transient_cooldown_seconds
        delay = min(max(1, int(delay)), self.settings.max_retry_after_seconds)
        try:
            repository.defer_store_requests(store_id, delay)
        except Exception:
            logger.exception(
                "Could not persist store circuit breaker",
                extra={"store_id": store_id, "error_code": exc.code},
            )
        return delay

    def _load_document(
        self,
        *,
        store_id: str,
        source_url: str,
        allowed_hosts: list[str],
        respect_robots: bool,
        browser_required: bool,
        requests_per_minute: int,
    ) -> tuple[ParsedDocument, int, int, bool]:
        max_wait_seconds = max(0.0, self.settings.max_store_request_wait_seconds)
        wait_seconds = repository.reserve_store_request_slot(
            store_id,
            requests_per_minute,
            max_wait_seconds=max_wait_seconds,
        )
        if wait_seconds > max_wait_seconds:
            raise FetchError(
                "Store circuit breaker is open",
                code="circuit_open",
                retry_after_seconds=min(
                    max(1, math.ceil(wait_seconds)),
                    self.settings.max_retry_after_seconds,
                ),
            )
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        cache = repository.get_page_cache(store_id, source_url)
        cache_headers = CacheHeaders(
            etag=cache.get("etag") if cache else None,
            last_modified=cache.get("last_modified") if cache else None,
        )
        result = self.fetcher.fetch(
            source_url,
            allowed_hosts=allowed_hosts,
            respect_robots=respect_robots,
            browser_required=browser_required,
            cache_headers=cache_headers,
            browser_actions=(self._connector_config or {}).get("browserActions"),
            browser_resource_hosts=list(
                dict.fromkeys(
                    [
                        *allowed_hosts,
                        *((self._connector_config or {}).get("browserResourceHosts") or []),
                    ]
                )
            ),
        )

        if result.not_modified:
            parsed = cache.get("parsed_payload") if cache else None
            cached_price_required = (
                self.settings.enable_browser_fallback and not browser_required
            )
            if not _cache_payload_reusable(parsed) or (
                cached_price_required and not _cached_payload_has_price(parsed)
            ):
                # A proxy may return 304 even if our parsed cache was cleared.
                result = self.fetcher.fetch(
                    source_url,
                    allowed_hosts=allowed_hosts,
                    respect_robots=respect_robots,
                    browser_required=browser_required,
                    cache_headers=None,
                    browser_actions=(self._connector_config or {}).get("browserActions"),
                    browser_resource_hosts=list(
                        dict.fromkeys(
                            [
                                *allowed_hosts,
                                *((self._connector_config or {}).get("browserResourceHosts") or []),
                            ]
                        )
                    ),
                )
            else:
                return _document_from_cache(parsed), 304, 0, False

        body = result.body or ""
        document = parse_document(
            body,
            result.final_url,
            result.content_type,
            connector_config=getattr(self, "_connector_config", None),
        )
        total_response_bytes = result.response_bytes
        if (
            self.settings.enable_browser_fallback
            and not browser_required
            and not result.used_browser
            and not _document_has_price(document)
        ):
            logger.info(
                "No usable price in HTTP document; retrying with browser",
                extra={"store_id": store_id, "source_url": source_url},
            )
            browser_result = self.fetcher.fetch_browser(
                source_url,
                allowed_hosts=allowed_hosts,
                respect_robots=respect_robots,
                browser_actions=(self._connector_config or {}).get("browserActions"),
                browser_resource_hosts=list(
                    dict.fromkeys(
                        [
                            *allowed_hosts,
                            *((self._connector_config or {}).get("browserResourceHosts") or []),
                        ]
                    )
                ),
            )
            total_response_bytes += browser_result.response_bytes
            result = browser_result
            body = result.body or ""
            document = parse_document(
                body,
                result.final_url,
                result.content_type,
                connector_config=getattr(self, "_connector_config", None),
            )
        content_hash = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()
        repository.upsert_page_cache(
            store_id,
            source_url,
            etag=result.etag,
            last_modified=result.last_modified,
            content_hash=content_hash,
            http_status=result.status_code,
            content_type=result.content_type,
            parsed_payload=_document_to_cache(document),
        )
        return document, result.status_code, total_response_bytes, result.used_browser

    def _candidate_pool(self, document: ParsedDocument) -> list[ProductCandidate]:
        seen: set[tuple[str, str | None, float | None]] = set()
        out: list[ProductCandidate] = []
        for candidate in [*document.candidates, *document.links]:
            key = (str(candidate.title or "").strip().lower(), candidate.url, candidate.price)
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
        return out

    def _apply_candidate(
        self,
        payload: ScrapeGroupPayload,
        target,
        candidate: ProductCandidate,
        score: float,
        text_for_installments: str,
    ) -> tuple[int, int]:
        if candidate.price is None:
            return 0, 0

        cash_result = CashOfferExtract(
            currency=candidate.currency or self.settings.default_currency,
            cash_price=candidate.price,
            old_price=candidate.old_price,
            shipping_cost=candidate.shipping_cost,
            free_shipping=candidate.free_shipping,
            availability=candidate.availability,
            seller_name=candidate.seller_name,
            source_url=candidate.url or payload.source_url,
            source_method=candidate.source_method,
            product_title=candidate.title,
            image_url=candidate.image_url,
            match_score=score,
            raw=candidate.raw,
        )
        if cash_result.currency.upper() != self.settings.default_currency.upper():
            repository.mark_mapping_failure(
                target,
                run_id=payload.run_id,
                error_code="currency_not_rankable",
                error_message=(
                    f"Observed {cash_result.currency}; this launch ranks "
                    f"{self.settings.default_currency} offers only"
                ),
            )
            return 0, 0
        repository.upsert_cash_offer(
            target,
            cash_result,
            run_id=payload.run_id,
            connector_version=payload.connector_version,
        )

        plans = extract_installment_plans(
            text_for_installments,
            source_url=cash_result.source_url,
            cash_price=cash_result.cash_price,
            provider_aliases=(self._connector_config or {}).get("providerAliases"),
        )
        installment_updates = repository.upsert_installment_plans(
            target,
            plans,
            run_id=payload.run_id,
            connector_version=payload.connector_version,
        )
        return 1, installment_updates

    def _follow_discovered(
        self,
        payload: ScrapeGroupPayload,
        target,
        candidate: ProductCandidate,
        score: float,
    ) -> tuple[int, int, int, int, str | None]:
        if not candidate.url:
            return 0, 0, 0, 0, "discovered_url_missing"
        try:
            document, status, response_bytes, _ = self._load_document(
                store_id=payload.store_id,
                source_url=candidate.url,
                allowed_hosts=payload.allowed_hosts,
                respect_robots=payload.respect_robots,
                browser_required=payload.browser_required,
                requests_per_minute=payload.requests_per_minute,
            )
        except FetchError as exc:
            repository.mark_mapping_failure(
                target,
                run_id=payload.run_id,
                error_code=exc.code,
                error_message=f"Discovered URL could not be fetched: {exc}",
            )
            return 0, 0, 1, 0, exc.code

        best, direct_score = choose_best_candidate(target, self._candidate_pool(document))
        if not best or direct_score < self.settings.min_direct_match_score:
            repository.mark_mapping_failure(
                target,
                run_id=payload.run_id,
                error_code="direct_product_mismatch",
                error_message=f"Discovered product page match score was {direct_score:.1f}",
            )
            return 0, 0, 1, response_bytes, "direct_product_mismatch"

        cash, installments = self._apply_candidate(
            payload,
            target,
            best,
            direct_score,
            document.visible_text,
        )
        if cash:
            repository.update_mapping_direct_url(
                target.mapping_id,
                candidate.url,
                candidate.title,
                direct_score,
                prefer_for_scrape=True,
            )
        failure_code = None
        if not cash:
            if best.price is None:
                failure_code = "price_not_found"
            elif (
                best.currency or self.settings.default_currency
            ).upper() != self.settings.default_currency.upper():
                failure_code = "currency_not_rankable"
            else:
                failure_code = "price_not_found"
        return cash, installments, 1, response_bytes, failure_code

    def process(
        self,
        payload: ScrapeGroupPayload,
        *,
        cloud_tasks_retry_count: int = 0,
    ) -> dict[str, Any]:
        concurrency = max(1, int(payload.max_concurrency or 1))
        lock_slot = int(hashlib.sha1(payload.task_id.encode("utf-8")).hexdigest(), 16) % concurrency
        # Claim only after the store slot becomes available. A queued request that
        # waits for another URL therefore does not look like a crashed running task.
        with repository.store_advisory_lock(payload.store_id, lock_slot):
            claim = repository.start_task(
                payload.task_id,
                allow_reclaim_running=cloud_tasks_retry_count > 0,
            )
            if claim == "terminal":
                return {"task_id": payload.task_id, "status": "already_completed"}
            if claim == "running":
                return {"task_id": payload.task_id, "status": "already_running"}
            if claim == "missing":
                return {"task_id": payload.task_id, "status": "missing_task"}
            return self._process_locked(payload)

    def _process_locked(self, payload: ScrapeGroupPayload) -> dict[str, Any]:
        targets = payload.mappings or repository.load_mapping_targets(payload.mapping_ids)
        self._connector_config = payload.connector_config or {}
        cash_updates = 0
        installment_updates = 0
        discovered_urls = 0
        total_bytes = 0
        http_status: int | None = None
        used_browser = False
        failures = 0
        failure_reasons: Counter[str] = Counter()

        try:
            document, http_status, response_bytes, used_browser = self._load_document(
                store_id=payload.store_id,
                source_url=payload.source_url,
                allowed_hosts=payload.allowed_hosts,
                respect_robots=payload.respect_robots,
                browser_required=payload.browser_required,
                requests_per_minute=payload.requests_per_minute,
            )
            total_bytes += response_bytes
            pool = self._candidate_pool(document)
            direct_source = _is_direct(payload.url_type)

            followed = 0
            for target in targets:
                best, score = choose_best_candidate(target, pool)
                threshold = (
                    self.settings.min_direct_match_score
                    if direct_source
                    else self.settings.min_listing_match_score
                )

                if not best or score < threshold:
                    failures += 1
                    failure_reasons["product_match_failed"] += 1
                    repository.mark_mapping_failure(
                        target,
                        run_id=payload.run_id,
                        error_code="product_match_failed",
                        error_message=f"No candidate met threshold {threshold}; best score={score:.1f}",
                    )
                    continue

                if (
                    best.url
                    and best.price is not None
                    and not direct_source
                    and score >= self.settings.min_listing_match_score
                ):
                    repository.update_mapping_direct_url(
                        target.mapping_id,
                        best.url,
                        best.title,
                        score,
                        prefer_for_scrape=False,
                    )
                    discovered_urls += 1

                text = document.visible_text if direct_source else best.text
                try:
                    cash, installments = self._apply_candidate(payload, target, best, score, text)
                except repository.PriceAnomalyError as exc:
                    failures += 1
                    failure_reasons["price_anomaly"] += 1
                    repository.mark_mapping_failure(
                        target,
                        run_id=payload.run_id,
                        error_code="price_anomaly",
                        error_message=str(exc),
                    )
                    continue
                cash_updates += cash
                installment_updates += installments

                if (
                    cash == 0
                    and not direct_source
                    and best.url
                    and self.settings.follow_discovered_product_urls
                    and followed < self.settings.max_discovered_fetches_per_task
                ):
                    time.sleep(max(0.0, 60.0 / max(payload.requests_per_minute, 1)))
                    try:
                        (
                            follow_cash,
                            follow_installments,
                            follow_discovered,
                            follow_bytes,
                            follow_failure,
                        ) = self._follow_discovered(payload, target, best, score)
                    except repository.PriceAnomalyError as exc:
                        repository.mark_mapping_failure(
                            target,
                            run_id=payload.run_id,
                            error_code="price_anomaly",
                            error_message=str(exc),
                        )
                        follow_cash, follow_installments, follow_discovered, follow_bytes = 0, 0, 1, 0
                        follow_failure = "price_anomaly"
                    followed += 1
                    cash_updates += follow_cash
                    installment_updates += follow_installments
                    discovered_urls += follow_discovered
                    total_bytes += follow_bytes
                    if follow_cash == 0:
                        failures += 1
                        failure_reasons[follow_failure or "price_not_found"] += 1
                elif cash == 0:
                    failures += 1
                    if best.price is None:
                        failure_code = "price_not_found"
                    elif (
                        best.currency or self.settings.default_currency
                    ).upper() != self.settings.default_currency.upper():
                        failure_code = "currency_not_rankable"
                    else:
                        failure_code = "price_not_found"
                    failure_reasons[failure_code] += 1
                    if failure_code != "currency_not_rankable":
                        repository.mark_mapping_failure(
                            target,
                            run_id=payload.run_id,
                            error_code=failure_code,
                            error_message=(
                                f"Matched product '{best.title}' but no reliable "
                                "price was extracted."
                            ),
                        )

            status = "success" if targets and failures < len(targets) else "failed"
            all_mappings_failed = not targets or failures >= len(targets)
            error_code = _source_url_error(
                failure_reasons,
                all_mappings_failed=all_mappings_failed,
            )
            error_message = _failure_summary(failure_reasons)
            repository.finish_task(
                payload.task_id,
                status=status,
                http_status=http_status,
                response_bytes=total_bytes,
                cash_updates=cash_updates,
                installment_updates=installment_updates,
                discovered_urls=discovered_urls,
                error_code=error_code,
                error_message=error_message,
                metrics={
                    "candidate_count": len(document.candidates),
                    "link_count": len(document.links),
                    "used_browser": used_browser,
                    "failed_mappings": failures,
                    "failure_reasons": dict(sorted(failure_reasons.items())),
                },
            )
            return {
                "task_id": payload.task_id,
                "status": status,
                "cash_updates": cash_updates,
                "installment_updates": installment_updates,
                "discovered_urls": discovered_urls,
                "failed_mappings": failures,
            }

        except FetchError as exc:
            for target in targets:
                repository.mark_mapping_failure(
                    target,
                    run_id=payload.run_id,
                    error_code=exc.code,
                    error_message=str(exc),
                )
            task_status = "retryable_failed" if exc.code in RETRYABLE_FETCH_CODES else "failed"
            retry_after_seconds = (
                self._open_store_circuit(payload.store_id, exc)
                if task_status == "retryable_failed"
                else None
            )
            repository.finish_task(
                payload.task_id,
                status=task_status,
                http_status=exc.status_code,
                response_bytes=total_bytes,
                error_code=exc.code,
                error_message=str(exc),
                metrics={
                    "used_browser": used_browser,
                    "retry_after_seconds": retry_after_seconds,
                },
            )
            logger.warning(
                "Store fetch failed",
                extra={
                    "task_id": payload.task_id,
                    "store_id": payload.store_id,
                    "error_code": exc.code,
                },
            )
            result = {
                "task_id": payload.task_id,
                "status": task_status,
                "error": exc.code,
            }
            if retry_after_seconds is not None:
                result["retry_after_seconds"] = retry_after_seconds
            return result

        except Exception as exc:
            error_code = _internal_error_code(exc)
            failure_location = _internal_failure_location(exc)
            logger.exception(
                "Unhandled scrape task failure",
                extra={
                    "task_id": payload.task_id,
                    "error_code": error_code,
                    "failure_location": failure_location,
                },
            )
            for target in targets:
                try:
                    repository.mark_mapping_failure(
                        target,
                        run_id=payload.run_id,
                        error_code=error_code,
                        error_message=str(exc),
                    )
                except Exception:
                    logger.exception("Could not record mapping failure")
            repository.finish_task(
                payload.task_id,
                status="failed",
                http_status=http_status,
                response_bytes=total_bytes,
                error_code=error_code,
                error_message=str(exc),
                metrics={"failure_location": failure_location},
            )
            return {"task_id": payload.task_id, "status": "failed", "error": error_code}
