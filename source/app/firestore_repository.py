from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.schedule import next_refresh_at
from app.schemas import (
    CashOfferExtract,
    CatalogDiscoveryTaskPayload,
    InstallmentPlanExtract,
    MappingTarget,
    ScrapeGroupPayload,
)
from app.scraping.normalization import normalize_text, normalize_url
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

MONEY_SCALE = Decimal("100")
CASH_MONEY_FIELDS = {
    "cash_price",
    "old_price",
    "discount_amount",
    "shipping_cost",
    "total_price",
}
PLAN_MONEY_FIELDS = {
    "periodic_payment",
    "first_payment",
    "down_payment",
    "admin_fees",
    "processing_fees",
    "insurance_fees",
    "other_fees",
    "total_published",
    "total_calculated",
    "cash_price_at_observation",
    "financing_cost",
    "minimum_purchase",
    "maximum_financing",
}
TERMINAL_TASK_STATUSES = {"success", "failed"}


class PriceAnomalyError(ValueError):
    """Raised when a new price is implausibly far from the last successful price."""


def amount_to_minor(value: Any) -> int | None:
    """Convert an EGP amount to integer piastres without binary-float drift."""

    if value is None or value == "":
        return None
    return int((Decimal(str(value)) * MONEY_SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def minor_to_amount(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(Decimal(int(value)) / MONEY_SCALE)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return None


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _safe_doc_id(value: str) -> str:
    value = str(value)
    if value and "/" not in value and len(value.encode("utf-8")) <= 900:
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _bounded_payload(value: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    """Keep parsed page cache documents safely below Firestore's 1 MiB limit."""

    candidate = dict(value)
    safe_candidates = [
        _cache_candidate_payload(item)
        for item in list(value.get("candidates") or [])
        if isinstance(item, dict)
    ]
    safe_links = [
        _cache_candidate_payload(item) for item in list(value.get("links") or []) if isinstance(item, dict)
    ]
    candidate["candidates"] = safe_candidates
    candidate["links"] = safe_links
    candidate["raw_summary"] = _bounded_raw_payload(
        value.get("raw_summary") or {},
        max_bytes=20_000,
    )
    if len(json.dumps(_jsonable(candidate), ensure_ascii=False).encode("utf-8")) <= max_bytes:
        return candidate

    candidate = {
        "final_url": str(value.get("final_url") or "")[:4_000],
        "title": str(value.get("title") or "")[:10_000],
        "visible_text": str(value.get("visible_text") or "")[:180_000],
        "candidates": safe_candidates[:400],
        "links": safe_links[:800],
        "cache_truncated": True,
        "raw_summary": {
            "cache_truncated": True,
        },
    }
    while (
        len(json.dumps(_jsonable(candidate), ensure_ascii=False).encode("utf-8")) > max_bytes
        and candidate["links"]
    ):
        candidate["links"] = candidate["links"][: len(candidate["links"]) // 2]
    while (
        len(json.dumps(_jsonable(candidate), ensure_ascii=False).encode("utf-8")) > max_bytes
        and candidate["candidates"]
    ):
        candidate["candidates"] = candidate["candidates"][: len(candidate["candidates"]) // 2]
    if len(json.dumps(_jsonable(candidate), ensure_ascii=False).encode("utf-8")) > max_bytes:
        candidate = {
            "final_url": str(value.get("final_url") or "")[:2_000],
            "title": str(value.get("title") or "")[:4_000],
            "visible_text": str(value.get("visible_text") or "")[:40_000],
            "candidates": [],
            "links": [],
            "cache_truncated": True,
            "raw_summary": {"cache_truncated": True},
        }
    if len(json.dumps(_jsonable(candidate), ensure_ascii=False).encode("utf-8")) > max_bytes:
        candidate = {
            "cache_truncated": True,
            "raw_summary": {
                "cache_truncated": True,
                "payload_sha256": _fingerprint(value),
            },
        }
    return candidate


def _bounded_raw_payload(value: Any, max_bytes: int = 100_000) -> dict[str, Any]:
    """Bound untrusted extractor evidence before placing it in Firestore.

    The structured offer fields remain the source of truth. Raw evidence is
    always serialized into one string inside a shallow map. This protects both
    Firestore's document-size limit and its maximum nested-map depth.
    """

    normalized = _jsonable(value or {})
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    content_limit = max(0, max_bytes - 512)
    truncated = len(encoded) > content_limit
    content = encoded[: min(content_limit, 50_000) if truncated else content_limit]
    payload = {
        "raw_truncated": truncated,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "original_bytes": len(encoded),
        "json_preview" if truncated else "json": content.decode("utf-8", errors="replace"),
    }
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > max_bytes:
        payload.pop("json", None)
        payload.pop("json_preview", None)
        payload["raw_truncated"] = True
    return payload


def _bounded_text(value: str | None, max_bytes: int = 4_000) -> str | None:
    """Truncate untrusted text on a UTF-8 byte boundary."""

    if value is None:
        return None
    encoded = str(value).encode("utf-8")
    if len(encoded) <= max_bytes:
        return str(value)
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _bounded_string_fields(values: dict[str, Any]) -> dict[str, Any]:
    """Bound top-level external strings before normalized Firestore writes."""

    return {
        key: (
            _bounded_text(value, 8_000 if "url" in key.lower() else 4_000)
            if isinstance(value, str)
            else value
        )
        for key, value in values.items()
    }


def _cache_candidate_payload(value: dict[str, Any]) -> dict[str, Any]:
    """Keep candidate fields useful while flattening untrusted nested evidence."""

    candidate = dict(value)
    candidate["raw"] = _bounded_raw_payload(
        candidate.get("raw") or {},
        max_bytes=20_000,
    )
    return candidate


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _plan_identity(target: MappingTarget, plan: InstallmentPlanExtract) -> tuple[str, str]:
    provider = normalize_text(
        _bounded_text(plan.provider_name or plan.bank_or_card or "unknown", 500) or "unknown"
    )
    name = normalize_text(_bounded_text(plan.plan_name, 1_000) or "")
    source_signature = ""
    if provider == "unknown" and not name:
        source_text = normalize_text(
            _bounded_text(
                str((plan.raw or {}).get("source_segment") or ""),
                1_000,
            )
            or ""
        )
        source_signature = re.sub(r"\d+(?:[.,]\d+)?", "#", source_text)[:180]
    identity = "|".join(
        [
            _bounded_text(target.offer_key, 1_000) or "",
            provider,
            normalize_text(_bounded_text(plan.bank_or_card, 500)),
            str(plan.months or 0),
            normalize_text(_bounded_text(plan.payment_frequency, 100)),
            name,
            source_signature,
            "start" if plan.starting_from_only else "fixed",
        ]
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:18].upper()
    return f"INST-{digest}", identity


class FirestoreRepository:
    """Firestore Standard implementation of the price-engine repository contract.

    The public API reads one materialized comparison document per product.  The
    normalized collections remain the source of truth for workers and audits.
    Transactions protect run/task claims, price upserts, and distributed store
    leases.  Monetary values are persisted as integer piastres.
    """

    PriceAnomalyError = PriceAnomalyError

    def __init__(self, client: Any | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if client is None:
            from google.cloud import firestore

            project = self.settings.firestore_project_id or self.settings.gcp_project_id or None
            kwargs: dict[str, Any] = {"database": self.settings.firestore_database}
            if project:
                kwargs["project"] = project
            client = firestore.Client(**kwargs)
        self.client = client
        self._catalog_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._catalog_lock = threading.Lock()
        self._summary_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _collection_name(self, name: str) -> str:
        return f"{self.settings.firestore_collection_prefix}{name}"

    def _col(self, name: str):
        return self.client.collection(self._collection_name(name))

    def _run_transaction(self, callback: Callable[[Any], Any]) -> Any:
        if hasattr(self.client, "run_transaction"):
            return self.client.run_transaction(callback)

        from google.cloud import firestore

        transaction = self.client.transaction()

        @firestore.transactional
        def runner(tx):
            return callback(tx)

        return runner(transaction)

    @staticmethod
    def _snapshot(snapshot: Any) -> dict[str, Any] | None:
        if snapshot is None or not snapshot.exists:
            return None
        value = snapshot.to_dict() or {}
        value.setdefault("_document_id", snapshot.id)
        return value

    def _where(self, collection: Any, field: str, operator: str, value: Any):
        if getattr(self.client, "is_fake_firestore", False):
            return collection.where(field, operator, value)
        from google.cloud.firestore_v1.base_query import FieldFilter

        return collection.where(filter=FieldFilter(field, operator, value))

    def _stream_where(
        self, collection_name: str, field: str, operator: str, value: Any
    ) -> Iterator[dict[str, Any]]:
        query = self._where(self._col(collection_name), field, operator, value)
        for snapshot in query.stream():
            row = self._snapshot(snapshot)
            if row is not None:
                yield row

    def _get_many(self, collection_name: str, ids: Iterable[str]) -> list[dict[str, Any]]:
        refs = [self._col(collection_name).document(_safe_doc_id(value)) for value in ids]
        if not refs:
            return []
        return [
            row for snapshot in self.client.get_all(refs) if (row := self._snapshot(snapshot)) is not None
        ]

    def healthcheck(self) -> bool:
        try:
            self._col("system").document("stats").get()
            return True
        except Exception:
            logger.exception("Firestore readiness check failed")
            return False

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()

    @contextmanager
    def store_advisory_lock(self, store_id: str, slot: int = 0):
        lock_ref = self._col("store_locks").document(_safe_doc_id(f"{store_id}:{max(slot, 0)}"))
        owner = uuid.uuid4().hex
        deadline = time.monotonic() + max(self.settings.firestore_lock_wait_seconds, 1)
        lease_seconds = max(self.settings.task_dispatch_deadline_seconds + 60, 120)

        while True:
            now = _utcnow()

            def acquire(tx, *, now=now):
                current = self._snapshot(lock_ref.get(transaction=tx))
                expires_at = _as_utc((current or {}).get("expires_at"))
                if current and expires_at and expires_at > now:
                    return False
                tx.set(
                    lock_ref,
                    {
                        "store_id": store_id,
                        "slot": max(slot, 0),
                        "owner": owner,
                        "acquired_at": now,
                        "expires_at": now + timedelta(seconds=lease_seconds),
                    },
                )
                return True

            if self._run_transaction(acquire):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for Firestore store lock {store_id}:{slot}")
            time.sleep(0.5)

        try:
            yield
        finally:

            def release(tx):
                current = self._snapshot(lock_ref.get(transaction=tx))
                if current and current.get("owner") == owner:
                    tx.delete(lock_ref)

            self._run_transaction(release)

    def reserve_store_request_slot(
        self,
        store_id: str,
        requests_per_minute: int,
        *,
        max_wait_seconds: float | None = None,
    ) -> float:
        ref = self._col("store_rate_limits").document(_safe_doc_id(store_id))
        spacing = 60.0 / max(int(requests_per_minute or 1), 1)
        now = _utcnow()

        def reserve(tx):
            current = self._snapshot(ref.get(transaction=tx)) or {}
            next_allowed = _as_utc(current.get("next_allowed_at")) or now
            slot = max(now, next_allowed)
            wait_seconds = max((slot - now).total_seconds(), 0.0)
            if max_wait_seconds is not None and wait_seconds > max_wait_seconds:
                return wait_seconds
            tx.set(
                ref,
                {
                    "store_id": store_id,
                    "next_allowed_at": slot + timedelta(seconds=spacing),
                    "updated_at": now,
                },
            )
            return wait_seconds

        return float(self._run_transaction(reserve))

    def defer_store_requests(self, store_id: str, delay_seconds: int) -> None:
        """Open or extend a durable per-store circuit using the rate-limit row."""

        ref = self._col("store_rate_limits").document(_safe_doc_id(store_id))
        now = _utcnow()
        blocked_until = now + timedelta(seconds=max(1, int(delay_seconds)))

        def defer(tx):
            current = self._snapshot(ref.get(transaction=tx)) or {}
            next_allowed = _as_utc(current.get("next_allowed_at")) or now
            tx.set(
                ref,
                {
                    "store_id": store_id,
                    "next_allowed_at": max(next_allowed, blocked_until),
                    "updated_at": now,
                    "circuit_open": True,
                },
            )

        self._run_transaction(defer)

    def reconcile_stale_runs(self, stale_after_minutes: int) -> int:
        cutoff = _utcnow() - timedelta(minutes=max(int(stale_after_minutes), 30))
        stale_ids: list[str] = []
        for row in self._stream_where("scrape_task_runs", "terminal", "==", False):
            started = _as_utc(row.get("started_at") or row.get("scheduled_for"))
            if started and started < cutoff:
                stale_ids.append(str(row.get("external_task_id") or row["_document_id"]))
        for task_id in stale_ids:
            self.finish_task(
                task_id,
                status="failed",
                error_code="stale_task_reconciled",
                error_message="Task exceeded the reconciliation age without a terminal result",
            )
        return len(stale_ids)

    def finalize_overdue_price_runs(self, max_run_age_minutes: int) -> dict[str, Any]:
        """Firestore rollback parity for the independent price-run finalizer."""

        deadline_minutes = max(1, int(max_run_age_minutes))
        cutoff = _utcnow() - timedelta(minutes=deadline_minutes)
        run_ids: list[str] = []
        task_ids: list[str] = []
        for status in ("created", "enqueuing", "queued", "running"):
            for run in self._stream_where("scrape_runs", "status", "==", status):
                started_at = _as_utc(run.get("started_at"))
                enqueue_complete = bool((run.get("metadata") or {}).get("enqueue_complete"))
                if not started_at or started_at > cutoff or not enqueue_complete:
                    continue
                run_id = str(run.get("run_id") or run.get("_document_id"))
                run_ids.append(run_id)
                for task in self._stream_where("scrape_task_runs", "run_id", "==", run_id):
                    if task.get("status") in TERMINAL_TASK_STATUSES:
                        continue
                    task_id = str(task.get("external_task_id") or task.get("_document_id"))
                    metrics = {
                        **(task.get("metrics") or {}),
                        "deadline_finalized": True,
                        "previous_status": task.get("status"),
                    }
                    self.finish_task(
                        task_id,
                        status="failed",
                        error_code=task.get("error_code") or "run_deadline_exceeded",
                        error_message=(
                            str(task.get("error_message") or "")
                            + "\nPrice run exceeded its maximum runtime; finalized independently"
                        ).strip(),
                        metrics=metrics,
                    )
                    task_ids.append(task_id)
        return {
            "deadline_minutes": deadline_minutes,
            "runs_finalized": len(run_ids),
            "tasks_finalized": len(task_ids),
            "run_ids": run_ids,
        }

    def repair_terminal_price_run(self, run_id: str) -> dict[str, Any] | None:
        tasks = self._stream_where("scrape_task_runs", "run_id", "==", run_id)
        if not tasks or any(task.get("status") not in TERMINAL_TASK_STATUSES for task in tasks):
            return {
                "run_id": run_id,
                "repaired": False,
                "total": len(tasks),
                "completed": sum(task.get("status") in TERMINAL_TASK_STATUSES for task in tasks),
            }
        ref = self._run_ref(run_id)

        def repair(tx):
            run = self._snapshot(ref.get(transaction=tx))
            if not run:
                return None
            failed = sum(task.get("status") == "failed" for task in tasks)
            succeeded = sum(task.get("status") == "success" for task in tasks)
            status = "completed_with_errors" if failed else "completed"
            previous = str(run.get("status") or "")
            run.update(
                {
                    "status": status,
                    "completed_at": run.get("completed_at") or _utcnow(),
                    "queued_task_count": len(tasks),
                    "completed_task_count": len(tasks),
                    "successful_task_count": succeeded,
                    "failed_task_count": failed,
                    "metadata": {
                        **(run.get("metadata") or {}),
                        "terminal_state_repaired": True,
                        "terminal_state_repaired_at": _utcnow(),
                    },
                }
            )
            run.pop("_document_id", None)
            tx.set(ref, run)
            return {
                "run_id": run_id,
                "status": status,
                "repaired": previous != status,
                "total": len(tasks),
                "completed": len(tasks),
            }

        return self._run_transaction(repair)

    def create_or_get_run(self, run_slot: datetime, trigger_source: str) -> tuple[dict[str, Any], bool]:
        slot = _as_utc(run_slot) or run_slot
        doc_id = slot.strftime("%Y%m%dT%H%M%SZ")
        ref = self._col("scrape_runs").document(doc_id)
        now = _utcnow()

        def create(tx):
            existing = self._snapshot(ref.get(transaction=tx))
            if existing:
                return existing, False
            run = {
                "run_id": f"RUN-{doc_id}",
                "run_slot": slot,
                "trigger_source": trigger_source,
                "status": "created",
                "started_at": now,
                "completed_at": None,
                "mapping_count": 0,
                "url_group_count": 0,
                "queued_task_count": 0,
                "completed_task_count": 0,
                "successful_task_count": 0,
                "failed_task_count": 0,
                "cash_updates": 0,
                "installment_updates": 0,
                "discovered_urls": 0,
                "metadata": {},
                "expires_at": now + timedelta(days=self.settings.firestore_run_retention_days),
            }
            tx.set(ref, run)
            return run, True

        return self._run_transaction(create)

    def load_active_mapping_rows(self) -> list[dict[str, Any]]:
        rows = []
        for row in self._stream_where("mappings", "active", "==", True):
            if not row.get("connector_enabled", True) or not row.get("store_active", True):
                continue
            effective = (
                row.get("direct_product_url")
                if (row.get("metadata") or {}).get("prefer_direct_scrape") and row.get("direct_product_url")
                else row.get("source_url")
            )
            if not effective:
                continue
            item = dict(row)
            item["effective_source_url"] = effective
            item["effective_url_type"] = (
                "رابط منتج مباشر مكتشف" if effective == row.get("direct_product_url") else row.get("url_type")
            )
            rows.append(item)
        rows.sort(
            key=lambda row: (
                str(row.get("priority") or "ZZ"),
                str(row.get("store_id") or ""),
                str(row.get("effective_source_url") or ""),
            )
        )
        return rows

    def load_failed_mapping_rows(self, source_run_id: str) -> list[dict[str, Any]]:
        failed_urls = {
            (str(task.get("store_id")), str(task.get("source_url")))
            for task in self._stream_where("scrape_task_runs", "run_id", "==", source_run_id)
            if task.get("status") == "failed" and task.get("source_url")
        }
        selected = []
        for row in self.load_active_mapping_rows():
            store_id = str(row.get("store_id"))
            candidate_urls = {
                str(value)
                for value in (
                    row.get("effective_source_url"),
                    row.get("source_url"),
                    row.get("direct_product_url"),
                )
                if value
            }
            if any((store_id, url) in failed_urls for url in candidate_urls):
                selected.append(row)
        return selected

    def load_mapping_targets(self, mapping_ids: list[str]) -> list[MappingTarget]:
        if not mapping_ids:
            return []
        rows_by_id = {
            str(row.get("mapping_id") or row["_document_id"]): row
            for row in self._get_many("mappings", mapping_ids)
            if row.get("active", True)
        }
        targets = []
        for mapping_id in mapping_ids:
            row = rows_by_id.get(mapping_id)
            if not row:
                continue
            prefer_direct = (row.get("metadata") or {}).get("prefer_direct_scrape")
            source_url = (
                row.get("direct_product_url")
                if prefer_direct and row.get("direct_product_url")
                else row.get("source_url")
            )
            targets.append(
                MappingTarget(
                    mapping_id=mapping_id,
                    offer_id=row["offer_id"],
                    offer_key=row["offer_key"],
                    variant_id=row["variant_id"],
                    store_id=row["store_id"],
                    store_name=row.get("store_name"),
                    store_base_url=row.get("store_base_url"),
                    seller_id=row.get("seller_id"),
                    seller_name=row.get("seller_name"),
                    store_sku=row.get("store_sku"),
                    source_url=source_url,
                    url_type=(
                        "رابط منتج مباشر مكتشف"
                        if source_url == row.get("direct_product_url")
                        else row.get("url_type")
                    ),
                    title_as_seen=row.get("title_as_seen"),
                    match_method=row.get("match_method"),
                    match_confidence=row.get("match_confidence"),
                    extraction_hint=row.get("extraction_hint"),
                    canonical_name=row["canonical_name"],
                    section=row.get("section"),
                    product_type=row.get("product_type"),
                    brand=row.get("brand"),
                    model=row.get("model"),
                    variant_name=row.get("variant_name"),
                    ram_gb=_number(row.get("ram_gb")),
                    storage_gb=_number(row.get("storage_gb")),
                    color=row.get("color"),
                    manufacturer_sku=row.get("manufacturer_sku"),
                    gtin=row.get("gtin"),
                )
            )
        return targets

    def _run_ref(self, run_id: str):
        doc_id = run_id.removeprefix("RUN-")
        return self._col("scrape_runs").document(_safe_doc_id(doc_id))

    def mark_run_enqueuing(
        self,
        run_id: str,
        *,
        mapping_count: int,
        url_group_count: int,
        queued_task_count: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        ref = self._run_ref(run_id)

        def update(tx):
            run = self._snapshot(ref.get(transaction=tx))
            if not run:
                raise RuntimeError(f"Run {run_id} does not exist")
            merged = {**(run.get("metadata") or {}), **(metadata or {})}
            merged["enqueue_complete"] = False
            run.update(
                {
                    "status": "completed" if queued_task_count == 0 else "enqueuing",
                    "completed_at": _utcnow() if queued_task_count == 0 else None,
                    "mapping_count": mapping_count,
                    "url_group_count": url_group_count,
                    "queued_task_count": queued_task_count,
                    "metadata": merged,
                }
            )
            run.pop("_document_id", None)
            tx.set(ref, run)

        self._run_transaction(update)

    def mark_run_enqueue_complete(self, run_id: str) -> None:
        ref = self._run_ref(run_id)

        def complete(tx):
            run = self._snapshot(ref.get(transaction=tx))
            if not run:
                return
            queued = int(run.get("queued_task_count") or 0)
            completed = int(run.get("completed_task_count") or 0)
            failed = int(run.get("failed_task_count") or 0)
            if queued == 0 or completed >= queued:
                status = "completed_with_errors" if failed else "completed"
                completed_at = run.get("completed_at") or _utcnow()
            elif completed:
                status = "running"
                completed_at = None
            else:
                status = "queued"
                completed_at = None
            run["status"] = status
            run["completed_at"] = completed_at
            run["metadata"] = {**(run.get("metadata") or {}), "enqueue_complete": True}
            run.pop("_document_id", None)
            tx.set(ref, run)

        self._run_transaction(complete)

    def mark_run_enqueue_failed(
        self,
        run_id: str,
        message: str,
        *,
        successfully_queued: int = 0,
        planned_tasks: int | None = None,
    ) -> None:
        ref = self._run_ref(run_id)

        def fail(tx):
            run = self._snapshot(ref.get(transaction=tx))
            if not run:
                return
            if run.get("completed_at") or run.get("status") not in {
                "created",
                "enqueuing",
                "queued",
                "running",
            }:
                return
            metadata = {
                **(run.get("metadata") or {}),
                "enqueue_error": message,
                "successfully_queued": successfully_queued,
                "enqueue_complete": False,
            }
            if planned_tasks is not None:
                metadata["planned_tasks"] = planned_tasks
            run.update(
                {
                    "status": "enqueue_failed",
                    "completed_at": _utcnow(),
                    "metadata": metadata,
                }
            )
            run.pop("_document_id", None)
            tx.set(ref, run)

        self._run_transaction(fail)

    def register_task_run(self, payload: ScrapeGroupPayload) -> None:
        ref = self._col("scrape_task_runs").document(_safe_doc_id(payload.task_id))
        now = _utcnow()

        def register(tx):
            if self._snapshot(ref.get(transaction=tx)):
                return
            tx.set(
                ref,
                {
                    "external_task_id": payload.task_id,
                    "run_id": payload.run_id,
                    "store_id": payload.store_id,
                    "source_url": payload.source_url,
                    "url_type": payload.url_type,
                    "mapping_count": len(payload.mapping_ids or payload.mappings),
                    "scheduled_for": payload.scheduled_for,
                    "status": "queued",
                    "terminal": False,
                    "attempt": 1,
                    "started_at": None,
                    "completed_at": None,
                    "created_at": now,
                    "expires_at": now + timedelta(days=self.settings.firestore_task_retention_days),
                },
            )

        self._run_transaction(register)

    def load_registered_task_identities(self, run_id: str) -> dict[str, dict[str, Any]]:
        rows = self._stream_where("scrape_task_runs", "run_id", "==", run_id)
        return {
            str(row.get("external_task_id") or row.get("_document_id")): {
                "source_url": row.get("source_url"),
                "url_type": row.get("url_type"),
            }
            for row in rows
            if row.get("external_task_id") or row.get("_document_id")
        }

    def count_registered_tasks(self, run_id: str) -> int:
        return sum(1 for _row in self._stream_where("scrape_task_runs", "run_id", "==", run_id))

    def start_task(self, task_id: str, *, allow_reclaim_running: bool = False) -> str:
        task_ref = self._col("scrape_task_runs").document(_safe_doc_id(task_id))

        def claim(tx):
            task = self._snapshot(task_ref.get(transaction=tx))
            if not task:
                return "missing"
            status = str(task.get("status") or "")
            if status in TERMINAL_TASK_STATUSES:
                return "terminal"
            if status == "running" and not allow_reclaim_running:
                return "running"
            run_ref = self._run_ref(str(task["run_id"]))
            run = self._snapshot(run_ref.get(transaction=tx))
            task["attempt"] = int(task.get("attempt") or 1) + (
                1 if status in {"retryable_failed", "running"} else 0
            )
            task.update(
                {
                    "status": "running",
                    "terminal": False,
                    "started_at": _utcnow(),
                    "completed_at": None,
                }
            )
            task.pop("_document_id", None)
            tx.set(task_ref, task)

            if run and run.get("status") in {
                "queued",
                "enqueuing",
                "enqueue_failed",
            }:
                run["status"] = "running"
                run.pop("_document_id", None)
                tx.set(run_ref, run)
            return "claimed"

        return str(self._run_transaction(claim))

    def finish_task(
        self,
        task_id: str,
        *,
        status: str,
        http_status: int | None = None,
        response_bytes: int = 0,
        cash_updates: int = 0,
        installment_updates: int = 0,
        discovered_urls: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        task_ref = self._col("scrape_task_runs").document(_safe_doc_id(task_id))
        now = _utcnow()

        def finish(tx):
            task = self._snapshot(task_ref.get(transaction=tx))
            if not task:
                return False
            was_terminal = bool(task.get("terminal")) or task.get("status") in TERMINAL_TASK_STATUSES
            if was_terminal:
                return False
            is_terminal = status in TERMINAL_TASK_STATUSES
            run_id = str(task["run_id"])
            run_ref = self._run_ref(run_id)
            run = self._snapshot(run_ref.get(transaction=tx)) if is_terminal else None
            task.update(
                {
                    "status": status,
                    "terminal": is_terminal,
                    "completed_at": now if is_terminal else None,
                    "http_status": http_status,
                    "response_bytes": response_bytes,
                    "cash_updates": cash_updates,
                    "installment_updates": installment_updates,
                    "discovered_urls": discovered_urls,
                    "error_code": error_code,
                    "error_message": error_message[:2000] if error_message else None,
                    "metrics": _jsonable(metrics or {}),
                }
            )
            task.pop("_document_id", None)
            tx.set(task_ref, task)
            if not is_terminal:
                return False

            if not run:
                return False
            success = status == "success"
            run["completed_task_count"] = int(run.get("completed_task_count") or 0) + 1
            run["successful_task_count"] = int(run.get("successful_task_count") or 0) + (1 if success else 0)
            run["failed_task_count"] = int(run.get("failed_task_count") or 0) + (0 if success else 1)
            run["cash_updates"] = int(run.get("cash_updates") or 0) + cash_updates
            run["installment_updates"] = int(run.get("installment_updates") or 0) + installment_updates
            run["discovered_urls"] = int(run.get("discovered_urls") or 0) + discovered_urls
            completed = int(run["completed_task_count"])
            queued = int(run.get("queued_task_count") or 0)
            became_complete = queued > 0 and completed >= queued
            if became_complete:
                run["status"] = (
                    "completed_with_errors" if int(run.get("failed_task_count") or 0) else "completed"
                )
                run["completed_at"] = now
            run.pop("_document_id", None)
            tx.set(run_ref, run)
            return became_complete

        became_complete = bool(self._run_transaction(finish))
        if became_complete:
            self._rebuild_system_stats()

    def promote_retry_exhausted(self, task_id: str) -> None:
        task = self._snapshot(self._col("scrape_task_runs").document(_safe_doc_id(task_id)).get())
        if not task or task.get("status") in TERMINAL_TASK_STATUSES:
            return
        self.finish_task(
            task_id,
            status="failed",
            error_code=task.get("error_code") or "retry_exhausted",
            error_message=(
                str(task.get("error_message") or "") + "\nCloud Tasks retry budget exhausted"
            ).strip(),
        )

    def get_page_cache(self, store_id: str, source_url: str) -> dict[str, Any] | None:
        cache_id = _safe_doc_id(f"{store_id}|{normalize_url(source_url)}")
        return self._snapshot(self._col("page_cache").document(cache_id).get())

    def upsert_page_cache(
        self,
        store_id: str,
        source_url: str,
        *,
        etag: str | None,
        last_modified: str | None,
        content_hash: str | None,
        http_status: int,
        content_type: str | None,
        parsed_payload: dict[str, Any] | None,
    ) -> None:
        normalized = normalize_url(source_url)
        cache_id = _safe_doc_id(f"{store_id}|{normalized}")
        now = _utcnow()
        bounded = (
            _bounded_payload(parsed_payload, self.settings.firestore_page_cache_max_bytes)
            if parsed_payload is not None
            else None
        )
        self._col("page_cache").document(cache_id).set(
            {
                "store_id": store_id,
                "source_url": normalized,
                "etag": etag,
                "last_modified": last_modified,
                "content_hash": content_hash,
                "http_status": http_status,
                "content_type": content_type,
                "parsed_payload": bounded,
                "fetched_at": now,
                "expires_at": now + timedelta(days=self.settings.firestore_page_cache_retention_days),
            }
        )

    def update_mapping_direct_url(
        self,
        mapping_id: str,
        direct_url: str,
        title: str | None,
        score: float,
        *,
        prefer_for_scrape: bool = False,
    ) -> None:
        normalized = _bounded_text(normalize_url(direct_url), 8_000) or ""
        ref = self._col("mappings").document(_safe_doc_id(mapping_id))

        def update(tx):
            mapping = self._snapshot(ref.get(transaction=tx))
            if not mapping:
                return
            metadata = {
                **(mapping.get("metadata") or {}),
                "prefer_direct_scrape": prefer_for_scrape,
            }
            mapping.update(
                {
                    "direct_product_url": normalized,
                    "title_as_seen": (_bounded_text(title) if title else mapping.get("title_as_seen")),
                    "match_method": "automatic_discovery",
                    "match_confidence": ("عالية" if score >= 80 else "متوسطة" if score >= 55 else "منخفضة"),
                    "metadata": metadata,
                    "last_discovered_at": _utcnow(),
                    "review_status": "تلقائي" if score >= 55 else "تحتاج مراجعة",
                    "updated_at": _utcnow(),
                }
            )
            mapping.pop("_document_id", None)
            tx.set(ref, mapping)

        self._run_transaction(update)

    def _cash_price_fingerprint(self, values: dict[str, Any]) -> str | None:
        if values.get("cash_price_minor") is None:
            return None
        return _fingerprint(
            {
                "currency": values.get("currency"),
                "cash_price_minor": values.get("cash_price_minor"),
                "old_price_minor": values.get("old_price_minor"),
                "shipping_cost_minor": values.get("shipping_cost_minor"),
                "total_price_minor": values.get("total_price_minor"),
                "free_shipping": values.get("free_shipping"),
            }
        )

    def upsert_cash_offer(
        self,
        target: MappingTarget,
        result: CashOfferExtract,
        *,
        run_id: str,
        connector_version: str,
    ) -> bool:
        if result.cash_price <= 0:
            raise ValueError("Cash price must be positive")

        free_shipping = (
            result.free_shipping
            if result.free_shipping is not None
            else (result.shipping_cost == 0 if result.shipping_cost is not None else None)
        )
        total = (
            result.cash_price
            if free_shipping
            else (result.cash_price + result.shipping_cost if result.shipping_cost is not None else None)
        )
        discount = max(result.old_price - result.cash_price, 0) if result.old_price is not None else None
        discount_percent = (
            discount / result.old_price
            if discount is not None and result.old_price not in (None, 0)
            else None
        )
        now = _utcnow()
        values = {
            "offer_id": target.offer_id,
            "offer_key": target.offer_key,
            "mapping_id": target.mapping_id,
            "variant_id": target.variant_id,
            "store_id": target.store_id,
            "store_name": target.store_name,
            "store_base_url": target.store_base_url,
            "seller_id": target.seller_id,
            "seller_name": result.seller_name or target.seller_name,
            "currency": result.currency,
            "cash_price_minor": amount_to_minor(result.cash_price),
            "old_price_minor": amount_to_minor(result.old_price),
            "discount_amount_minor": amount_to_minor(discount),
            "discount_percent": discount_percent,
            "shipping_cost_minor": amount_to_minor(result.shipping_cost),
            "total_price_minor": amount_to_minor(total),
            "free_shipping": free_shipping,
            "availability": result.availability,
            "available_quantity": result.available_quantity,
            "delivery_region": self.settings.default_delivery_region,
            "delivery_text": result.delivery_text,
            "min_delivery_days": result.min_delivery_days,
            "max_delivery_days": result.max_delivery_days,
            "warranty_type": result.warranty_type,
            "warranty_provider": result.warranty_provider,
            "warranty_months": result.warranty_months,
            "source_method": result.source_method,
            "source_url": result.source_url,
            "canonical_name": target.canonical_name,
            "section": target.section,
            "product_type": target.product_type,
            "brand": target.brand,
            "model": target.model,
            "variant_name": target.variant_name,
            "ram_gb": target.ram_gb,
            "storage_gb": target.storage_gb,
            "color": target.color,
            "last_checked_at": now,
            "last_success_at": now,
            "freshness_status": "fresh",
            "extraction_status": "success",
            "consecutive_failures": 0,
            "connector_version": connector_version,
            "last_run_id": run_id,
            "active": True,
            "review_status": "تلقائي",
            "raw_payload": _bounded_raw_payload(result.raw),
            "updated_at": now,
        }
        values = _bounded_string_fields(values)
        values["price_fingerprint"] = self._cash_price_fingerprint(values)
        offer_ref = self._col("cash_offers").document(_safe_doc_id(target.offer_key))

        def upsert(tx):
            old = self._snapshot(offer_ref.get(transaction=tx)) or {}
            old_price = old.get("cash_price_minor")
            if old_price and int(old_price) > 0:
                ratio = int(values["cash_price_minor"]) / int(old_price)
                if (
                    ratio < self.settings.min_price_ratio_to_previous
                    or ratio > self.settings.max_price_ratio_to_previous
                ):
                    raise PriceAnomalyError(
                        f"New price {result.cash_price:.2f} is {ratio:.3f}x the "
                        f"previous price {minor_to_amount(old_price):.2f}; manual review required"
                    )
            changed = old.get("price_fingerprint") != values["price_fingerprint"]
            merged = {**old, **values}
            if old.get("review_status") == "مرفوض":
                merged["review_status"] = "مرفوض"
            merged.setdefault("created_at", now)
            merged.pop("_document_id", None)
            tx.set(offer_ref, merged)
            if changed:
                change_type = "first_seen" if old.get("cash_price_minor") is None else "price_changed"
                history_id = _safe_doc_id(f"{target.offer_key}|{run_id}|{values['price_fingerprint']}")
                history_ref = self._col("cash_offer_history").document(history_id)
                tx.set(
                    history_ref,
                    {
                        "history_id": history_id,
                        "offer_key": target.offer_key,
                        "variant_id": target.variant_id,
                        "store_id": target.store_id,
                        "seller_id": target.seller_id,
                        "observed_at": now,
                        "run_id": run_id,
                        "change_type": change_type,
                        "cash_price_minor": values["cash_price_minor"],
                        "old_price_minor": values["old_price_minor"],
                        "shipping_cost_minor": values["shipping_cost_minor"],
                        "total_price_minor": values["total_price_minor"],
                        "availability": result.availability,
                        "snapshot": _jsonable(
                            {
                                key: value
                                for key, value in values.items()
                                if key not in {"raw_payload", "updated_at"}
                            }
                        ),
                    },
                )
            return changed

        changed = bool(self._run_transaction(upsert))
        discovery_ref = self._col("installment_discovery").document(_safe_doc_id(target.offer_key))
        discovery_ref.set(
            {
                "cash_offer_key": target.offer_key,
                "mapping_id": target.mapping_id,
                "variant_id": target.variant_id,
                "store_id": target.store_id,
                "source_url": result.source_url,
                "last_checked_at": now,
                "updated_at": now,
            },
            merge=True,
        )
        return changed

    def mark_mapping_failure(
        self,
        target: MappingTarget,
        *,
        run_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        now = _utcnow()
        offer_ref = self._col("cash_offers").document(_safe_doc_id(target.offer_key))

        def fail(tx):
            offer = self._snapshot(offer_ref.get(transaction=tx))
            discovery_ref = self._col("installment_discovery").document(_safe_doc_id(target.offer_key))
            discovery = self._snapshot(discovery_ref.get(transaction=tx)) or {
                "cash_offer_key": target.offer_key,
                "mapping_id": target.mapping_id,
                "variant_id": target.variant_id,
                "store_id": target.store_id,
                "source_url": target.source_url,
            }
            if offer:
                notes = (str(offer.get("review_notes") or "") + "\n" + error_message).strip()[-4000:]
                offer.update(
                    {
                        "last_checked_at": now,
                        "extraction_status": error_code,
                        "consecutive_failures": int(offer.get("consecutive_failures") or 0) + 1,
                        "last_run_id": run_id,
                        "review_notes": notes,
                        "updated_at": now,
                    }
                )
                offer.pop("_document_id", None)
                tx.set(offer_ref, offer)

            discovery.update(
                {
                    "last_checked_at": now,
                    "status": error_code,
                    "consecutive_failures": int(discovery.get("consecutive_failures") or 0) + 1,
                    "notes": (str(discovery.get("notes") or "") + "\n" + error_message).strip()[-4000:],
                    "updated_at": now,
                }
            )
            discovery.pop("_document_id", None)
            tx.set(discovery_ref, discovery)

        self._run_transaction(fail)
        self._rebuild_comparison(target.variant_id)

    def _plan_financial_values(self, plan: InstallmentPlanExtract) -> dict[str, Any]:
        result = plan.model_dump(mode="python")
        for field in PLAN_MONEY_FIELDS:
            result[f"{field}_minor"] = amount_to_minor(result.pop(field, None))
        result["raw_payload"] = _bounded_raw_payload(result.pop("raw", {}))
        return _bounded_string_fields(result)

    @staticmethod
    def _plan_fingerprint(values: dict[str, Any]) -> str:
        fields = [
            "provider_name",
            "provider_type",
            "bank_or_card",
            "plan_name",
            "months",
            "payment_frequency",
            *[f"{name}_minor" for name in sorted(PLAN_MONEY_FIELDS)],
            "down_payment_percent",
            "financing_markup_percent",
            "apr",
            "interest_type",
            "interest_free",
            "grace_months",
            "starts_at",
            "ends_at",
            "starting_from_only",
            "completeness",
        ]
        return _fingerprint({field: values.get(field) for field in fields})

    def upsert_installment_plans(
        self,
        target: MappingTarget,
        plans: list[InstallmentPlanExtract],
        *,
        run_id: str,
        connector_version: str,
    ) -> int:
        now = _utcnow()
        existing = list(self._stream_where("installment_plans", "cash_offer_key", "==", target.offer_key))
        existing_by_key = {str(row.get("plan_key")): row for row in existing}
        active_keys: set[str] = set()
        changed_count = 0

        for plan in plans:
            plan_id, plan_key = _plan_identity(target, plan)
            active_keys.add(plan_key)
            values = self._plan_financial_values(plan)
            values.update(
                {
                    "plan_id": plan_id,
                    "plan_key": plan_key,
                    "cash_offer_key": target.offer_key,
                    "variant_id": target.variant_id,
                    "store_id": target.store_id,
                    "store_name": target.store_name,
                    "store_base_url": target.store_base_url,
                    "seller_id": target.seller_id,
                    "seller_name": target.seller_name,
                    "canonical_name": target.canonical_name,
                    "section": target.section,
                    "product_type": target.product_type,
                    "brand": target.brand,
                    "model": target.model,
                    "variant_name": target.variant_name,
                    "last_checked_at": now,
                    "last_success_at": now,
                    "freshness_status": "fresh",
                    "extraction_status": "success",
                    "consecutive_failures": 0,
                    "connector_version": connector_version,
                    "last_run_id": run_id,
                    "active": True,
                    "review_status": "تلقائي",
                    "updated_at": now,
                }
            )
            values = _bounded_string_fields(values)
            values["plan_fingerprint"] = self._plan_fingerprint(values)
            ref = self._col("installment_plans").document(_safe_doc_id(plan_id))

            def upsert(tx, *, values=values, ref=ref, plan_key=plan_key):
                old = self._snapshot(ref.get(transaction=tx)) or {}
                changed = old.get("plan_fingerprint") != values["plan_fingerprint"]
                merged = {**old, **values}
                merged.setdefault("created_at", now)
                merged.pop("_document_id", None)
                tx.set(ref, merged)
                if changed:
                    history_id = _safe_doc_id(f"{plan_key}|{run_id}|{values['plan_fingerprint']}")
                    tx.set(
                        self._col("installment_plan_history").document(history_id),
                        {
                            "history_id": history_id,
                            "plan_key": plan_key,
                            "cash_offer_key": target.offer_key,
                            "variant_id": target.variant_id,
                            "store_id": target.store_id,
                            "observed_at": now,
                            "run_id": run_id,
                            "change_type": ("first_seen" if not old else "plan_changed"),
                            "snapshot": _jsonable(
                                {
                                    key: value
                                    for key, value in values.items()
                                    if key not in {"raw_payload", "updated_at"}
                                }
                            ),
                        },
                    )
                return changed

            if self._run_transaction(upsert):
                changed_count += 1

        batch = self.client.batch()
        for plan_key, old in existing_by_key.items():
            if plan_key in active_keys or not old.get("active", True):
                continue
            old.update(
                {
                    "last_checked_at": now,
                    "extraction_status": "not_seen_in_latest_scan",
                    "consecutive_failures": int(old.get("consecutive_failures") or 0) + 1,
                    "active": (
                        False if (_as_utc(old.get("ends_at")) or now) < now else old.get("active", True)
                    ),
                    "updated_at": now,
                }
            )
            doc_id = str(old.pop("_document_id"))
            batch.set(self._col("installment_plans").document(doc_id), old)
        batch.commit()

        discovery_ref = self._col("installment_discovery").document(_safe_doc_id(target.offer_key))
        discovery_ref.set(
            {
                "cash_offer_key": target.offer_key,
                "mapping_id": target.mapping_id,
                "variant_id": target.variant_id,
                "store_id": target.store_id,
                "source_url": _bounded_text(target.source_url, 8_000),
                "last_checked_at": now,
                "last_success_at": now if plans else None,
                "status": "plans_found" if plans else "no_plan_extracted",
                "consecutive_failures": 0 if plans else 1,
                "updated_at": now,
            },
            merge=True,
        )
        self._rebuild_comparison(target.variant_id)
        return changed_count

    def _freshness(self, last_success_at: Any, now: datetime) -> str:
        success = _as_utc(last_success_at)
        if not success:
            return "unseen"
        minutes = (now - success).total_seconds() / 60
        if minutes <= self.settings.freshness_minutes:
            return "fresh"
        if minutes <= self.settings.stale_after_minutes:
            return "late"
        return "stale"

    def _cash_public(self, row: dict[str, Any], now: datetime) -> dict[str, Any]:
        result = {
            key: row.get(key)
            for key in (
                "offer_id",
                "offer_key",
                "variant_id",
                "store_id",
                "seller_id",
                "seller_name",
                "currency",
                "discount_percent",
                "free_shipping",
                "availability",
                "available_quantity",
                "purchase_limit",
                "delivery_region",
                "delivery_text",
                "min_delivery_days",
                "max_delivery_days",
                "warranty_type",
                "warranty_provider",
                "warranty_months",
                "store_verified",
                "seller_verified",
                "source_url",
                "last_checked_at",
                "last_success_at",
                "canonical_name",
                "section",
                "product_type",
                "brand",
                "model",
                "variant_name",
                "ram_gb",
                "storage_gb",
                "color",
                "store_name",
                "store_base_url",
            )
        }
        for field in CASH_MONEY_FIELDS:
            result[field] = minor_to_amount(row.get(f"{field}_minor"))
        freshness = self._freshness(row.get("last_success_at"), now)
        result["computed_freshness"] = freshness
        result["shipping_cost_known"] = bool(
            row.get("free_shipping")
            or row.get("shipping_cost_minor") is not None
            or row.get("total_price_minor") is not None
        )
        comparable_minor = row.get("total_price_minor")
        if comparable_minor is None and row.get("free_shipping"):
            comparable_minor = row.get("cash_price_minor")
        if comparable_minor is None and row.get("shipping_cost_minor") is not None:
            cash_minor = row.get("cash_price_minor")
            if cash_minor is not None:
                comparable_minor = int(cash_minor) + int(row["shipping_cost_minor"])
        result["comparable_total"] = minor_to_amount(comparable_minor)
        result["eligible_for_ranking"] = bool(
            row.get("last_success_at")
            and freshness in {"fresh", "late"}
            and row.get("cash_price_minor") is not None
            and str(row.get("currency") or "").upper() == self.settings.default_currency.upper()
            and row.get("extraction_status") == "success"
            and (row.get("availability") or "unknown") != "out_of_stock"
            and row.get("review_status") != "مرفوض"
            and row.get("active", True)
        )
        return _jsonable(result)

    def _installment_public(
        self, row: dict[str, Any], now: datetime, cash_by_key: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        result = {
            key: row.get(key)
            for key in (
                "plan_id",
                "plan_key",
                "variant_id",
                "store_id",
                "seller_id",
                "seller_name",
                "provider_id",
                "provider_name",
                "provider_type",
                "bank_or_card",
                "plan_name",
                "currency",
                "months",
                "payment_frequency",
                "down_payment_percent",
                "financing_markup_percent",
                "apr",
                "interest_type",
                "interest_free",
                "grace_months",
                "eligibility",
                "required_card",
                "customer_type",
                "new_customers_only",
                "geography",
                "starts_at",
                "ends_at",
                "promo_code",
                "terms_url",
                "source_url",
                "starting_from_only",
                "completeness",
                "last_checked_at",
                "last_success_at",
                "canonical_name",
                "section",
                "product_type",
                "brand",
                "model",
                "variant_name",
                "store_name",
                "store_base_url",
            )
        }
        for field in PLAN_MONEY_FIELDS:
            result[field] = minor_to_amount(row.get(f"{field}_minor"))
        normalized_minor = row.get("total_published_minor")
        if normalized_minor is None:
            normalized_minor = row.get("total_calculated_minor")
        result["normalized_total"] = minor_to_amount(normalized_minor)
        cash = cash_by_key.get(str(row.get("cash_offer_key"))) or {}
        result["currency"] = row.get("currency") or cash.get("currency")
        result["cash_availability"] = cash.get("availability")
        freshness = self._freshness(row.get("last_success_at"), now)
        result["computed_freshness"] = freshness
        starts_at = _as_utc(row.get("starts_at"))
        ends_at = _as_utc(row.get("ends_at"))
        result["eligible_for_ranking"] = bool(
            row.get("last_success_at")
            and freshness in {"fresh", "late"}
            and row.get("extraction_status") == "success"
            and str(result.get("currency") or "").upper() == self.settings.default_currency.upper()
            and (row.get("periodic_payment_minor") is not None or normalized_minor is not None)
            and (cash.get("availability") or "unknown") != "out_of_stock"
            and (starts_at is None or starts_at <= now)
            and (ends_at is None or ends_at >= now)
            and not row.get("starting_from_only", False)
            and row.get("completeness") == "complete"
            and int(row.get("months") or 0) > 0
            and bool(row.get("provider_name") or row.get("bank_or_card") or row.get("plan_name"))
            and row.get("review_status") != "مرفوض"
            and row.get("active", True)
        )
        return _jsonable(result)

    @staticmethod
    def _cash_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        freshness_order = {"fresh": 0, "late": 1, "stale": 2, "unseen": 3}
        availability_order = {
            "available": 0,
            "limited": 1,
            "preorder": 2,
            "unknown": 3,
            "out_of_stock": 4,
        }
        comparable_price = row.get("comparable_total")
        if comparable_price is None:
            comparable_price = row.get("cash_price")
        return (
            not bool(row.get("eligible_for_ranking")),
            comparable_price is None,
            comparable_price if comparable_price is not None else float("inf"),
            row.get("cash_price") is None,
            row.get("cash_price") if row.get("cash_price") is not None else float("inf"),
            freshness_order.get(str(row.get("computed_freshness")), 4),
            availability_order.get(str(row.get("availability") or "unknown"), 4),
            str(row.get("store_name") or ""),
        )

    @staticmethod
    def _installment_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        freshness_order = {"fresh": 0, "late": 1, "stale": 2, "unseen": 3}
        return (
            not bool(row.get("eligible_for_ranking")),
            freshness_order.get(str(row.get("computed_freshness")), 4),
            bool(row.get("starting_from_only")),
            row.get("normalized_total") is None,
            row.get("normalized_total") or float("inf"),
            row.get("periodic_payment") is None,
            row.get("periodic_payment") or float("inf"),
            row.get("months") is None,
            row.get("months") or 9999,
            str(row.get("store_name") or ""),
        )

    def _materialize(
        self,
        product: dict[str, Any],
        cash_docs: list[dict[str, Any]],
        installment_docs: list[dict[str, Any]],
        *,
        include_unpriced: bool,
    ) -> dict[str, Any]:
        now = _utcnow()
        cash_rows = [self._cash_public(row, now) for row in cash_docs]
        cash_by_key = {str(row.get("offer_key")): row for row in cash_docs if row.get("offer_key")}
        installment_rows = [self._installment_public(row, now, cash_by_key) for row in installment_docs]
        ranked_cash = [row for row in cash_rows if row["eligible_for_ranking"]]
        ranked_installments = [row for row in installment_rows if row["eligible_for_ranking"]]

        product_out = {
            key: value
            for key, value in product.items()
            if not key.startswith("_") and key not in {"specs_index"}
        }
        product_out.update(
            {
                "lowest_cash_price": min(
                    (row["cash_price"] for row in ranked_cash),
                    default=None,
                ),
                "lowest_delivered_total": min(
                    (
                        row["comparable_total"]
                        for row in ranked_cash
                        if row.get("comparable_total") is not None
                    ),
                    default=None,
                ),
                "cash_offer_count": len({row.get("offer_key") for row in ranked_cash}),
                "installment_plan_count": len({row.get("plan_key") for row in ranked_installments}),
                "lowest_periodic_payment": min(
                    (
                        row["periodic_payment"]
                        for row in ranked_installments
                        if row.get("periodic_payment") is not None
                    ),
                    default=None,
                ),
            }
        )
        product_out["lowest_cash_total"] = product_out["lowest_delivered_total"]
        cash_rows.sort(key=self._cash_sort_key)
        installment_rows.sort(key=self._installment_sort_key)
        if not include_unpriced:
            cash_rows = [row for row in cash_rows if row.get("cash_price") is not None]
        return {
            "product": _jsonable(product_out),
            "cash_offers": cash_rows,
            "installment_plans": installment_rows,
        }

    def _ready_payload(
        self,
        variant_id: str,
        product: dict[str, Any],
        cash_docs: list[dict[str, Any]],
        installment_docs: list[dict[str, Any]],
        observed_at: datetime,
    ) -> dict[str, Any]:
        audit_only_fields = {
            "_document_id",
            "raw_payload",
            "price_fingerprint",
            "plan_fingerprint",
            "connector_version",
            "last_run_id",
            "created_at",
            "updated_at",
        }
        ready_cash_docs = [
            {key: value for key, value in row.items() if key not in audit_only_fields} for row in cash_docs
        ]
        ready_installment_docs = [
            {key: value for key, value in row.items() if key not in audit_only_fields}
            for row in installment_docs
        ]
        product = dict(product)
        product.pop("_document_id", None)
        payload = {
            "variant_id": variant_id,
            "product": product,
            "cash_offers": ready_cash_docs,
            "installment_plans": ready_installment_docs,
            "source_observed_at": observed_at,
            "updated_at": _utcnow(),
        }
        size = len(json.dumps(_jsonable(payload), ensure_ascii=False).encode("utf-8"))
        if size > self.settings.firestore_comparison_max_bytes:
            raise RuntimeError(
                f"Comparison document {variant_id} is {size} bytes; refusing to "
                "truncate offers or exceed the configured Firestore safety limit"
            )
        return payload

    def _ready_summary_payload(
        self,
        variant_id: str,
        cash_docs: list[dict[str, Any]],
        installment_docs: list[dict[str, Any]],
        observed_at: datetime,
    ) -> dict[str, Any]:
        cash_fields = (
            "offer_key",
            "variant_id",
            "store_id",
            "currency",
            "cash_price_minor",
            "shipping_cost_minor",
            "total_price_minor",
            "free_shipping",
            "availability",
            "last_success_at",
            "extraction_status",
            "review_status",
            "active",
        )
        plan_fields = (
            "plan_key",
            "cash_offer_key",
            "variant_id",
            "store_id",
            "currency",
            "periodic_payment_minor",
            "total_published_minor",
            "total_calculated_minor",
            "last_success_at",
            "extraction_status",
            "starts_at",
            "ends_at",
            "starting_from_only",
            "completeness",
            "months",
            "provider_name",
            "bank_or_card",
            "plan_name",
            "review_status",
            "active",
        )
        return {
            "variant_id": variant_id,
            "cash_offers": [{key: row.get(key) for key in cash_fields} for row in cash_docs],
            "installment_plans": [{key: row.get(key) for key in plan_fields} for row in installment_docs],
            "source_observed_at": observed_at,
            "updated_at": _utcnow(),
        }

    def _rebuild_comparison(self, variant_id: str) -> None:
        observed_at = _utcnow()
        product = self._snapshot(self._col("product_variants").document(_safe_doc_id(variant_id)).get())
        if not product:
            return
        cash_docs = list(self._stream_where("cash_offers", "variant_id", "==", variant_id))
        installment_docs = list(self._stream_where("installment_plans", "variant_id", "==", variant_id))
        payload = self._ready_payload(variant_id, product, cash_docs, installment_docs, observed_at)
        summary_payload = self._ready_summary_payload(variant_id, cash_docs, installment_docs, observed_at)
        ref = self._col("comparison_docs").document(_safe_doc_id(variant_id))
        summary_ref = self._col("comparison_summaries").document(_safe_doc_id(variant_id))

        def publish(tx):
            existing = self._snapshot(ref.get(transaction=tx))
            existing_observed = _as_utc((existing or {}).get("source_observed_at"))
            if existing_observed and existing_observed > observed_at:
                return False
            tx.set(ref, payload)
            tx.set(summary_ref, summary_payload)
            return True

        if self._run_transaction(publish):
            self._summary_cache.pop(variant_id, None)

    def rebuild_all_comparisons(self) -> int:
        """Build all ready comparison documents with three collection scans.

        This is used by the one-time bootstrap job.  Runtime updates call the
        per-variant method so a public comparison remains a single document read.
        """

        observed_at = _utcnow()
        products = [
            row
            for snapshot in self._col("product_variants").stream()
            if (row := self._snapshot(snapshot)) is not None
        ]
        cash_by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
        plans_by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for snapshot in self._col("cash_offers").stream():
            row = self._snapshot(snapshot)
            if row:
                cash_by_variant[str(row.get("variant_id"))].append(row)
        for snapshot in self._col("installment_plans").stream():
            row = self._snapshot(snapshot)
            if row:
                plans_by_variant[str(row.get("variant_id"))].append(row)

        prepared: list[tuple[Any, Any, dict[str, Any], dict[str, Any], int]] = []
        for product in products:
            variant_id = str(product.get("variant_id") or product["_document_id"])
            payload = self._ready_payload(
                variant_id,
                product,
                cash_by_variant.get(variant_id, []),
                plans_by_variant.get(variant_id, []),
                observed_at,
            )
            summary_payload = self._ready_summary_payload(
                variant_id,
                cash_by_variant.get(variant_id, []),
                plans_by_variant.get(variant_id, []),
                observed_at,
            )
            payload_size = len(
                json.dumps(
                    _jsonable({"comparison": payload, "summary": summary_payload}),
                    ensure_ascii=False,
                ).encode("utf-8")
            )
            prepared.append(
                (
                    self._col("comparison_docs").document(_safe_doc_id(variant_id)),
                    self._col("comparison_summaries").document(_safe_doc_id(variant_id)),
                    payload,
                    summary_payload,
                    payload_size + 4_096,
                )
            )

        chunks: list[list[tuple[Any, Any, dict[str, Any], dict[str, Any], int]]] = []
        chunk: list[tuple[Any, Any, dict[str, Any], dict[str, Any], int]] = []
        chunk_bytes = 0
        for item in prepared:
            item_bytes = item[4]
            if chunk and (len(chunk) >= 100 or chunk_bytes + item_bytes > 6_000_000):
                chunks.append(chunk)
                chunk = []
                chunk_bytes = 0
            chunk.append(item)
            chunk_bytes += item_bytes
        if chunk:
            chunks.append(chunk)

        written = 0
        for ready_chunk in chunks:
            comparison_refs = [item[0] for item in ready_chunk]

            def publish(
                tx,
                *,
                comparison_refs=comparison_refs,
                ready_chunk=ready_chunk,
            ):
                existing_by_id = {
                    snapshot.id: self._snapshot(snapshot) for snapshot in tx.get_all(comparison_refs)
                }
                publishable = []
                for item in ready_chunk:
                    comparison_ref = item[0]
                    existing = existing_by_id.get(comparison_ref.id)
                    existing_observed = _as_utc((existing or {}).get("source_observed_at"))
                    if existing_observed and existing_observed >= observed_at:
                        continue
                    publishable.append(item)
                for (
                    comparison_ref,
                    summary_ref,
                    payload,
                    summary_payload,
                    _,
                ) in publishable:
                    tx.set(comparison_ref, payload)
                    tx.set(summary_ref, summary_payload)
                return len(publishable)

            written += int(self._run_transaction(publish))
        self._summary_cache.clear()
        return written

    def rebuild_catalog_index(self, shard_size: int = 150) -> int:
        products = [
            row
            for snapshot in self._col("product_variants").stream()
            if (row := self._snapshot(snapshot)) is not None
            and row.get("source_status") != "catalog_provisional"
        ]
        products.sort(
            key=lambda row: (
                str(row.get("section") or ""),
                str(row.get("brand") or ""),
                str(row.get("model") or ""),
                float(row.get("storage_gb") or 0),
                str(row.get("canonical_name") or ""),
            )
        )
        compact_keys = (
            "variant_id",
            "canonical_name",
            "section",
            "product_type",
            "brand",
            "model",
            "variant_name",
            "ram_gb",
            "storage_gb",
            "color",
        )
        batch = self.client.batch()
        count = 0
        for start in range(0, len(products), max(1, shard_size)):
            records = [
                {key: row.get(key) for key in compact_keys} for row in products[start : start + shard_size]
            ]
            batch.set(
                self._col("catalog_index").document(f"shard-{count:04d}"),
                {
                    "shard": count,
                    "items": records,
                    "updated_at": _utcnow(),
                },
            )
            count += 1
        if count:
            batch.commit()
        self._catalog_cache = None
        return count

    def _catalog_items(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        cached = self._catalog_cache
        if cached and now - cached[0] <= self.settings.firestore_catalog_cache_seconds:
            return [dict(row) for row in cached[1]]
        with self._catalog_lock:
            cached = self._catalog_cache
            if cached and now - cached[0] <= self.settings.firestore_catalog_cache_seconds:
                return [dict(row) for row in cached[1]]
            shards = []
            for snapshot in self._col("catalog_index").stream():
                row = self._snapshot(snapshot)
                if row:
                    shards.append(row)
            shards.sort(key=lambda row: int(row.get("shard") or 0))
            items = [dict(item) for shard in shards for item in list(shard.get("items") or [])]
            self._catalog_cache = (now, items)
            return [dict(row) for row in items]

    def _attach_summaries(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = time.monotonic()
        missing: list[str] = []
        for item in items:
            variant_id = str(item.get("variant_id") or "")
            cached = self._summary_cache.get(variant_id)
            if not cached or now - cached[0] > 60:
                missing.append(variant_id)
        if missing:
            refs = [
                self._col("comparison_summaries").document(_safe_doc_id(variant_id)) for variant_id in missing
            ]
            for snapshot in self.client.get_all(refs):
                ready = self._snapshot(snapshot)
                if not ready:
                    continue
                comparison = self._materialize(
                    {"variant_id": ready.get("variant_id")},
                    [dict(row) for row in ready.get("cash_offers") or []],
                    [dict(row) for row in ready.get("installment_plans") or []],
                    include_unpriced=True,
                )
                product = comparison["product"]
                summary = {
                    key: product.get(key)
                    for key in (
                        "lowest_cash_price",
                        "lowest_delivered_total",
                        "lowest_cash_total",
                        "cash_offer_count",
                        "installment_plan_count",
                        "lowest_periodic_payment",
                    )
                }
                self._summary_cache[str(ready["variant_id"])] = (now, summary)
        for item in items:
            cached = self._summary_cache.get(str(item.get("variant_id") or ""))
            if cached:
                item.update(cached[1])
            else:
                item.update(
                    {
                        "lowest_cash_price": None,
                        "lowest_delivered_total": None,
                        "lowest_cash_total": None,
                        "cash_offer_count": 0,
                        "installment_plan_count": 0,
                        "lowest_periodic_payment": None,
                    }
                )
        return items

    @staticmethod
    def _facet(items: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
        counts: dict[str, int] = defaultdict(int)
        for row in items:
            value = row.get(field)
            if value not in (None, ""):
                counts[str(value)] += 1
        return [{field: value, "variant_count": counts[value]} for value in sorted(counts)]

    def catalog_sections(self) -> list[dict[str, Any]]:
        return self._facet(self._catalog_items(), "section")

    def catalog_brands(self, section: str | None = None) -> list[dict[str, Any]]:
        items = self._catalog_items()
        if section is not None:
            items = [row for row in items if row.get("section") == section]
        return self._facet(items, "brand")

    def catalog_product_types(self, section: str | None = None) -> list[dict[str, Any]]:
        items = self._catalog_items()
        if section is not None:
            items = [row for row in items if row.get("section") == section]
        return self._facet(items, "product_type")

    def catalog_models(self, section: str | None = None, brand: str | None = None) -> list[dict[str, Any]]:
        items = self._catalog_items()
        if section is not None:
            items = [row for row in items if row.get("section") == section]
        if brand is not None:
            items = [row for row in items if row.get("brand") == brand]
        return self._facet(items, "model")

    def catalog_variants(
        self,
        *,
        section: str | None = None,
        brand: str | None = None,
        model: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        items = self._catalog_items()
        for field, value in (
            ("section", section),
            ("brand", brand),
            ("model", model),
        ):
            if value is not None:
                items = [row for row in items if row.get(field) == value]
        items.sort(
            key=lambda row: (
                row.get("storage_gb") is None,
                float(row.get("storage_gb") or 0),
                row.get("ram_gb") is None,
                float(row.get("ram_gb") or 0),
                str(row.get("variant_name") or ""),
                str(row.get("canonical_name") or ""),
            )
        )
        return self._attach_summaries(items[: max(1, min(limit, 1000))])

    def search_products(
        self,
        query: str,
        *,
        limit: int = 20,
        section: str | None = None,
        brand: str | None = None,
    ) -> list[dict[str, Any]]:
        from app.repository import normalize_public_search_query

        normalized = normalize_public_search_query(query)
        tokens = set(normalized.split())
        ranked = []
        for row in self._catalog_items():
            if section is not None and row.get("section") != section:
                continue
            if brand is not None and row.get("brand") != brand:
                continue
            haystack = normalize_public_search_query(
                " ".join(
                    str(row.get(field) or "")
                    for field in ("canonical_name", "model", "brand", "variant_name")
                )
            )
            if normalized and normalized not in haystack and not tokens.intersection(haystack.split()):
                continue
            overlap = len(tokens.intersection(haystack.split())) / max(len(tokens), 1) if normalized else 0
            relevance = (2.0 if normalized and normalized in haystack else 0.0) + overlap
            ranked.append((0 if normalized and normalized in haystack else 1, -relevance, haystack, row))
        ranked.sort(key=lambda item: item[:3])
        result = []
        for _, negative_relevance, _, row in ranked[: max(1, min(limit, 100))]:
            result.append({**row, "relevance": -negative_relevance})
        return self._attach_summaries(result)

    def get_product_comparison(
        self, variant_id: str, *, include_unpriced: bool = False
    ) -> dict[str, Any] | None:
        alias = self._snapshot(self._col("variant_aliases").document(_safe_doc_id(variant_id)).get())
        canonical = str((alias or {}).get("canonical_variant_id") or variant_id)
        ready = self._snapshot(self._col("comparison_docs").document(_safe_doc_id(canonical)).get())
        if not ready:
            product = self._snapshot(self._col("product_variants").document(_safe_doc_id(canonical)).get())
            if not product:
                return None
            self._rebuild_comparison(canonical)
            ready = self._snapshot(self._col("comparison_docs").document(_safe_doc_id(canonical)).get())
            if not ready:
                return None
        return self._materialize(
            dict(ready.get("product") or {}),
            [dict(row) for row in ready.get("cash_offers") or []],
            [dict(row) for row in ready.get("installment_plans") or []],
            include_unpriced=include_unpriced,
        )

    def sync_catalog_discovery_sources(self) -> int:
        stores = {
            str(row.get("store_id") or row["_document_id"]): row
            for snapshot in self._col("stores").stream()
            if (row := self._snapshot(snapshot)) is not None
            and (row.get("active", True) or row.get("registry_status") == "نشط/كتالوج فقط")
            and row.get("base_url")
        }
        connectors = {
            str(row.get("store_id") or row["_document_id"]): row
            for snapshot in self._col("connector_configs").stream()
            if (row := self._snapshot(snapshot)) is not None
        }
        active_ids: set[str] = set()
        batch = self.client.batch()
        count = 0
        for store_id, store in stores.items():
            connector = connectors.get(store_id)
            if not connector:
                continue
            if not connector.get("enabled", True) and store.get("registry_status") != "نشط/كتالوج فقط":
                continue
            configured = list((connector.get("config") or {}).get("discoverySources") or [])
            for value in dict.fromkeys([store["base_url"], *configured]):
                if not value:
                    continue
                normalized = normalize_url(str(value))
                source_id = (
                    "SRC-" + hashlib.sha256(f"{store_id}|{normalized}".encode()).hexdigest()[:20].upper()
                )
                active_ids.add(source_id)
                source_ref = self._col("catalog_discovery_sources").document(_safe_doc_id(source_id))
                existing = self._snapshot(source_ref.get()) or {}
                batch.set(
                    source_ref,
                    {
                        "source_id": source_id,
                        "store_id": store_id,
                        "source_url": str(value),
                        "normalized_url": normalized,
                        "source_type": "auto",
                        "enabled": True,
                        "priority": store.get("priority"),
                        "status": existing.get("status") or "pending",
                        "next_scan_at": existing.get("next_scan_at") or _utcnow(),
                        "updated_at": _utcnow(),
                    },
                    merge=True,
                )
                count += 1
        if count:
            batch.commit()
        return count

    def create_or_get_catalog_discovery_run(
        self,
        run_slot: datetime,
        trigger_source: str,
        *,
        full_coverage: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        run_id = "CATRUN-" + hashlib.sha256(run_slot.isoformat().encode()).hexdigest()[:24]
        incomplete_active_refs = []
        if full_coverage:
            expected_store_count = len(
                {
                    str(row.get("store_id") or row["_document_id"])
                    for snapshot in self._col("catalog_discovery_sources").stream()
                    if (row := self._snapshot(snapshot)) is not None
                    and row.get("enabled", True)
                    and row.get("store_id")
                }
            )
            for snapshot in self._col("catalog_discovery_runs").stream():
                active = self._snapshot(snapshot)
                if not active:
                    continue
                if active.get("run_slot") == run_slot:
                    continue
                if str(active.get("status") or "") not in {
                    "created",
                    "enqueuing",
                    "queued",
                    "running",
                }:
                    continue
                metadata = active.get("metadata") or {}
                if not metadata.get("full_coverage") or metadata.get("superseded_by_run_id"):
                    continue
                active_store_count = max(
                    int(active.get("source_count") or 0),
                    int(active.get("queued_task_count") or 0),
                )
                if active_store_count < expected_store_count:
                    incomplete_active_refs.append((snapshot.reference, metadata))
                    continue
                active["_overlap_active"] = True
                return active, False
        ref = self._col("catalog_discovery_runs").document(_safe_doc_id(run_id))

        def create(tx):
            existing = self._snapshot(ref.get(transaction=tx))
            if existing:
                return existing, False
            value = {
                "run_id": run_id,
                "run_slot": run_slot,
                "trigger_source": trigger_source,
                "status": "created",
                "metadata": {"full_coverage": full_coverage},
                "source_count": 0,
                "queued_task_count": 0,
                "created_at": _utcnow(),
                "updated_at": _utcnow(),
            }
            tx.set(ref, value)
            return value, True

        result = self._run_transaction(create)
        if result[1] and incomplete_active_refs:
            for active_ref, active_metadata in incomplete_active_refs:
                active_ref.set(
                    {
                        "metadata": {
                            **active_metadata,
                            "superseded_by_run_id": run_id,
                            "superseded_reason": "registry_growth",
                        },
                        "updated_at": _utcnow(),
                    },
                    merge=True,
                )
        return result

    def load_due_catalog_discovery_sources(
        self,
        *,
        limit: int,
        include_not_due: bool = False,
    ) -> list[dict[str, Any]]:
        now = _utcnow()
        stores = {
            str(row.get("store_id") or row["_document_id"]): row
            for snapshot in self._col("stores").stream()
            if (row := self._snapshot(snapshot)) is not None
            and (row.get("active", True) or row.get("registry_status") == "نشط/كتالوج فقط")
        }
        connectors = {
            str(row.get("store_id") or row["_document_id"]): row
            for snapshot in self._col("connector_configs").stream()
            if (row := self._snapshot(snapshot)) is not None
        }
        values = []
        seen_stores: set[str] = set()
        for snapshot in self._col("catalog_discovery_sources").stream():
            source = self._snapshot(snapshot)
            if not source or not source.get("enabled", True):
                continue
            if not include_not_due and (_as_utc(source.get("next_scan_at")) or now) > now:
                continue
            store_id = str(source.get("store_id") or "")
            if store_id in seen_stores or store_id not in stores or store_id not in connectors:
                continue
            seen_stores.add(store_id)
            store = stores[store_id]
            connector = connectors[store_id]
            if not connector.get("enabled", True) and store.get("registry_status") != "نشط/كتالوج فقط":
                continue
            values.append(
                {
                    **source,
                    "store_name": store.get("name") or store_id,
                    "allowed_hosts": list(connector.get("allowed_hosts") or []),
                    "connector_version": connector.get("version") or "catalog-generic-v1",
                    "connector_config": connector.get("config") or {},
                    "requests_per_minute": connector.get("requests_per_minute") or 6,
                    "respect_robots": connector.get("respect_robots", True),
                    "browser_required": connector.get("browser_required", False),
                }
            )
        priority = {"P0": 0, "P1": 1, "P2": 2, "مراقبة": 3}
        values.sort(
            key=lambda row: (
                priority.get(str(row.get("priority") or ""), 9),
                str(row.get("store_id") or ""),
            )
        )
        return values[: max(1, min(int(limit), 500))]

    def count_due_catalog_discovery_sources(self) -> int:
        return len(self.load_due_catalog_discovery_sources(limit=500))

    def mark_catalog_discovery_run_enqueuing(
        self,
        run_id: str,
        *,
        source_count: int,
        metadata: dict[str, Any],
    ) -> None:
        self._col("catalog_discovery_runs").document(_safe_doc_id(run_id)).set(
            {
                "status": "enqueuing",
                "source_count": source_count,
                "queued_task_count": source_count,
                "metadata": metadata,
                "started_at": _utcnow(),
                "updated_at": _utcnow(),
            },
            merge=True,
        )

    def mark_catalog_discovery_run_enqueue_complete(self, run_id: str) -> None:
        ref = self._col("catalog_discovery_runs").document(_safe_doc_id(run_id))
        current = self._snapshot(ref.get()) or {}
        empty = int(current.get("queued_task_count") or 0) == 0
        ref.set(
            {
                "status": "success" if empty else "queued",
                "completed_at": _utcnow() if empty else None,
                "updated_at": _utcnow(),
            },
            merge=True,
        )

    def mark_catalog_discovery_run_enqueue_failed(
        self,
        run_id: str,
        message: str,
        *,
        successfully_queued: int,
        planned_tasks: int,
    ) -> None:
        self._col("catalog_discovery_runs").document(_safe_doc_id(run_id)).set(
            {
                "status": "enqueue_failed",
                "enqueue_error": message[:2000],
                "successfully_queued": successfully_queued,
                "planned_tasks": planned_tasks,
                "updated_at": _utcnow(),
            },
            merge=True,
        )

    def register_catalog_discovery_task(self, payload: CatalogDiscoveryTaskPayload) -> None:
        self._col("catalog_discovery_tasks").document(_safe_doc_id(payload.task_id)).set(
            {
                **payload.model_dump(mode="python"),
                "status": "queued",
                "attempt_count": 0,
                "created_at": _utcnow(),
                "updated_at": _utcnow(),
            }
        )

    def start_catalog_discovery_task(
        self,
        task_id: str,
        *,
        delivery_generation: int = 1,
        allow_reclaim_running: bool = False,
    ) -> str:
        ref = self._col("catalog_discovery_tasks").document(_safe_doc_id(task_id))

        def claim(tx):
            current = self._snapshot(ref.get(transaction=tx))
            if not current:
                return "missing"
            if current.get("status") in {"success", "failed"}:
                return "terminal"
            if current.get("status") == "running" and not allow_reclaim_running:
                return "running"
            tx.set(
                ref,
                {
                    "status": "running",
                    "attempt_count": int(current.get("attempt_count") or 0) + 1,
                    "started_at": current.get("started_at") or _utcnow(),
                    "updated_at": _utcnow(),
                },
                merge=True,
            )
            return "claimed"

        return str(self._run_transaction(claim))

    def _rebuild_catalog_discovery_run(self, run_id: str) -> None:
        tasks = list(self._stream_where("catalog_discovery_tasks", "run_id", "==", run_id))
        terminal = [row for row in tasks if row.get("status") in {"success", "failed"}]
        successful = [row for row in tasks if row.get("status") == "success"]
        failed = [row for row in tasks if row.get("status") == "failed"]
        run_ref = self._col("catalog_discovery_runs").document(_safe_doc_id(run_id))
        run = self._snapshot(run_ref.get()) or {}
        queued = int(run.get("queued_task_count") or 0)
        complete = queued > 0 and len(terminal) >= queued
        run_ref.set(
            {
                "status": ("failed" if not successful else "success") if complete else "running",
                "completed_task_count": len(terminal),
                "successful_task_count": len(successful),
                "failed_task_count": len(failed),
                "candidates_seen": sum(int(row.get("candidates_seen") or 0) for row in tasks),
                "candidates_new": sum(int(row.get("candidates_new") or 0) for row in tasks),
                "mappings_created": sum(int(row.get("mappings_created") or 0) for row in tasks),
                "completed_at": _utcnow() if complete else None,
                "updated_at": _utcnow(),
            },
            merge=True,
        )

    def finish_catalog_discovery_task(
        self,
        task_id: str,
        *,
        status: str,
        delivery_generation: int | None = None,
        http_status: int | None = None,
        response_bytes: int = 0,
        candidates_seen: int = 0,
        candidates_new: int = 0,
        mappings_created: int = 0,
        provisional_products: int = 0,
        verified_products: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        ref = self._col("catalog_discovery_tasks").document(_safe_doc_id(task_id))
        current = self._snapshot(ref.get())
        if not current:
            return
        terminal = status in {"success", "failed"}
        ref.set(
            {
                "status": status,
                "http_status": http_status,
                "response_bytes": response_bytes,
                "candidates_seen": candidates_seen,
                "candidates_new": candidates_new,
                "mappings_created": mappings_created,
                "provisional_products": provisional_products,
                "verified_products": verified_products,
                "error_code": error_code,
                "error_message": (error_message or "")[:4000] or None,
                "metrics": metrics or {},
                "completed_at": _utcnow() if terminal else None,
                "updated_at": _utcnow(),
            },
            merge=True,
        )
        successful = status == "success"
        delay = (
            self.settings.catalog_discovery_rescan_hours
            if successful
            else 1
            if status == "retryable_failed"
            else 168
        )
        self._col("catalog_discovery_sources").document(_safe_doc_id(str(current["source_id"]))).set(
            {
                "status": "active" if successful else status,
                "last_scan_at": _utcnow(),
                "last_success_at": _utcnow() if successful else None,
                "next_scan_at": _utcnow() + timedelta(hours=delay),
                "last_error_code": error_code,
                "last_error_message": (error_message or "")[:4000] or None,
                "updated_at": _utcnow(),
            },
            merge=True,
        )
        self._rebuild_catalog_discovery_run(str(current["run_id"]))

    def promote_catalog_retry_exhausted(self, task_id: str) -> None:
        current = (
            self._snapshot(self._col("catalog_discovery_tasks").document(_safe_doc_id(task_id)).get()) or {}
        )
        self.finish_catalog_discovery_task(
            task_id,
            status="failed",
            error_code=current.get("error_code") or "retry_exhausted",
            error_message=current.get("error_message") or "Cloud Tasks retries exhausted",
        )

    def ingest_catalog_candidates(
        self,
        payload: CatalogDiscoveryTaskPayload,
        candidates: list[dict[str, Any]],
    ) -> dict[str, int]:
        # Firestore is a rollback backend. Discovery remains useful there, but
        # automatic publication is intentionally disabled to avoid producing a
        # different catalog from PostgreSQL during a temporary rollback.
        new_count = 0
        batch = self.client.batch()
        for item in candidates:
            candidate_id = (
                "CAT-"
                + hashlib.sha256(f"{payload.store_id}|{item['normalized_url']}".encode())
                .hexdigest()[:22]
                .upper()
            )
            ref = self._col("catalog_candidates").document(_safe_doc_id(candidate_id))
            if not self._snapshot(ref.get()):
                new_count += 1
            raw = dict(item)
            raw["text"] = str(raw.get("text") or "")[:4000]
            batch.set(
                ref,
                {
                    "candidate_id": candidate_id,
                    "store_id": payload.store_id,
                    "source_id": payload.source_id,
                    "normalized_url": item["normalized_url"],
                    "source_url": item["source_url"],
                    "title": item["title"],
                    "brand": item.get("brand"),
                    "sku": item.get("sku"),
                    "gtin": item.get("gtin"),
                    "fingerprint": item.get("fingerprint"),
                    "currency": item.get("currency"),
                    "observed_price": amount_to_minor(item.get("price")),
                    "availability": item.get("availability"),
                    "source_method": item.get("source_method"),
                    "status": "needs_review_rollback_backend",
                    "last_run_id": payload.run_id,
                    "last_seen_at": _utcnow(),
                    "raw_payload": _bounded_raw_payload(raw),
                    "updated_at": _utcnow(),
                },
                merge=True,
            )
        if candidates:
            batch.commit()
        return {
            "candidates_new": new_count,
            "mappings_created": 0,
            "matched_existing_mappings": 0,
            "review_candidates": len(candidates),
            "provisional_products": 0,
            "verified_products": 0,
        }

    def get_catalog_discovery_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._snapshot(self._col("catalog_discovery_runs").document(_safe_doc_id(run_id)).get())
        if not run:
            return None
        tasks = list(self._stream_where("catalog_discovery_tasks", "run_id", "==", run_id))
        tasks.sort(key=lambda row: str(row.get("scheduled_for") or ""))
        return {"run": _jsonable(run), "tasks": _jsonable(tasks)}

    def get_run(
        self,
        run_id: str,
        *,
        task_limit: int = 500,
        task_offset: int = 0,
    ) -> dict[str, Any] | None:
        if not 1 <= task_limit <= 500:
            raise ValueError("task_limit must be between 1 and 500")
        if task_offset < 0:
            raise ValueError("task_offset must be non-negative")

        run = self._snapshot(self._run_ref(run_id).get())
        if not run:
            return None
        tasks = list(self._stream_where("scrape_task_runs", "run_id", "==", run_id))
        tasks.sort(
            key=lambda row: (
                _as_utc(row.get("scheduled_for")) or datetime.max.replace(tzinfo=UTC),
                str(row.get("store_id") or ""),
                str(row.get("external_task_id") or ""),
            )
        )
        public_task_fields = (
            "external_task_id",
            "store_id",
            "source_url",
            "status",
            "scheduled_for",
            "started_at",
            "completed_at",
            "cash_updates",
            "installment_updates",
            "discovered_urls",
            "error_code",
            "error_message",
        )
        run.pop("_document_id", None)
        total_task_rows = len(tasks)
        page = tasks[task_offset : task_offset + task_limit]
        return {
            "run": _jsonable(run),
            "tasks": [_jsonable({key: row.get(key) for key in public_task_fields}) for row in page],
            "pagination": {
                "limit": task_limit,
                "offset": task_offset,
                "returned_task_rows": len(page),
                "total_task_rows": total_task_rows,
                "has_more": task_offset + len(page) < total_task_rows,
            },
        }

    def set_registry_stats(
        self,
        *,
        products: int,
        registry_stores: int,
        active_stores: int,
        connected_stores: int,
        active_mappings: int,
    ) -> None:
        ref = self._col("system").document("stats")
        ref.set(
            {
                "products": products,
                "registry_stores": registry_stores,
                "active_stores": active_stores,
                "connected_stores": connected_stores,
                "active_mappings": active_mappings,
                "updated_at": _utcnow(),
            },
            merge=True,
        )

    def _rebuild_system_stats(self) -> None:
        existing = self._snapshot(self._col("system").document("stats").get()) or {}
        priced_stores: set[str] = set()
        priced_products: set[str] = set()
        priced_cash_offers = 0
        active_installment_plans = 0
        latest_cash: datetime | None = None
        latest_installment: datetime | None = None
        now = _utcnow()

        for snapshot in self._col("comparison_docs").stream():
            ready = self._snapshot(snapshot)
            if not ready:
                continue
            cash_docs = [dict(row) for row in ready.get("cash_offers") or []]
            cash_by_key = {str(row.get("offer_key")): row for row in cash_docs if row.get("offer_key")}
            for row in cash_docs:
                public = self._cash_public(row, now)
                success = _as_utc(row.get("last_success_at"))
                if success and (latest_cash is None or success > latest_cash):
                    latest_cash = success
                if public["eligible_for_ranking"]:
                    priced_cash_offers += 1
                    priced_stores.add(str(row.get("store_id")))
                    priced_products.add(str(row.get("variant_id")))
            for row in ready.get("installment_plans") or []:
                success = _as_utc(row.get("last_success_at"))
                if success and (latest_installment is None or success > latest_installment):
                    latest_installment = success
                if self._installment_public(dict(row), now, cash_by_key)["eligible_for_ranking"]:
                    active_installment_plans += 1

        payload = {
            key: existing.get(key, 0)
            for key in (
                "products",
                "registry_stores",
                "active_stores",
                "connected_stores",
                "active_mappings",
            )
        }
        payload.update(
            {
                "priced_stores": len(priced_stores),
                "priced_products": len(priced_products),
                "priced_cash_offers": priced_cash_offers,
                "active_installment_plans": active_installment_plans,
                "latest_cash_update": latest_cash,
                "latest_installment_update": latest_installment,
                "updated_at": now,
            }
        )
        self._col("system").document("stats").set(payload)

    def system_stats(self) -> dict[str, Any]:
        stats = self._snapshot(self._col("system").document("stats").get())
        defaults = {
            "products": 0,
            "registry_stores": 0,
            "active_stores": 0,
            "connected_stores": 0,
            "priced_stores": 0,
            "priced_products": 0,
            "active_mappings": 0,
            "priced_cash_offers": 0,
            "active_installment_plans": 0,
            "latest_cash_update": None,
            "latest_installment_update": None,
            "latest_price_run_slot": None,
            "latest_price_run_control_error": None,
        }
        if stats:
            stats.pop("_document_id", None)
            stats.pop("updated_at", None)
        stats = {**defaults, **(stats or {})}
        catalog_sources = [
            row
            for snapshot in self._col("catalog_discovery_sources").stream()
            if (row := self._snapshot(snapshot)) is not None and row.get("enabled", True)
        ]
        catalog_candidates = [
            row
            for snapshot in self._col("catalog_candidates").stream()
            if (row := self._snapshot(snapshot)) is not None
        ]
        stats["catalog_sources"] = len(catalog_sources)
        stats["catalog_registered_stores"] = len({str(row.get("store_id") or "") for row in catalog_sources})
        stats["catalog_candidates"] = len(catalog_candidates)
        stats["catalog_review_candidates"] = sum(
            str(row.get("status") or "").startswith("needs_review") for row in catalog_candidates
        )
        stats["catalog_provisional_products"] = 0
        stats["catalog_verified_products"] = 0
        stats["refresh_interval_minutes"] = self.settings.refresh_interval_minutes
        stats["next_update_at"] = next_refresh_at().isoformat()
        stats["scheduler_timezone"] = self.settings.scheduler_timezone
        return _jsonable(stats)
