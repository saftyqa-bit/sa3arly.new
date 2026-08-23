from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from psycopg.types.json import Jsonb

from app.catalog_bootstrap import normalize_external_product_records
from app.catalog_identity import (
    catalog_entity_identity,
    catalog_observation_publishable,
    catalog_technical_specs,
)
from app.catalog_matching import (
    build_catalog_variant_index,
    catalog_candidate_has_match_evidence,
    deterministic_catalog_match,
)
from app.db import connection, transaction
from app.schedule import next_refresh_at
from app.schemas import (
    CashOfferExtract,
    CatalogBootstrapImportRequest,
    CatalogDiscoveryTaskPayload,
    InstallmentPlanExtract,
    MappingTarget,
    ScrapeGroupPayload,
)
from app.scraping.matching import score_candidate
from app.scraping.normalization import normalize_text, normalize_url
from app.scraping.types import ProductCandidate
from app.settings import get_settings

logger = logging.getLogger(__name__)

CATALOG_CANDIDATE_RECONCILE_VERSION = 1

PUBLIC_SEARCH_ALIASES = {
    "آيفون": "iphone",
    "ايفون": "iphone",
    "أيفون": "iphone",
    "سامسونج": "samsung",
    "شاومي": "xiaomi",
    "هواوي": "huawei",
    "هونر": "honor",
    "أوبو": "oppo",
    "اوبو": "oppo",
    "ريلمي": "realme",
    "لينوفو": "lenovo",
    "ديل": "dell",
    "اتش بي": "hp",
    "إتش بي": "hp",
    "ماك بوك": "macbook",
    "لاب توب": "laptop",
    "لابتوب": "laptop",
    "برو ماكس": "pro max",
    "الترا": "ultra",
}


class PriceAnomalyError(ValueError):
    """Raised when a new price is implausibly far from the last successful price."""


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def normalize_public_search_query(query: str) -> str:
    normalized = normalize_text(query)
    for alias in sorted(PUBLIC_SEARCH_ALIASES, key=len, reverse=True):
        normalized = normalized.replace(normalize_text(alias), PUBLIC_SEARCH_ALIASES[alias])
    return re.sub(r"\s+", " ", normalized).strip()


@contextmanager
def store_advisory_lock(store_id: str, slot: int = 0):
    """Limit each store to its configured number of cross-instance lock slots."""
    lock_key = f"{store_id}:{max(slot, 0)}"
    with connection() as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (lock_key,))
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_key,))


def reserve_store_request_slot(
    store_id: str,
    requests_per_minute: int,
    *,
    max_wait_seconds: float | None = None,
) -> float:
    """Reserve a durable request slot, or report an open long cooldown."""
    spacing_seconds = 60.0 / max(int(requests_per_minute or 1), 1)
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO store_rate_limits (store_id)
            VALUES (%s)
            ON CONFLICT (store_id) DO NOTHING
            """,
            (store_id,),
        )
        row = conn.execute(
            """
            SELECT next_allowed_at, NOW() AS database_now
            FROM store_rate_limits
            WHERE store_id = %s
            FOR UPDATE
            """,
            (store_id,),
        ).fetchone()
        database_now = row["database_now"]
        next_allowed_at = row["next_allowed_at"]
        slot = max(database_now, next_allowed_at)
        wait_seconds = max((slot - database_now).total_seconds(), 0.0)
        if max_wait_seconds is not None and wait_seconds > max_wait_seconds:
            return wait_seconds
        conn.execute(
            """
            UPDATE store_rate_limits
            SET next_allowed_at = %s,
                updated_at = NOW()
            WHERE store_id = %s
            """,
            (slot + timedelta(seconds=spacing_seconds), store_id),
        )
        return wait_seconds


def defer_store_requests(store_id: str, delay_seconds: int) -> None:
    """Open or extend a durable per-store circuit using the rate-limit row."""

    delay = max(1, int(delay_seconds))
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO store_rate_limits (store_id)
            VALUES (%s)
            ON CONFLICT (store_id) DO NOTHING
            """,
            (store_id,),
        )
        conn.execute(
            """
            UPDATE store_rate_limits
            SET next_allowed_at = GREATEST(
                    next_allowed_at,
                    NOW() + (%s * INTERVAL '1 second')
                ),
                updated_at = NOW()
            WHERE store_id = %s
            """,
            (delay, store_id),
        )


def reconcile_stale_runs(stale_after_minutes: int) -> int:
    """Fail abandoned task attempts and recalculate their parent run counters."""
    with transaction() as conn:
        stale = conn.execute(
            """
            UPDATE price_tasks
            SET status = 'failed',
                completed_at = NOW(),
                error_code = COALESCE(error_code, 'stale_task_reconciled'),
                error_message = COALESCE(
                    error_message,
                    'Task exceeded the reconciliation age without a terminal result'
                )
            WHERE status NOT IN ('success', 'failed')
              AND COALESCE(started_at, scheduled_for) <
                  NOW() - (%s * INTERVAL '1 minute')
            RETURNING run_id
            """,
            (max(int(stale_after_minutes), 30),),
        ).fetchall()
        run_ids = sorted({row["run_id"] for row in stale})
        for run_id in run_ids:
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('success', 'failed')) AS completed,
                    COUNT(*) FILTER (WHERE status = 'success') AS succeeded,
                    COUNT(*) FILTER (WHERE status = 'failed') AS failed
                FROM price_tasks
                WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()
            conn.execute(
                """
                UPDATE price_runs
                SET completed_task_count = %s,
                    successful_task_count = %s,
                    failed_task_count = %s,
                    status = CASE
                        WHEN COALESCE((metadata->>'enqueue_complete')::boolean, FALSE) = FALSE
                            THEN 'enqueue_failed'
                        WHEN %s >= queued_task_count AND queued_task_count > 0
                            THEN 'completed_with_errors'
                        ELSE 'running'
                    END,
                    completed_at = CASE
                        WHEN %s >= queued_task_count AND queued_task_count > 0
                        THEN NOW()
                        ELSE completed_at
                    END
                WHERE run_id = %s
                """,
                (
                    counts["completed"],
                    counts["succeeded"],
                    counts["failed"],
                    counts["completed"],
                    counts["completed"],
                    run_id,
                ),
            )
        return len(stale)


def finalize_overdue_price_runs(max_run_age_minutes: int) -> dict[str, Any]:
    """Terminalize price runs that exceeded their total runtime budget.

    Cloud Tasks can exhaust a delivery before the final request reaches the
    application.  In that case the database task remains ``retryable_failed``
    forever and the parent run never reaches its equality-based completion
    condition.  This independent finalizer preserves the last root error while
    making every remaining task terminal and rebuilding the run counters.
    """

    deadline_minutes = max(1, int(max_run_age_minutes))
    with transaction() as conn:
        overdue = conn.execute(
            """
            SELECT run_id
            FROM price_runs
            WHERE status IN ('created', 'enqueuing', 'queued', 'running')
              AND COALESCE((metadata ->> 'enqueue_complete')::boolean, FALSE)
              AND started_at <= NOW() - (%s * INTERVAL '1 minute')
            ORDER BY started_at
            """,
            (deadline_minutes,),
        ).fetchall()
        run_ids = [str(row["run_id"]) for row in overdue]
        if not run_ids:
            return {
                "deadline_minutes": deadline_minutes,
                "runs_finalized": 0,
                "tasks_finalized": 0,
                "run_ids": [],
            }

        finalized_tasks = conn.execute(
            """
            UPDATE price_tasks
            SET status = 'failed',
                completed_at = NOW(),
                error_code = COALESCE(error_code, 'run_deadline_exceeded'),
                error_message = LEFT(
                    CONCAT_WS(
                        E'\n',
                        NULLIF(error_message, ''),
                        'Price run exceeded its maximum runtime; finalized independently'
                    ),
                    2000
                ),
                metrics = COALESCE(metrics, '{}'::jsonb) || jsonb_build_object(
                    'deadline_finalized', TRUE,
                    'previous_status', status
                )
            WHERE run_id = ANY(%s::uuid[])
              AND status NOT IN ('success', 'failed')
            RETURNING run_id
            """,
            (run_ids,),
        ).fetchall()

        for run_id in run_ids:
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('success', 'failed')) AS completed,
                    COUNT(*) FILTER (WHERE status = 'success') AS succeeded,
                    COUNT(*) FILTER (WHERE status = 'failed') AS failed
                FROM price_tasks
                WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()
            conn.execute(
                """
                UPDATE price_runs
                SET completed_task_count = %s,
                    successful_task_count = %s,
                    failed_task_count = %s,
                    status = CASE
                        WHEN %s > 0 THEN 'completed_with_errors'
                        ELSE 'completed'
                    END,
                    completed_at = NOW(),
                    metadata = metadata || jsonb_build_object(
                        'deadline_finalized', TRUE,
                        'deadline_minutes', %s,
                        'deadline_finalized_at', NOW()
                    )
                WHERE run_id = %s
                """,
                (
                    counts["completed"],
                    counts["succeeded"],
                    counts["failed"],
                    counts["failed"],
                    deadline_minutes,
                    run_id,
                ),
            )

    return {
        "deadline_minutes": deadline_minutes,
        "runs_finalized": len(run_ids),
        "tasks_finalized": len(finalized_tasks),
        "run_ids": run_ids,
    }


def repair_terminal_price_run(run_id: str) -> dict[str, Any] | None:
    """Rebuild a terminal run state without changing any task outcome.

    A control-plane exception used to overwrite an already completed run with
    ``enqueue_failed``.  Recalculate the counters from the immutable task rows
    and repair the parent only when every registered task is terminal.
    """

    with transaction() as conn:
        run = conn.execute(
            "SELECT status FROM price_runs WHERE run_id = %s FOR UPDATE",
            (run_id,),
        ).fetchone()
        if not run:
            return None
        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status IN ('success', 'failed')) AS completed,
                COUNT(*) FILTER (WHERE status = 'success') AS succeeded,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM price_tasks
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()
        total = int(counts["total"] or 0)
        completed = int(counts["completed"] or 0)
        if total == 0 or completed != total:
            return {
                "run_id": run_id,
                "status": str(run["status"]),
                "repaired": False,
                "total": total,
                "completed": completed,
            }
        status = "completed_with_errors" if int(counts["failed"] or 0) else "completed"
        conn.execute(
            """
            UPDATE price_runs
            SET status = %s,
                completed_at = COALESCE(completed_at, NOW()),
                queued_task_count = %s,
                completed_task_count = %s,
                successful_task_count = %s,
                failed_task_count = %s,
                metadata = metadata || jsonb_build_object(
                    'terminal_state_repaired', TRUE,
                    'terminal_state_repaired_at', NOW()
                )
            WHERE run_id = %s
            """,
            (
                status,
                total,
                completed,
                int(counts["succeeded"] or 0),
                int(counts["failed"] or 0),
                run_id,
            ),
        )
        return {
            "run_id": run_id,
            "status": status,
            "repaired": str(run["status"]) != status,
            "total": total,
            "completed": completed,
        }


def create_or_get_run(run_slot: datetime, trigger_source: str) -> tuple[dict[str, Any], bool]:
    with transaction() as conn:
        created = conn.execute(
            """
            INSERT INTO price_runs (run_slot, trigger_source, status)
            VALUES (%s, %s, 'created')
            ON CONFLICT (run_slot) DO NOTHING
            RETURNING *
            """,
            (run_slot, trigger_source),
        ).fetchone()
        if created:
            return dict(created), True
        existing = conn.execute("SELECT * FROM price_runs WHERE run_slot = %s", (run_slot,)).fetchone()
        if not existing:
            raise RuntimeError("Could not create or load scheduled refresh run")
        return dict(existing), False


def load_active_mapping_rows() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT
                m.mapping_id, m.offer_id, m.offer_key, m.variant_id, m.store_id,
                m.seller_id, m.seller_name, m.store_sku, m.source_url, m.normalized_url,
                m.url_type, m.direct_product_url, m.direct_url_status,
                CASE
                    WHEN m.direct_url_status = 'verified'
                         AND NULLIF(m.direct_product_url, '') IS NOT NULL
                    THEN m.direct_product_url
                    WHEN COALESCE(m.metadata->>'prefer_direct_scrape', 'false') = 'true'
                    THEN COALESCE(NULLIF(m.direct_product_url, ''), m.source_url)
                    ELSE m.source_url
                END AS effective_source_url,
                CASE
                    WHEN NULLIF(m.direct_product_url, '') IS NOT NULL
                         AND (
                            m.direct_url_status = 'verified'
                            OR COALESCE(
                                m.metadata->>'prefer_direct_scrape',
                                'false'
                            ) = 'true'
                         )
                    THEN 'رابط منتج مباشر مكتشف'
                    ELSE m.url_type
                END AS effective_url_type,
                m.title_as_seen, m.match_method,
                m.match_confidence, m.extraction_hint,
                p.canonical_name, p.brand, p.model, p.variant_name, p.ram_gb,
                p.storage_gb, p.color, p.manufacturer_sku, p.gtin,
                s.name AS store_name,
                c.mode AS connector_mode, c.allowed_hosts, c.browser_required,
                c.respect_robots, c.version AS connector_version, c.config AS connector_config,
                c.requests_per_minute, c.max_concurrency
            FROM listings m
            JOIN variants p ON p.variant_id = m.variant_id
            JOIN stores s ON s.store_id = m.store_id AND s.active = TRUE
            JOIN connector_configs c ON c.store_id = m.store_id AND c.enabled = TRUE
            WHERE m.active = TRUE
              AND COALESCE(NULLIF(m.direct_product_url, ''), NULLIF(m.source_url, '')) IS NOT NULL
            ORDER BY s.priority NULLS LAST, m.store_id,
                     CASE
                         WHEN m.direct_url_status = 'verified'
                              AND NULLIF(m.direct_product_url, '') IS NOT NULL
                         THEN m.direct_product_url
                         WHEN COALESCE(m.metadata->>'prefer_direct_scrape', 'false') = 'true'
                         THEN COALESCE(NULLIF(m.direct_product_url, ''), m.source_url)
                         ELSE m.source_url
                     END
            """
        ).fetchall()
        return [dict(row) for row in rows]


def load_failed_mapping_rows(source_run_id: str) -> list[dict[str, Any]]:
    """Return active mappings belonging only to failed source-run URL groups."""

    with connection() as conn:
        failed_rows = conn.execute(
            """
            SELECT DISTINCT store_id, source_url
            FROM price_tasks
            WHERE run_id = %s AND status = 'failed'
            """,
            (source_run_id,),
        ).fetchall()
    failed_urls = {(str(row["store_id"]), str(row["source_url"])) for row in failed_rows}
    if not failed_urls:
        return []

    selected = []
    for row in load_active_mapping_rows():
        store_id = str(row["store_id"])
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


def load_mapping_targets(mapping_ids: list[str]) -> list[MappingTarget]:
    if not mapping_ids:
        return []
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT
                m.mapping_id, m.offer_id, m.offer_key, m.variant_id, m.store_id,
                m.seller_id, m.seller_name, m.store_sku,
                CASE
                    WHEN m.direct_url_status = 'verified'
                         AND NULLIF(m.direct_product_url, '') IS NOT NULL
                    THEN m.direct_product_url
                    WHEN COALESCE(m.metadata->>'prefer_direct_scrape', 'false') = 'true'
                    THEN COALESCE(NULLIF(m.direct_product_url, ''), m.source_url)
                    ELSE m.source_url
                END AS source_url,
                CASE
                    WHEN NULLIF(m.direct_product_url, '') IS NOT NULL
                         AND (
                            m.direct_url_status = 'verified'
                            OR COALESCE(
                                m.metadata->>'prefer_direct_scrape',
                                'false'
                            ) = 'true'
                         )
                    THEN 'رابط منتج مباشر مكتشف'
                    ELSE m.url_type
                END AS url_type,
                m.title_as_seen, m.match_method, m.match_confidence, m.extraction_hint,
                p.canonical_name, p.brand, p.model, p.variant_name, p.ram_gb,
                p.storage_gb, p.color, p.manufacturer_sku, p.gtin
            FROM listings m
            JOIN variants p ON p.variant_id = m.variant_id
            WHERE m.mapping_id = ANY(%s) AND m.active = TRUE
            ORDER BY array_position(%s::text[], m.mapping_id)
            """,
            (mapping_ids, mapping_ids),
        ).fetchall()

    def number(value):
        return float(value) if value is not None else None

    return [
        MappingTarget(
            mapping_id=row["mapping_id"],
            offer_id=row["offer_id"],
            offer_key=row["offer_key"],
            variant_id=row["variant_id"],
            store_id=row["store_id"],
            seller_id=row.get("seller_id"),
            seller_name=row.get("seller_name"),
            store_sku=row.get("store_sku"),
            source_url=row["source_url"],
            url_type=row.get("url_type"),
            title_as_seen=row.get("title_as_seen"),
            match_method=row.get("match_method"),
            match_confidence=row.get("match_confidence"),
            extraction_hint=row.get("extraction_hint"),
            canonical_name=row["canonical_name"],
            brand=row.get("brand"),
            model=row.get("model"),
            variant_name=row.get("variant_name"),
            ram_gb=number(row.get("ram_gb")),
            storage_gb=number(row.get("storage_gb")),
            color=row.get("color"),
            manufacturer_sku=row.get("manufacturer_sku"),
            gtin=row.get("gtin"),
        )
        for row in rows
    ]


def mark_run_enqueuing(
    run_id: str,
    *,
    mapping_count: int,
    url_group_count: int,
    queued_task_count: int,
    metadata: dict[str, Any] | None = None,
) -> None:
    with transaction() as conn:
        conn.execute(
            """
            UPDATE price_runs
            SET status = CASE WHEN %s = 0 THEN 'completed' ELSE 'enqueuing' END,
                completed_at = CASE WHEN %s = 0 THEN NOW() ELSE NULL END,
                mapping_count = %s,
                url_group_count = %s,
                queued_task_count = %s,
                metadata = metadata || %s::jsonb
            WHERE run_id = %s
            """,
            (
                queued_task_count,
                queued_task_count,
                mapping_count,
                url_group_count,
                queued_task_count,
                json.dumps({**(metadata or {}), "enqueue_complete": False}),
                run_id,
            ),
        )


def mark_run_enqueue_complete(run_id: str) -> None:
    """Finalize enqueueing without overwriting tasks that already completed."""
    with transaction() as conn:
        conn.execute(
            """
            UPDATE price_runs
            SET status = CASE
                    WHEN queued_task_count = 0 THEN 'completed'
                    WHEN completed_task_count >= queued_task_count
                        THEN CASE WHEN failed_task_count > 0
                                  THEN 'completed_with_errors' ELSE 'completed' END
                    WHEN completed_task_count > 0 THEN 'running'
                    ELSE 'queued'
                END,
                completed_at = CASE
                    WHEN queued_task_count = 0 OR completed_task_count >= queued_task_count
                    THEN COALESCE(completed_at, NOW())
                    ELSE NULL
                END,
                metadata = metadata || '{"enqueue_complete": true}'::jsonb
            WHERE run_id = %s
            """,
            (run_id,),
        )


def mark_run_enqueue_failed(
    run_id: str,
    message: str,
    *,
    successfully_queued: int = 0,
    planned_tasks: int | None = None,
) -> None:
    """Record a partial enqueue failure without pretending every planned task exists."""
    metadata = {
        "enqueue_error": message,
        "successfully_queued": successfully_queued,
        "enqueue_complete": False,
    }
    if planned_tasks is not None:
        metadata["planned_tasks"] = planned_tasks
    with transaction() as conn:
        conn.execute(
            """
            UPDATE price_runs
            SET status = 'enqueue_failed',
                completed_at = NOW(),
                metadata = metadata || %s::jsonb
            WHERE run_id = %s
              AND status IN ('created', 'enqueuing', 'queued', 'running')
              AND completed_at IS NULL
            """,
            (json.dumps(metadata), run_id),
        )


def register_task_run(payload: ScrapeGroupPayload) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO price_tasks (
                external_task_id, run_id, store_id, source_url, url_type,
                mapping_count, scheduled_for, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued')
            ON CONFLICT (external_task_id) DO NOTHING
            """,
            (
                payload.task_id,
                payload.run_id,
                payload.store_id,
                payload.source_url,
                payload.url_type,
                len(payload.mapping_ids or payload.mappings),
                payload.scheduled_for,
            ),
        )
        mapping_ids = payload.mapping_ids or [m.mapping_id for m in payload.mappings]
        conn.execute(
            """
            UPDATE listings
            SET last_enqueued_run_id = %s, updated_at = NOW()
            WHERE mapping_id = ANY(%s)
            """,
            (payload.run_id, mapping_ids),
        )


def load_registered_task_identities(run_id: str) -> dict[str, dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT external_task_id, source_url, url_type
            FROM price_tasks
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchall()
    return {
        str(row["external_task_id"]): {
            "source_url": row["source_url"],
            "url_type": row["url_type"],
        }
        for row in rows
        if row["external_task_id"]
    }


def count_registered_tasks(run_id: str) -> int:
    with connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS task_count FROM price_tasks WHERE run_id = %s",
            (run_id,),
        ).fetchone()
    return int(row["task_count"] or 0)


def start_task(task_id: str, *, allow_reclaim_running: bool = False) -> str:
    """Claim a task once, while allowing a Cloud Tasks retry to reclaim a lost attempt."""
    with transaction() as conn:
        existing = conn.execute(
            """
            SELECT run_id, status
            FROM price_tasks
            WHERE external_task_id = %s
            FOR UPDATE
            """,
            (task_id,),
        ).fetchone()
        if not existing:
            return "missing"
        status = existing["status"]
        if status in {"success", "failed"}:
            return "terminal"
        if status == "running" and not allow_reclaim_running:
            return "running"

        conn.execute(
            """
            UPDATE price_tasks
            SET attempt = CASE
                    WHEN status IN ('retryable_failed', 'running') THEN attempt + 1
                    ELSE attempt
                END,
                status = 'running',
                started_at = NOW(),
                completed_at = NULL
            WHERE external_task_id = %s
            """,
            (task_id,),
        )
        conn.execute(
            """
            UPDATE price_runs r
            SET status = CASE
                    WHEN r.status IN ('queued', 'enqueuing', 'enqueue_failed') THEN 'running'
                    ELSE r.status
                END
            FROM price_tasks t
            WHERE t.external_task_id = %s AND r.run_id = t.run_id
            """,
            (task_id,),
        )
        return "claimed"


def finish_task(
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
    terminal_statuses = {"success", "failed"}
    with transaction() as conn:
        existing = conn.execute(
            "SELECT run_id, status FROM price_tasks WHERE external_task_id = %s FOR UPDATE",
            (task_id,),
        ).fetchone()
        if not existing:
            return

        was_terminal = existing["status"] in terminal_statuses
        is_terminal = status in terminal_statuses
        run_id = existing["run_id"]
        # A deadline finalizer can make the task terminal while an older worker
        # response is still in flight.  Never let that late response overwrite
        # the terminal record or desynchronize the parent counters.
        if was_terminal:
            return
        conn.execute(
            """
            UPDATE price_tasks
            SET status = %s,
                completed_at = NOW(),
                http_status = %s,
                response_bytes = %s,
                cash_updates = %s,
                installment_updates = %s,
                discovered_urls = %s,
                error_code = %s,
                error_message = %s,
                metrics = %s
            WHERE external_task_id = %s
            """,
            (
                status,
                http_status,
                response_bytes,
                cash_updates,
                installment_updates,
                discovered_urls,
                error_code,
                error_message[:2000] if error_message else None,
                Jsonb(metrics or {}),
                task_id,
            ),
        )

        # Retryable failures are attempts, not completed tasks. Run counters are
        # updated only on the first transition into a terminal state.
        if not is_terminal:
            return

        success = status == "success"
        conn.execute(
            """
            UPDATE price_runs
            SET completed_task_count = completed_task_count + 1,
                successful_task_count = successful_task_count + %s,
                failed_task_count = failed_task_count + %s,
                cash_updates = cash_updates + %s,
                installment_updates = installment_updates + %s,
                discovered_urls = discovered_urls + %s
            WHERE run_id = %s
            """,
            (
                1 if success else 0,
                0 if success else 1,
                cash_updates,
                installment_updates,
                discovered_urls,
                run_id,
            ),
        )
        conn.execute(
            """
            UPDATE price_runs
            SET status = CASE WHEN failed_task_count > 0 THEN 'completed_with_errors' ELSE 'completed' END,
                completed_at = NOW()
            WHERE run_id = %s
              AND completed_task_count >= queued_task_count
              AND queued_task_count > 0
            """,
            (run_id,),
        )


def promote_retry_exhausted(task_id: str) -> None:
    """Make the last retryable attempt terminal without double-counting the run."""
    with transaction() as conn:
        existing = conn.execute(
            """
            SELECT run_id, status, error_code, error_message
            FROM price_tasks
            WHERE external_task_id = %s
            FOR UPDATE
            """,
            (task_id,),
        ).fetchone()
        if not existing or existing["status"] in {"success", "failed"}:
            return
        run_id = existing["run_id"]
        conn.execute(
            """
            UPDATE price_tasks
            SET status = 'failed',
                completed_at = NOW(),
                error_code = COALESCE(error_code, 'retry_exhausted'),
                error_message = LEFT(
                    COALESCE(error_message || E'\n', '') || 'Cloud Tasks retry budget exhausted',
                    2000
                )
            WHERE external_task_id = %s
            """,
            (task_id,),
        )
        conn.execute(
            """
            UPDATE price_runs
            SET completed_task_count = completed_task_count + 1,
                failed_task_count = failed_task_count + 1
            WHERE run_id = %s
            """,
            (run_id,),
        )
        conn.execute(
            """
            UPDATE price_runs
            SET status = CASE WHEN failed_task_count > 0 THEN 'completed_with_errors' ELSE 'completed' END,
                completed_at = NOW()
            WHERE run_id = %s
              AND completed_task_count >= queued_task_count
              AND queued_task_count > 0
            """,
            (run_id,),
        )


def get_page_cache(store_id: str, source_url: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM page_cache WHERE store_id = %s AND source_url = %s",
            (store_id, source_url),
        ).fetchone()
        return dict(row) if row else None


def upsert_page_cache(
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
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO page_cache (
                store_id, source_url, etag, last_modified, content_hash,
                http_status, content_type, parsed_payload, fetched_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (store_id, source_url) DO UPDATE SET
                etag = EXCLUDED.etag,
                last_modified = EXCLUDED.last_modified,
                content_hash = EXCLUDED.content_hash,
                http_status = EXCLUDED.http_status,
                content_type = EXCLUDED.content_type,
                parsed_payload = EXCLUDED.parsed_payload,
                fetched_at = NOW()
            """,
            (
                store_id,
                source_url,
                etag,
                last_modified,
                content_hash,
                http_status,
                content_type,
                Jsonb(parsed_payload) if parsed_payload is not None else None,
            ),
        )


def update_mapping_direct_url(
    mapping_id: str,
    direct_url: str,
    title: str | None,
    score: float,
    *,
    prefer_for_scrape: bool = False,
) -> None:
    normalize_url(direct_url)  # validates/normalizes syntax before storage
    with transaction() as conn:
        conn.execute(
            """
            UPDATE listings
            SET direct_product_url = %s,
                title_as_seen = COALESCE(NULLIF(%s, ''), title_as_seen),
                match_method = 'automatic_discovery',
                match_confidence = CASE
                    WHEN %s >= 80 THEN 'عالية'
                    WHEN %s >= 55 THEN 'متوسطة'
                    ELSE 'منخفضة'
                END,
                metadata = metadata || jsonb_build_object('prefer_direct_scrape', %s),
                last_discovered_at = NOW(),
                review_status = CASE WHEN %s >= 55 THEN 'تلقائي' ELSE 'تحتاج مراجعة' END,
                updated_at = NOW()
            WHERE mapping_id = %s
            """,
            (
                direct_url,
                title or "",
                score,
                score,
                prefer_for_scrape,
                score,
                mapping_id,
            ),
        )


def _numeric_equal(left: Any, right: Any, places: str = "0.0001") -> bool:
    if left is None or right is None:
        return left is right
    try:
        return Decimal(str(left)).quantize(Decimal(places)) == Decimal(str(right)).quantize(Decimal(places))
    except Exception:
        return left == right


def _comparison_value(value: Any) -> Any:
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    return value


def _change_type(old: dict[str, Any] | None, new: dict[str, Any]) -> str | None:
    if not old or old.get("cash_price") is None:
        return "first_seen"
    if not _numeric_equal(old.get("cash_price"), new.get("cash_price")):
        return "price_changed"
    if not _numeric_equal(old.get("shipping_cost"), new.get("shipping_cost")):
        return "shipping_changed"
    if old.get("availability") != new.get("availability"):
        return "availability_changed"
    warranty_old = (
        old.get("warranty_type"),
        old.get("warranty_provider"),
        _comparison_value(old.get("warranty_months")),
    )
    warranty_new = (
        new.get("warranty_type"),
        new.get("warranty_provider"),
        _comparison_value(new.get("warranty_months")),
    )
    if warranty_old != warranty_new:
        return "warranty_changed"
    return None


def upsert_cash_offer(
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
    if free_shipping:
        total = result.cash_price
    elif result.shipping_cost is not None:
        total = result.cash_price + result.shipping_cost
    else:
        total = None
    discount_amount = max(result.old_price - result.cash_price, 0) if result.old_price is not None else None
    discount_percent = (
        discount_amount / result.old_price
        if discount_amount is not None and result.old_price not in (None, 0)
        else None
    )
    new_values = {
        "cash_price": result.cash_price,
        "old_price": result.old_price,
        "shipping_cost": result.shipping_cost,
        "total_price": total,
        "free_shipping": free_shipping,
        "availability": result.availability,
        "warranty_type": result.warranty_type,
        "warranty_provider": result.warranty_provider,
        "warranty_months": result.warranty_months,
    }

    with transaction() as conn:
        old_row = conn.execute(
            "SELECT * FROM current_offers WHERE offer_key = %s FOR UPDATE",
            (target.offer_key,),
        ).fetchone()
        old = dict(old_row) if old_row else None
        old_cash_price = float(old["cash_price"]) if old and old.get("cash_price") is not None else None
        if old_cash_price and old_cash_price > 0:
            settings = get_settings()
            ratio = float(result.cash_price) / old_cash_price
            if ratio < settings.min_price_ratio_to_previous or ratio > settings.max_price_ratio_to_previous:
                raise PriceAnomalyError(
                    f"New price {result.cash_price:.2f} is {ratio:.3f}x the previous "
                    f"price {old_cash_price:.2f}; manual review required"
                )
        change_type = _change_type(old, new_values)

        conn.execute(
            """
            INSERT INTO current_offers (
                offer_id, offer_key, mapping_id, variant_id, store_id, seller_id, seller_name,
                currency, cash_price, old_price, discount_amount, discount_percent,
                shipping_cost, total_price, free_shipping, availability, available_quantity,
                delivery_region, delivery_text, min_delivery_days, max_delivery_days,
                warranty_type, warranty_provider, warranty_months,
                source_method, source_url, last_checked_at, last_success_at,
                freshness_status, extraction_status, consecutive_failures,
                connector_version, last_run_id, active, review_status, raw_payload, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, NOW(), NOW(),
                'fresh', 'success', 0,
                %s, %s, TRUE, 'تلقائي', %s, NOW()
            )
            ON CONFLICT (offer_key) DO UPDATE SET
                seller_id = EXCLUDED.seller_id,
                seller_name = COALESCE(EXCLUDED.seller_name, current_offers.seller_name),
                currency = EXCLUDED.currency,
                cash_price = EXCLUDED.cash_price,
                old_price = EXCLUDED.old_price,
                discount_amount = EXCLUDED.discount_amount,
                discount_percent = EXCLUDED.discount_percent,
                shipping_cost = EXCLUDED.shipping_cost,
                total_price = EXCLUDED.total_price,
                free_shipping = EXCLUDED.free_shipping,
                availability = EXCLUDED.availability,
                available_quantity = EXCLUDED.available_quantity,
                delivery_text = EXCLUDED.delivery_text,
                min_delivery_days = EXCLUDED.min_delivery_days,
                max_delivery_days = EXCLUDED.max_delivery_days,
                warranty_type = EXCLUDED.warranty_type,
                warranty_provider = EXCLUDED.warranty_provider,
                warranty_months = EXCLUDED.warranty_months,
                source_method = EXCLUDED.source_method,
                source_url = EXCLUDED.source_url,
                last_checked_at = NOW(),
                last_success_at = NOW(),
                freshness_status = 'fresh',
                extraction_status = 'success',
                consecutive_failures = 0,
                connector_version = EXCLUDED.connector_version,
                last_run_id = EXCLUDED.last_run_id,
                active = TRUE,
                review_status = CASE
                    WHEN current_offers.review_status = 'مرفوض' THEN current_offers.review_status
                    ELSE 'تلقائي'
                END,
                raw_payload = EXCLUDED.raw_payload,
                updated_at = NOW()
            """,
            (
                target.offer_id,
                target.offer_key,
                target.mapping_id,
                target.variant_id,
                target.store_id,
                target.seller_id,
                result.seller_name or target.seller_name,
                result.currency,
                result.cash_price,
                result.old_price,
                discount_amount,
                discount_percent,
                result.shipping_cost,
                total,
                free_shipping,
                result.availability,
                result.available_quantity,
                get_settings().default_delivery_region,
                result.delivery_text,
                result.min_delivery_days,
                result.max_delivery_days,
                result.warranty_type,
                result.warranty_provider,
                result.warranty_months,
                result.source_method,
                result.source_url,
                connector_version,
                run_id,
                Jsonb(_jsonable(result)),
            ),
        )

        if result.image_url and result.image_url.startswith("https://"):
            conn.execute(
                """
                UPDATE variants
                SET image_url = COALESCE(NULLIF(image_url, ''), %s),
                    updated_at = NOW()
                WHERE variant_id = %s
                """,
                (result.image_url, target.variant_id),
            )

        if change_type:
            conn.execute(
                """
                INSERT INTO offer_observations (
                    offer_key, variant_id, store_id, seller_id, observed_at, run_id,
                    change_type, cash_price, old_price, shipping_cost, total_price,
                    availability, warranty_type, warranty_provider, warranty_months, snapshot
                )
                VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    target.offer_key,
                    target.variant_id,
                    target.store_id,
                    target.seller_id,
                    run_id,
                    change_type,
                    result.cash_price,
                    result.old_price,
                    result.shipping_cost,
                    total,
                    result.availability,
                    result.warranty_type,
                    result.warranty_provider,
                    result.warranty_months,
                    Jsonb(_jsonable(result)),
                ),
            )
        conn.execute(
            """
            UPDATE installment_tasks
            SET source_url = %s, last_checked_at = NOW(), updated_at = NOW()
            WHERE cash_offer_key = %s
            """,
            (result.source_url, target.offer_key),
        )
        return bool(change_type)


def mark_mapping_failure(
    target: MappingTarget,
    *,
    run_id: str,
    error_code: str,
    error_message: str,
) -> None:
    settings = get_settings()
    with transaction() as conn:
        conn.execute(
            """
            UPDATE current_offers
            SET last_checked_at = NOW(),
                extraction_status = %s,
                consecutive_failures = consecutive_failures + 1,
                last_run_id = %s,
                freshness_status = CASE
                    WHEN last_success_at IS NULL THEN 'unseen'
                    WHEN last_success_at >= NOW() - (%s * INTERVAL '1 minute') THEN 'fresh'
                    WHEN last_success_at >= NOW() - (%s * INTERVAL '1 minute') THEN 'late'
                    ELSE 'stale'
                END,
                review_notes = LEFT(COALESCE(review_notes || E'\n', '') || %s, 4000),
                updated_at = NOW()
            WHERE offer_key = %s
            """,
            (
                error_code,
                run_id,
                settings.freshness_minutes,
                settings.stale_after_minutes,
                error_message,
                target.offer_key,
            ),
        )
        conn.execute(
            """
            UPDATE installment_tasks
            SET last_checked_at = NOW(),
                status = %s,
                consecutive_failures = consecutive_failures + 1,
                notes = LEFT(COALESCE(notes || E'\n', '') || %s, 4000),
                updated_at = NOW()
            WHERE cash_offer_key = %s
            """,
            (error_code, error_message, target.offer_key),
        )


def _plan_identity(target: MappingTarget, plan: InstallmentPlanExtract) -> tuple[str, str]:
    provider = normalize_text(plan.provider_name or plan.bank_or_card or "unknown")
    name = normalize_text(plan.plan_name or "")
    source_signature = ""
    if provider == "unknown" and not name:
        source_text = normalize_text(str((plan.raw or {}).get("source_segment") or ""))
        source_signature = re.sub(r"\d+(?:[.,]\d+)?", "#", source_text)[:180]
    # Keep the identity stable when the amount changes so that a scheduled price
    # change becomes a history event instead of creating an unrelated new plan.
    identity = "|".join(
        [
            target.offer_key,
            provider,
            normalize_text(plan.bank_or_card),
            str(plan.months or 0),
            normalize_text(plan.payment_frequency),
            name,
            source_signature,
            "start" if plan.starting_from_only else "fixed",
        ]
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:18].upper()
    return f"INST-{digest}", identity


def upsert_installment_plans(
    target: MappingTarget,
    plans: list[InstallmentPlanExtract],
    *,
    run_id: str,
    connector_version: str,
) -> int:
    settings = get_settings()
    if not plans:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE installment_tasks
                SET last_checked_at = NOW(), status = 'no_plan_extracted', updated_at = NOW()
                WHERE cash_offer_key = %s
                """,
                (target.offer_key,),
            )
            conn.execute(
                """
                UPDATE current_installment_offers
                SET last_checked_at = NOW(),
                    extraction_status = 'not_seen_in_latest_scan',
                    consecutive_failures = consecutive_failures + 1,
                    freshness_status = CASE
                        WHEN last_success_at IS NULL THEN 'unseen'
                        WHEN last_success_at >= NOW() - (%s * INTERVAL '1 minute') THEN 'fresh'
                        WHEN last_success_at >= NOW() - (%s * INTERVAL '1 minute') THEN 'late'
                        ELSE 'stale'
                    END,
                    active = CASE WHEN ends_at IS NOT NULL AND ends_at < NOW() THEN FALSE ELSE active END,
                    updated_at = NOW()
                WHERE cash_offer_key = %s AND active = TRUE
                """,
                (
                    settings.freshness_minutes,
                    settings.stale_after_minutes,
                    target.offer_key,
                ),
            )
        return 0

    active_keys: list[str] = []
    changed_count = 0
    with transaction() as conn:
        for plan in plans:
            plan_id, plan_key = _plan_identity(target, plan)
            active_keys.append(plan_key)
            old_row = conn.execute(
                "SELECT * FROM current_installment_offers WHERE plan_key = %s FOR UPDATE",
                (plan_key,),
            ).fetchone()
            old = dict(old_row) if old_row else None
            snapshot = _jsonable(plan)
            comparable = tuple(
                _comparison_value(value)
                for value in (
                    plan.months,
                    plan.periodic_payment,
                    plan.down_payment,
                    plan.admin_fees,
                    plan.total_published,
                    plan.total_calculated,
                    plan.interest_free,
                    plan.starting_from_only,
                    plan.ends_at,
                )
            )
            old_comparable = None
            if old:
                old_comparable = tuple(
                    _comparison_value(value)
                    for value in (
                        old.get("months"),
                        old.get("periodic_payment"),
                        old.get("down_payment"),
                        old.get("admin_fees"),
                        old.get("total_published"),
                        old.get("total_calculated"),
                        old.get("interest_free"),
                        old.get("starting_from_only"),
                        old.get("ends_at"),
                    )
                )
            change_type = (
                "first_seen" if not old else ("plan_changed" if comparable != old_comparable else None)
            )

            conn.execute(
                """
                INSERT INTO current_installment_offers (
                    plan_id, plan_key, cash_offer_key, variant_id, store_id, seller_id, seller_name,
                    provider_name, provider_type, bank_or_card, plan_name, months, payment_frequency,
                    periodic_payment, first_payment, down_payment, down_payment_percent,
                    admin_fees, processing_fees, insurance_fees, other_fees,
                    total_published, total_calculated, cash_price_at_observation,
                    financing_cost, financing_markup_percent, apr, interest_type, interest_free,
                    grace_months, minimum_purchase, maximum_financing, eligibility, required_card,
                    customer_type, new_customers_only, geography, starts_at, ends_at, promo_code,
                    terms_url, source_url, starting_from_only, completeness,
                    last_checked_at, last_success_at, freshness_status, extraction_status,
                    consecutive_failures, connector_version, last_run_id, active,
                    review_status, raw_payload, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    NOW(), NOW(), 'fresh', 'success',
                    0, %s, %s, TRUE,
                    'تلقائي', %s, NOW()
                )
                ON CONFLICT (plan_key) DO UPDATE SET
                    provider_name = EXCLUDED.provider_name,
                    provider_type = EXCLUDED.provider_type,
                    bank_or_card = EXCLUDED.bank_or_card,
                    plan_name = EXCLUDED.plan_name,
                    months = EXCLUDED.months,
                    payment_frequency = EXCLUDED.payment_frequency,
                    periodic_payment = EXCLUDED.periodic_payment,
                    first_payment = EXCLUDED.first_payment,
                    down_payment = EXCLUDED.down_payment,
                    down_payment_percent = EXCLUDED.down_payment_percent,
                    admin_fees = EXCLUDED.admin_fees,
                    processing_fees = EXCLUDED.processing_fees,
                    insurance_fees = EXCLUDED.insurance_fees,
                    other_fees = EXCLUDED.other_fees,
                    total_published = EXCLUDED.total_published,
                    total_calculated = EXCLUDED.total_calculated,
                    cash_price_at_observation = EXCLUDED.cash_price_at_observation,
                    financing_cost = EXCLUDED.financing_cost,
                    financing_markup_percent = EXCLUDED.financing_markup_percent,
                    apr = EXCLUDED.apr,
                    interest_type = EXCLUDED.interest_type,
                    interest_free = EXCLUDED.interest_free,
                    grace_months = EXCLUDED.grace_months,
                    minimum_purchase = EXCLUDED.minimum_purchase,
                    maximum_financing = EXCLUDED.maximum_financing,
                    eligibility = EXCLUDED.eligibility,
                    required_card = EXCLUDED.required_card,
                    customer_type = EXCLUDED.customer_type,
                    new_customers_only = EXCLUDED.new_customers_only,
                    geography = EXCLUDED.geography,
                    starts_at = EXCLUDED.starts_at,
                    ends_at = EXCLUDED.ends_at,
                    promo_code = EXCLUDED.promo_code,
                    terms_url = EXCLUDED.terms_url,
                    source_url = EXCLUDED.source_url,
                    starting_from_only = EXCLUDED.starting_from_only,
                    completeness = EXCLUDED.completeness,
                    last_checked_at = NOW(),
                    last_success_at = NOW(),
                    freshness_status = 'fresh',
                    extraction_status = 'success',
                    consecutive_failures = 0,
                    connector_version = EXCLUDED.connector_version,
                    last_run_id = EXCLUDED.last_run_id,
                    active = TRUE,
                    review_status = 'تلقائي',
                    raw_payload = EXCLUDED.raw_payload,
                    updated_at = NOW()
                """,
                (
                    plan_id,
                    plan_key,
                    target.offer_key,
                    target.variant_id,
                    target.store_id,
                    target.seller_id,
                    target.seller_name,
                    plan.provider_name,
                    plan.provider_type,
                    plan.bank_or_card,
                    plan.plan_name,
                    plan.months,
                    plan.payment_frequency,
                    plan.periodic_payment,
                    plan.first_payment,
                    plan.down_payment,
                    plan.down_payment_percent,
                    plan.admin_fees,
                    plan.processing_fees,
                    plan.insurance_fees,
                    plan.other_fees,
                    plan.total_published,
                    plan.total_calculated,
                    plan.cash_price_at_observation,
                    plan.financing_cost,
                    plan.financing_markup_percent,
                    plan.apr,
                    plan.interest_type,
                    plan.interest_free,
                    plan.grace_months,
                    plan.minimum_purchase,
                    plan.maximum_financing,
                    plan.eligibility,
                    plan.required_card,
                    plan.customer_type,
                    plan.new_customers_only,
                    plan.geography,
                    plan.starts_at,
                    plan.ends_at,
                    plan.promo_code,
                    plan.terms_url,
                    plan.source_url,
                    plan.starting_from_only,
                    plan.completeness,
                    connector_version,
                    run_id,
                    Jsonb(snapshot),
                ),
            )

            if change_type:
                changed_count += 1
                conn.execute(
                    """
                    INSERT INTO installment_observations (
                        plan_key, cash_offer_key, variant_id, store_id, observed_at,
                        run_id, change_type, snapshot
                    )
                    VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s)
                    """,
                    (
                        plan_key,
                        target.offer_key,
                        target.variant_id,
                        target.store_id,
                        run_id,
                        change_type,
                        Jsonb(snapshot),
                    ),
                )

        conn.execute(
            """
            UPDATE current_installment_offers
            SET last_checked_at = NOW(),
                extraction_status = 'not_seen_in_latest_scan',
                consecutive_failures = consecutive_failures + 1,
                freshness_status = CASE
                    WHEN last_success_at IS NULL THEN 'unseen'
                    WHEN last_success_at >= NOW() - (%s * INTERVAL '1 minute') THEN 'fresh'
                    WHEN last_success_at >= NOW() - (%s * INTERVAL '1 minute') THEN 'late'
                    ELSE 'stale'
                END,
                active = CASE WHEN ends_at IS NOT NULL AND ends_at < NOW() THEN FALSE ELSE active END,
                updated_at = NOW()
            WHERE cash_offer_key = %s
              AND active = TRUE
              AND NOT (plan_key = ANY(%s))
            """,
            (
                settings.freshness_minutes,
                settings.stale_after_minutes,
                target.offer_key,
                active_keys,
            ),
        )
        conn.execute(
            """
            UPDATE installment_tasks
            SET last_checked_at = NOW(),
                last_success_at = NOW(),
                status = 'plans_found',
                consecutive_failures = 0,
                updated_at = NOW()
            WHERE cash_offer_key = %s
            """,
            (target.offer_key,),
        )
    return changed_count


def sync_catalog_discovery_sources() -> int:
    """Create a safe root source for every retailer and catalog-only brand."""

    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT s.store_id, s.base_url, s.priority, c.config
            FROM stores s
            JOIN connector_configs c ON c.store_id = s.store_id
            WHERE (
                    (s.active = TRUE AND c.enabled = TRUE)
                    OR s.registry_status = 'نشط/كتالوج فقط'
                  )
              AND NULLIF(s.base_url, '') IS NOT NULL
            ORDER BY s.store_id
            """
        ).fetchall()
        written = 0
        active_source_ids: list[str] = []
        for row in rows:
            configured = list((row.get("config") or {}).get("discoverySources") or [])
            urls = [row["base_url"], *configured]
            for source_url in dict.fromkeys(str(value).strip() for value in urls if value):
                try:
                    normalized = normalize_url(source_url)
                except Exception:
                    continue
                digest = hashlib.sha256(f"{row['store_id']}|{normalized}".encode()).hexdigest()[:20].upper()
                source_id = f"SRC-{digest}"
                active_source_ids.append(source_id)
                conn.execute(
                    """
                    INSERT INTO discovery_sources (
                        source_id, store_id, source_url, normalized_url,
                        source_type, enabled, priority
                    )
                    VALUES (%s, %s, %s, %s, 'auto', TRUE, %s)
                    ON CONFLICT (source_id) DO UPDATE SET
                        source_url = EXCLUDED.source_url,
                        normalized_url = EXCLUDED.normalized_url,
                        priority = EXCLUDED.priority,
                        enabled = TRUE,
                        updated_at = NOW()
                    """,
                    (source_id, row["store_id"], source_url, normalized, row.get("priority")),
                )
                written += 1
        if active_source_ids:
            conn.execute(
                """
                UPDATE discovery_sources
                SET enabled = FALSE, updated_at = NOW()
                WHERE NOT (source_id = ANY(%s))
                """,
                (active_source_ids,),
            )
        return written


def create_or_get_catalog_discovery_run(
    run_slot: datetime,
    trigger_source: str,
    *,
    full_coverage: bool = False,
) -> tuple[dict[str, Any], bool]:
    with transaction() as conn:
        incomplete_active_run_ids: list[str] = []
        if full_coverage:
            # Serialize the active-run check and insert across scheduler,
            # deployment, and manual triggers.  Minute-based idempotency alone
            # cannot prevent two full scans started in different minutes.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("sa3arly:full-catalog-discovery",),
            )
            expected = conn.execute(
                """
                SELECT COUNT(DISTINCT src.store_id) AS total
                FROM discovery_sources src
                JOIN stores s ON s.store_id = src.store_id
                JOIN connector_configs c ON c.store_id = src.store_id
                WHERE src.enabled = TRUE
                  AND (
                        (s.active = TRUE AND c.enabled = TRUE)
                        OR s.registry_status = 'نشط/كتالوج فقط'
                  )
                """
            ).fetchone()
            expected_store_count = int((expected or {}).get("total") or 0)
            active = conn.execute(
                """
                SELECT *
                FROM discovery_runs
                WHERE status IN ('created', 'enqueuing', 'queued', 'running')
                  AND metadata ->> 'full_coverage' = 'true'
                  AND run_slot <> %s
                  AND NOT (metadata ? 'superseded_by_run_id')
                ORDER BY completed_task_count DESC, created_at
                LIMIT 1
                """,
                (run_slot,),
            ).fetchone()
            if active:
                active_store_count = max(
                    int(active.get("source_count") or 0),
                    int(active.get("queued_task_count") or 0),
                )
                if active_store_count >= expected_store_count:
                    value = dict(active)
                    value["_overlap_active"] = True
                    return value, False
                incomplete_active_run_ids.append(str(active["run_id"]))
        created = conn.execute(
            """
            INSERT INTO discovery_runs (
                run_slot, trigger_source, status, metadata
            )
            VALUES (%s, %s, 'created', %s)
            ON CONFLICT (run_slot) DO NOTHING
            RETURNING *
            """,
            (
                run_slot,
                trigger_source,
                Jsonb({"full_coverage": full_coverage}),
            ),
        ).fetchone()
        if created:
            if incomplete_active_run_ids:
                replacement_id = str(created["run_id"])
                conn.execute(
                    """
                    UPDATE discovery_tasks
                    SET status = 'failed',
                        completed_at = NOW(),
                        updated_at = NOW(),
                        error_code = COALESCE(
                            error_code,
                            'superseded_incomplete_registry_run'
                        ),
                        error_message = COALESCE(
                            error_message,
                            'Partial registry run replaced after store growth'
                        )
                    WHERE run_id = ANY(%s::uuid[])
                      AND status NOT IN ('success', 'failed')
                    """,
                    (incomplete_active_run_ids,),
                )
                for incomplete_id in incomplete_active_run_ids:
                    conn.execute(
                        """
                        UPDATE discovery_runs
                        SET metadata = metadata || %s, updated_at = NOW()
                        WHERE run_id = %s
                        """,
                        (
                            Jsonb(
                                {
                                    "superseded_by_run_id": replacement_id,
                                    "superseded_reason": "registry_growth",
                                }
                            ),
                            incomplete_id,
                        ),
                    )
                    _recalculate_catalog_run(conn, incomplete_id)
            return dict(created), True
        existing = conn.execute(
            "SELECT * FROM discovery_runs WHERE run_slot = %s",
            (run_slot,),
        ).fetchone()
        if not existing:
            raise RuntimeError("Could not create or load catalog discovery run")
        return dict(existing), False


def reconcile_stale_catalog_discovery_runs(stale_after_minutes: int) -> int:
    """Fail only tasks whose recoverable delivery generations are exhausted."""

    with transaction() as conn:
        stale = conn.execute(
            """
            UPDATE discovery_tasks
            SET status = 'failed',
                completed_at = NOW(),
                updated_at = NOW(),
                error_code = COALESCE(error_code, 'stale_task_reconciled'),
                error_message = COALESCE(
                    error_message,
                    'Catalog task exceeded the reconciliation age without a terminal result'
                )
            WHERE status NOT IN ('success', 'failed')
              AND recovery_count >= 3
              AND COALESCE(started_at, scheduled_for, updated_at) <
                  NOW() - (%s * INTERVAL '1 minute')
            RETURNING run_id
            """,
            (max(int(stale_after_minutes), 30),),
        ).fetchall()
        run_ids = sorted({str(row["run_id"]) for row in stale})
        for run_id in run_ids:
            _recalculate_catalog_run(conn, run_id)
        return len(stale)


def reconcile_overlapping_catalog_discovery_runs() -> int:
    """Supersede duplicate work while retaining retries for repaired internal failures."""

    with transaction() as conn:
        active_runs = conn.execute(
            """
            SELECT run_id::text, completed_task_count, created_at
            FROM discovery_runs
            WHERE status IN ('created', 'enqueuing', 'queued', 'running')
              AND metadata ->> 'full_coverage' = 'true'
              AND NOT (metadata ? 'superseded_by_run_id')
            ORDER BY COALESCE(completed_task_count, 0) DESC, created_at
            """
        ).fetchall()
        if len(active_runs) <= 1:
            return 0
        keeper_id = str(active_runs[0]["run_id"])
        duplicate_ids = [str(row["run_id"]) for row in active_runs[1:]]
        superseded = conn.execute(
            """
            UPDATE discovery_tasks AS duplicate_task
            SET status = 'failed',
                completed_at = NOW(),
                updated_at = NOW(),
                error_code = COALESCE(error_code, 'superseded_duplicate_run'),
                error_message = COALESCE(
                    error_message,
                    'Duplicate full catalog run superseded by a more advanced active run'
                )
            WHERE duplicate_task.run_id = ANY(%s::uuid[])
              AND duplicate_task.status NOT IN ('success', 'failed')
              AND NOT EXISTS (
                  SELECT 1
                  FROM discovery_tasks AS keeper_task
                  WHERE keeper_task.run_id = %s::uuid
                    AND keeper_task.store_id = duplicate_task.store_id
                    AND keeper_task.status = 'failed'
                    AND keeper_task.error_code LIKE 'internal_%%'
              )
            RETURNING duplicate_task.run_id::text
            """,
            (duplicate_ids, keeper_id),
        ).fetchall()
        for run_id in duplicate_ids:
            conn.execute(
                """
                UPDATE discovery_runs
                SET metadata = metadata || %s, updated_at = NOW()
                WHERE run_id = %s
                """,
                (Jsonb({"superseded_by_run_id": keeper_id}), run_id),
            )
            _recalculate_catalog_run(conn, run_id)
        return len(superseded)


def load_due_catalog_discovery_sources(
    *,
    limit: int,
    include_not_due: bool = False,
) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (src.store_id)
                src.source_id, src.store_id, src.source_url, src.source_type,
                s.name AS store_name, s.priority,
                c.allowed_hosts, c.version AS connector_version,
                c.config AS connector_config, c.requests_per_minute,
                c.respect_robots, c.browser_required
            FROM discovery_sources src
            JOIN stores s ON s.store_id = src.store_id
            JOIN connector_configs c ON c.store_id = src.store_id
            WHERE src.enabled = TRUE
              AND (
                    (s.active = TRUE AND c.enabled = TRUE)
                    OR s.registry_status = 'نشط/كتالوج فقط'
              )
              AND (CAST(%s AS BOOLEAN) OR src.next_scan_at <= NOW())
            ORDER BY src.store_id, src.next_scan_at, src.source_id
            """,
            (include_not_due,),
        ).fetchall()
        values = [dict(row) for row in rows]
        priority_order = {"P0": 0, "P1": 1, "P2": 2, "مراقبة": 3}
        values.sort(
            key=lambda row: (
                priority_order.get(str(row.get("priority") or ""), 9),
                str(row.get("store_id") or ""),
            )
        )
        return values[: max(1, min(int(limit), 500))]


def select_catalog_hydration_candidates(
    store_id: str,
    candidates: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Prioritize brand-new URLs, then the oldest links without product data."""

    limit = max(0, min(int(limit), 100))
    if not candidates or limit == 0:
        return []
    urls = [str(item.get("normalized_url") or "") for item in candidates]
    urls = [url for url in urls if url]
    with connection() as conn:
        known_rows = conn.execute(
            """
            SELECT normalized_url, observed_price, source_method, last_seen_at
            FROM discovery_candidates
            WHERE store_id = %s AND normalized_url = ANY(%s)
            """,
            (store_id, urls),
        ).fetchall()
    known = {str(row["normalized_url"]): dict(row) for row in known_rows}

    def priority(item: dict[str, Any]) -> tuple[int, datetime, str]:
        url = str(item.get("normalized_url") or "")
        previous = known.get(url)
        if previous is None:
            return (0, datetime.min.replace(tzinfo=UTC), url)
        weak = previous.get("observed_price") is None or str(previous.get("source_method") or "").startswith(
            "sitemap"
        )
        return (
            1 if weak else 2,
            previous.get("last_seen_at") or datetime.min.replace(tzinfo=UTC),
            url,
        )

    ranked = sorted(candidates, key=priority)
    return [item for item in ranked if priority(item)[0] < 2][:limit]


def count_due_catalog_discovery_sources() -> int:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT src.store_id) AS total
            FROM discovery_sources src
            JOIN stores s ON s.store_id = src.store_id
            JOIN connector_configs c ON c.store_id = src.store_id
            WHERE src.enabled = TRUE
              AND (
                    (s.active = TRUE AND c.enabled = TRUE)
                    OR s.registry_status = 'نشط/كتالوج فقط'
              )
              AND src.next_scan_at <= NOW()
            """
        ).fetchone()
        return int(row["total"] or 0)


def mark_catalog_discovery_run_enqueuing(
    run_id: str,
    *,
    source_count: int,
    metadata: dict[str, Any],
) -> None:
    with transaction() as conn:
        conn.execute(
            """
            UPDATE discovery_runs
            SET status = 'enqueuing', source_count = %s,
                queued_task_count = %s, metadata = %s,
                started_at = COALESCE(started_at, NOW()), updated_at = NOW()
            WHERE run_id = %s
            """,
            (source_count, source_count, Jsonb(metadata), run_id),
        )


def mark_catalog_discovery_run_enqueue_complete(run_id: str) -> None:
    with transaction() as conn:
        conn.execute(
            """
            UPDATE discovery_runs
            SET status = CASE WHEN queued_task_count = 0 THEN 'success' ELSE 'queued' END,
                completed_at = CASE WHEN queued_task_count = 0 THEN NOW() ELSE completed_at END,
                updated_at = NOW()
            WHERE run_id = %s
            """,
            (run_id,),
        )


def mark_catalog_discovery_run_enqueue_failed(
    run_id: str,
    message: str,
    *,
    successfully_queued: int,
    planned_tasks: int,
) -> None:
    with transaction() as conn:
        conn.execute(
            """
            UPDATE discovery_runs
            SET status = 'enqueue_failed',
                metadata = metadata || %s,
                updated_at = NOW()
            WHERE run_id = %s
            """,
            (
                Jsonb(
                    {
                        "enqueue_error": message[:2000],
                        "successfully_queued": successfully_queued,
                        "planned_tasks": planned_tasks,
                    }
                ),
                run_id,
            ),
        )


def register_catalog_discovery_task(payload: CatalogDiscoveryTaskPayload) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO discovery_tasks (
                task_id, run_id, source_id, store_id, source_url,
                status, scheduled_for, delivery_generation, recovery_after
            )
            VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s,
                    %s + INTERVAL '30 minutes')
            ON CONFLICT (task_id) DO NOTHING
            """,
            (
                payload.task_id,
                payload.run_id,
                payload.source_id,
                payload.store_id,
                payload.source_url,
                payload.scheduled_for,
                payload.delivery_generation,
                payload.scheduled_for,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_deliveries (task_id, generation, status)
            VALUES (%s, %s, 'prepared')
            ON CONFLICT (task_id, generation) DO NOTHING
            """,
            (payload.task_id, payload.delivery_generation),
        )


def mark_catalog_task_enqueued(
    task_id: str,
    *,
    delivery_generation: int,
    queue_task_name: str,
) -> None:
    """Persist the physical queue delivery without regressing inline terminal work."""

    with transaction() as conn:
        conn.execute(
            """
            UPDATE discovery_tasks
            SET queue_task_name = %s,
                last_enqueued_at = NOW(),
                recovery_after = GREATEST(
                    scheduled_for + INTERVAL '30 minutes',
                    NOW() + INTERVAL '30 minutes'
                ),
                updated_at = NOW()
            WHERE task_id = %s AND delivery_generation = %s
            """,
            (queue_task_name, task_id, delivery_generation),
        )
        conn.execute(
            """
            UPDATE task_deliveries
            SET queue_task_name = %s,
                status = CASE
                    WHEN status IN ('succeeded', 'failed') THEN status
                    ELSE 'enqueued'
                END,
                enqueued_at = COALESCE(enqueued_at, NOW()), updated_at = NOW()
            WHERE task_id = %s AND generation = %s
            """,
            (queue_task_name, task_id, delivery_generation),
        )


def start_catalog_discovery_task(
    task_id: str,
    *,
    delivery_generation: int = 1,
    allow_reclaim_running: bool = False,
) -> str:
    with transaction() as conn:
        row = conn.execute(
            """
            SELECT status, delivery_generation
            FROM discovery_tasks WHERE task_id = %s FOR UPDATE
            """,
            (task_id,),
        ).fetchone()
        if not row:
            return "missing"
        if int(row["delivery_generation"] or 1) != int(delivery_generation):
            conn.execute(
                """
                UPDATE task_deliveries
                SET status = 'superseded', completed_at = NOW(), updated_at = NOW()
                WHERE task_id = %s AND generation = %s
                  AND status NOT IN ('succeeded', 'failed')
                """,
                (task_id, delivery_generation),
            )
            return "superseded"
        status = str(row["status"])
        if status in {"success", "failed"}:
            return "terminal"
        if status == "running" and not allow_reclaim_running:
            return "running"
        conn.execute(
            """
            UPDATE discovery_tasks
            SET status = 'running', attempt_count = attempt_count + 1,
                started_at = COALESCE(started_at, NOW()),
                last_heartbeat_at = NOW(),
                recovery_after = NOW() + INTERVAL '30 minutes',
                updated_at = NOW()
            WHERE task_id = %s
            """,
            (task_id,),
        )
        conn.execute(
            """
            UPDATE task_deliveries
            SET status = 'dispatched', dispatch_count = dispatch_count + 1,
                dispatched_at = COALESCE(dispatched_at, NOW()), updated_at = NOW()
            WHERE task_id = %s AND generation = %s
            """,
            (task_id, delivery_generation),
        )
        return "claimed"


def _recalculate_catalog_run(conn, run_id: str) -> None:
    totals = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE status IN ('success', 'failed')) AS completed,
            COUNT(*) FILTER (WHERE status = 'success') AS successful,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            COALESCE(SUM(candidates_seen), 0) AS candidates_seen,
            COALESCE(SUM(candidates_new), 0) AS candidates_new,
            COALESCE(SUM(mappings_created), 0) AS mappings_created,
            COALESCE(SUM(provisional_products), 0) AS provisional_products,
            COALESCE(SUM(verified_products), 0) AS verified_products
        FROM discovery_tasks
        WHERE run_id = %s
        """,
        (run_id,),
    ).fetchone()
    run = conn.execute(
        "SELECT queued_task_count FROM discovery_runs WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    completed = int(totals["completed"] or 0)
    queued = int((run or {}).get("queued_task_count") or 0)
    if queued and completed >= queued:
        successful = int(totals["successful"] or 0)
        failed = int(totals["failed"] or 0)
        if successful == 0:
            status = "failed"
        elif failed > 0:
            status = "completed_with_errors"
        else:
            status = "success"
        completed_at = datetime.now().astimezone()
    else:
        status = "running"
        completed_at = None
    conn.execute(
        """
        UPDATE discovery_runs
        SET status = %s, completed_task_count = %s,
            successful_task_count = %s, failed_task_count = %s,
            candidates_seen = %s, candidates_new = %s,
            mappings_created = %s, provisional_products = %s,
            verified_products = %s,
            completed_at = COALESCE(%s, completed_at), updated_at = NOW()
        WHERE run_id = %s
        """,
        (
            status,
            completed,
            int(totals["successful"] or 0),
            int(totals["failed"] or 0),
            int(totals["candidates_seen"] or 0),
            int(totals["candidates_new"] or 0),
            int(totals["mappings_created"] or 0),
            int(totals["provisional_products"] or 0),
            int(totals["verified_products"] or 0),
            completed_at,
            run_id,
        ),
    )


def finish_catalog_discovery_task(
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
    settings = get_settings()
    with transaction() as conn:
        task = conn.execute(
            """
            SELECT run_id, source_id, delivery_generation
            FROM discovery_tasks
            WHERE task_id = %s FOR UPDATE
            """,
            (task_id,),
        ).fetchone()
        if not task:
            return
        conn.execute(
            """
            UPDATE discovery_tasks
            SET status = %s, http_status = %s, response_bytes = %s,
                candidates_seen = %s, candidates_new = %s,
                mappings_created = %s, provisional_products = %s,
                verified_products = %s, error_code = %s,
                error_message = %s, metrics = %s,
                completed_at = CASE WHEN %s IN ('success', 'failed') THEN NOW() ELSE NULL END,
                updated_at = NOW()
            WHERE task_id = %s
            """,
            (
                status,
                http_status,
                response_bytes,
                candidates_seen,
                candidates_new,
                mappings_created,
                provisional_products,
                verified_products,
                error_code,
                (error_message or "")[:4000] or None,
                Jsonb(metrics or {}),
                status,
                task_id,
            ),
        )
        completed_generation = int(delivery_generation or task["delivery_generation"] or 1)
        delivery_status = {
            "success": "succeeded",
            "failed": "failed",
        }.get(status, "dispatched")
        conn.execute(
            """
            UPDATE task_deliveries
            SET status = %s,
                response_code = %s,
                error_code = %s,
                error_message = %s,
                completed_at = CASE
                    WHEN %s IN ('succeeded', 'failed') THEN NOW()
                    ELSE completed_at
                END,
                updated_at = NOW()
            WHERE task_id = %s AND generation = %s
            """,
            (
                delivery_status,
                http_status,
                error_code,
                (error_message or "")[:4000] or None,
                delivery_status,
                task_id,
                completed_generation,
            ),
        )
        retryable = status == "retryable_failed"
        successful = status == "success"
        if successful:
            next_delay_hours = settings.catalog_discovery_rescan_hours
        elif retryable:
            next_delay_hours = 1
        else:
            next_delay_hours = max(settings.catalog_discovery_rescan_hours, 168)
        conn.execute(
            """
            UPDATE discovery_sources
            SET status = %s, last_scan_at = NOW(),
                last_success_at = CASE WHEN %s THEN NOW() ELSE last_success_at END,
                next_scan_at = NOW() + (%s * INTERVAL '1 hour'),
                consecutive_failures = CASE WHEN %s THEN 0 ELSE consecutive_failures + 1 END,
                last_error_code = %s, last_error_message = %s,
                updated_at = NOW()
            WHERE source_id = %s
            """,
            (
                "active" if successful else status,
                successful,
                next_delay_hours,
                successful,
                error_code,
                (error_message or "")[:4000] or None,
                task["source_id"],
            ),
        )
        _recalculate_catalog_run(conn, str(task["run_id"]))


def promote_catalog_retry_exhausted(task_id: str) -> None:
    with connection() as conn:
        row = conn.execute(
            "SELECT error_code, error_message FROM discovery_tasks WHERE task_id = %s",
            (task_id,),
        ).fetchone()
    finish_catalog_discovery_task(
        task_id,
        status="failed",
        error_code=(row or {}).get("error_code") or "retry_exhausted",
        error_message=(row or {}).get("error_message") or "Cloud Tasks retries exhausted",
    )


def prepare_catalog_task_recoveries(
    *,
    limit: int = 50,
) -> list[CatalogDiscoveryTaskPayload]:
    """Create a new physical delivery generation for stranded logical tasks."""

    limit = max(1, min(int(limit), 500))
    payloads: list[CatalogDiscoveryTaskPayload] = []
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                t.task_id, t.run_id AS raw_run_id,
                t.run_id::text AS run_id_text, r.run_slot,
                t.source_id, t.store_id,
                src.source_url, src.source_type,
                s.name AS store_name, s.base_url,
                COALESCE(c.allowed_hosts, '{}'::text[]) AS allowed_hosts,
                COALESCE(c.version, 'catalog-generic-v1') AS connector_version,
                COALESCE(c.config, '{}'::jsonb) AS connector_config,
                COALESCE(c.requests_per_minute, 6) AS requests_per_minute,
                COALESCE(c.respect_robots, TRUE) AS respect_robots,
                COALESCE(c.browser_required, FALSE) AS browser_required,
                t.delivery_generation
            FROM discovery_tasks t
            JOIN discovery_runs r ON r.run_id = t.run_id
            JOIN discovery_sources src ON src.source_id = t.source_id
            JOIN stores s ON s.store_id = t.store_id
            LEFT JOIN connector_configs c ON c.store_id = t.store_id
            WHERE t.status NOT IN ('success', 'failed')
              AND t.recovery_after IS NOT NULL
              AND t.recovery_after <= NOW()
              AND t.recovery_count < 3
            ORDER BY t.recovery_after, t.scheduled_for, t.task_id
            FOR UPDATE OF t SKIP LOCKED
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        now = datetime.now().astimezone()
        for row in rows:
            task_id = str(row["task_id"])
            previous_generation = int(row["delivery_generation"] or 1)
            next_generation = previous_generation + 1
            conn.execute(
                """
                UPDATE task_deliveries
                SET status = 'lost', completed_at = NOW(), updated_at = NOW(),
                    error_code = COALESCE(error_code, 'delivery_timeout'),
                    error_message = COALESCE(
                        error_message,
                        'No terminal worker result arrived before the recovery deadline'
                    )
                WHERE task_id = %s AND generation = %s
                  AND status NOT IN ('succeeded', 'failed', 'superseded')
                """,
                (task_id, previous_generation),
            )
            conn.execute(
                """
                UPDATE discovery_tasks
                SET delivery_generation = %s,
                    queue_task_name = NULL,
                    status = 'queued',
                    scheduled_for = NOW(),
                    started_at = NULL,
                    last_heartbeat_at = NULL,
                    recovery_count = recovery_count + 1,
                    recovery_after = NOW() + INTERVAL '30 minutes',
                    error_code = NULL,
                    error_message = NULL,
                    completed_at = NULL,
                    updated_at = NOW()
                WHERE task_id = %s
                """,
                (next_generation, task_id),
            )
            conn.execute(
                """
                INSERT INTO task_deliveries (task_id, generation, status)
                VALUES (%s, %s, 'prepared')
                ON CONFLICT (task_id, generation) DO NOTHING
                """,
                (task_id, next_generation),
            )
            conn.execute(
                """
                UPDATE discovery_runs
                SET status = 'running', completed_at = NULL, updated_at = NOW()
                WHERE run_id = %s
                """,
                (row["raw_run_id"],),
            )
            allowed_hosts = list(row.get("allowed_hosts") or [])
            for candidate_url in (row.get("base_url"), row.get("source_url")):
                host = (urlparse(str(candidate_url or "")).hostname or "").lower()
                if host and host not in allowed_hosts:
                    allowed_hosts.append(host)
            payloads.append(
                CatalogDiscoveryTaskPayload(
                    task_id=task_id,
                    run_id=str(row["run_id_text"]),
                    run_slot=row["run_slot"],
                    scheduled_for=now,
                    source_id=str(row["source_id"]),
                    store_id=str(row["store_id"]),
                    store_name=str(row["store_name"]),
                    source_url=str(row["source_url"]),
                    source_type=str(row.get("source_type") or "auto"),
                    allowed_hosts=allowed_hosts,
                    connector_version=str(row.get("connector_version") or "catalog-generic-v1"),
                    connector_config=dict(row.get("connector_config") or {}),
                    requests_per_minute=max(1, int(row.get("requests_per_minute") or 6)),
                    max_concurrency=1,
                    respect_robots=bool(row.get("respect_robots", True)),
                    browser_required=bool(row.get("browser_required", False)),
                    delivery_generation=next_generation,
                )
            )
    return payloads


def mark_catalog_task_recovery_enqueue_failed(
    task_id: str,
    *,
    delivery_generation: int,
    error_message: str,
) -> None:
    with transaction() as conn:
        conn.execute(
            """
            UPDATE task_deliveries
            SET status = 'failed', error_code = 'recovery_enqueue_failed',
                error_message = %s, completed_at = NOW(), updated_at = NOW()
            WHERE task_id = %s AND generation = %s
            """,
            (error_message[:4000], task_id, delivery_generation),
        )
        conn.execute(
            """
            UPDATE discovery_tasks
            SET recovery_after = NOW() + INTERVAL '5 minutes',
                error_code = 'recovery_enqueue_failed',
                error_message = %s, updated_at = NOW()
            WHERE task_id = %s AND delivery_generation = %s
            """,
            (error_message[:4000], task_id, delivery_generation),
        )


def _catalog_target(row: dict[str, Any], store_id: str) -> MappingTarget:
    return MappingTarget(
        mapping_id="catalog-candidate",
        offer_id="catalog-candidate",
        offer_key="catalog-candidate",
        variant_id=row["variant_id"],
        store_id=store_id,
        source_url="https://catalog.invalid/product",
        canonical_name=row["canonical_name"],
        section=row.get("section"),
        product_type=row.get("product_type"),
        brand=row.get("brand"),
        model=row.get("model"),
        variant_name=row.get("variant_name"),
        ram_gb=float(row["ram_gb"]) if row.get("ram_gb") is not None else None,
        storage_gb=float(row["storage_gb"]) if row.get("storage_gb") is not None else None,
        color=row.get("color"),
        manufacturer_sku=row.get("manufacturer_sku"),
        gtin=row.get("gtin"),
    )


def _match_catalog_candidate(conn, store_id: str, item: dict[str, Any]):
    title = str(item.get("title") or "")
    gtin = str(item.get("gtin") or "") or None
    sku = str(item.get("sku") or "") or None
    rows = conn.execute(
        """
        SELECT p.*,
               similarity(COALESCE(p.canonical_name, ''), %s) AS title_similarity
        FROM variants p
        WHERE (CAST(%s AS TEXT) IS NOT NULL AND p.gtin = %s)
           OR (CAST(%s AS TEXT) IS NOT NULL AND p.manufacturer_sku = %s)
           OR similarity(COALESCE(p.canonical_name, ''), %s) >= 0.18
        ORDER BY
            CASE WHEN CAST(%s AS TEXT) IS NOT NULL AND p.gtin = %s THEN 0
                 WHEN CAST(%s AS TEXT) IS NOT NULL AND p.manufacturer_sku = %s THEN 1
                 ELSE 2 END,
            title_similarity DESC
        LIMIT 12
        """,
        (title, gtin, gtin, sku, sku, title, gtin, gtin, sku, sku),
    ).fetchall()
    if not rows:
        return None, -10000.0, None, None
    exact_gtin = [dict(row) for row in rows if gtin and str(row.get("gtin") or "") == gtin]
    if len({row["variant_id"] for row in exact_gtin}) == 1:
        return exact_gtin[0], 200.0, "exact_gtin", None

    candidate = ProductCandidate(
        title=title,
        url=item.get("normalized_url"),
        price=item.get("price"),
        old_price=item.get("old_price"),
        currency=item.get("currency"),
        availability=item.get("availability"),
        sku=sku,
        gtin=gtin,
        brand=item.get("brand"),
        source_method=item.get("source_method") or "catalog_discovery",
        text=item.get("text") or title,
        raw=item.get("raw") or {},
    )
    scored = sorted(
        ((score_candidate(_catalog_target(dict(row), store_id), candidate), dict(row)) for row in rows),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else None
    return best, float(best_score), "catalog_title_variant", runner_up


def _promote_catalog_mapping(
    conn,
    *,
    store_id: str,
    variant_id: str,
    item: dict[str, Any],
    match_score: float,
    match_method: str,
    refresh_store_counts: bool = True,
) -> tuple[str | None, bool]:
    existing = conn.execute(
        """
        SELECT mapping_id, active, review_status
        FROM listings
        WHERE store_id = %s AND variant_id = %s AND seller_id IS NULL
        ORDER BY active DESC, updated_at DESC
        LIMIT 1
        """,
        (store_id, variant_id),
    ).fetchone()
    if existing:
        if not existing["active"]:
            return None, False
        conn.execute(
            """
            UPDATE listings
            SET direct_product_url = %s,
                last_discovered_at = NOW(), evidence_count = evidence_count + 1,
                metadata = metadata || %s,
                review_status = 'auto_verified', updated_at = NOW()
            WHERE mapping_id = %s
            """,
            (
                item["normalized_url"],
                Jsonb(
                    {
                        "catalog_last_seen_url": item["normalized_url"],
                        "prefer_direct_scrape": True,
                    }
                ),
                existing["mapping_id"],
            ),
        )
        return str(existing["mapping_id"]), False

    identity = f"{variant_id}|{store_id}|{item['normalized_url']}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest().upper()
    mapping_id = f"MAP-{digest[:16]}"
    offer_id = f"CASH-{digest[16:30]}"
    offer_key = f"{variant_id}|{store_id}|STORE"
    conn.execute(
        """
        INSERT INTO listings (
            mapping_id, offer_id, offer_key, variant_id, store_id,
            source_url, normalized_url, url_type, direct_product_url,
            title_as_seen, match_method, match_confidence,
            evidence_level, extraction_hint, evidence_urls,
            evidence_count, evidence_verified_at, last_discovered_at,
            active, review_status, metadata
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, 'catalog_product_url', %s,
            %s, %s, 'عالية', 'catalog_discovery', 'صفحة منتج مباشرة',
            %s, 1, NOW(), NOW(), TRUE, 'auto_verified', %s
        )
        ON CONFLICT DO NOTHING
        """,
        (
            mapping_id,
            offer_id,
            offer_key,
            variant_id,
            store_id,
            item["normalized_url"],
            item["normalized_url"],
            item["normalized_url"],
            item["title"],
            match_method,
            item["normalized_url"],
            Jsonb({"catalog_match_score": match_score, "prefer_direct_scrape": True}),
        ),
    )
    row = conn.execute(
        """
        SELECT mapping_id, offer_id, offer_key
        FROM listings
        WHERE store_id = %s AND variant_id = %s AND active = TRUE
        ORDER BY updated_at DESC LIMIT 1
        """,
        (store_id, variant_id),
    ).fetchone()
    if not row:
        return None, False
    mapping_id = str(row["mapping_id"])
    offer_id = str(row.get("offer_id") or offer_id)
    offer_key = str(row.get("offer_key") or offer_key)
    conn.execute(
        """
        INSERT INTO current_offers (
            offer_id, offer_key, mapping_id, variant_id, store_id,
            currency, source_method, source_url, freshness_status,
            extraction_status, connector_version, active, review_status
        )
        VALUES (%s, %s, %s, %s, %s, 'EGP', 'catalog_discovery', %s,
                'unseen', 'pending', 'catalog-generic-v1', TRUE, 'auto_verified')
        ON CONFLICT (offer_key) DO NOTHING
        """,
        (offer_id, offer_key, mapping_id, variant_id, store_id, item["normalized_url"]),
    )
    installment_id = "INSTDISC-" + hashlib.sha256(offer_key.encode()).hexdigest()[:18].upper()
    conn.execute(
        """
        INSERT INTO installment_tasks (
            task_id, cash_offer_key, mapping_id, variant_id, store_id,
            source_url, url_type, status, review_status, title_as_seen,
            notes, active, evidence_verified_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'catalog_product_url',
                'pending', 'auto_verified', %s,
                'Created by catalog discovery; cash/installment remain separate.',
                TRUE, NOW())
        ON CONFLICT (cash_offer_key) DO NOTHING
        """,
        (
            installment_id,
            offer_key,
            mapping_id,
            variant_id,
            store_id,
            item["normalized_url"],
            item["title"],
        ),
    )
    if refresh_store_counts:
        conn.execute(
            """
            UPDATE stores
            SET current_mapping_count = (
                    SELECT COUNT(*) FROM listings m
                    WHERE m.store_id = stores.store_id AND m.active = TRUE
                ),
                ready_mapping_count = (
                    SELECT COUNT(*) FROM listings m
                    WHERE m.store_id = stores.store_id AND m.active = TRUE
                ),
                updated_at = NOW()
            WHERE store_id = %s
            """,
            (store_id,),
        )
    return mapping_id, True


def _refresh_catalog_entity_evidence(conn, entity_id: str) -> dict[str, Any] | None:
    return conn.execute(
        """
        WITH evidence AS (
            SELECT
                COUNT(*) AS evidence_count,
                COUNT(DISTINCT store_id) AS evidence_store_count,
                COUNT(*) FILTER (WHERE observed_price >= 10) AS priced_evidence_count,
                COUNT(*) FILTER (WHERE publishable) AS publishable_count,
                COUNT(DISTINCT store_id) FILTER (WHERE publishable)
                    AS publishable_store_count,
                MAX(last_seen_at) AS last_seen_at
            FROM catalog_observations
            WHERE entity_id = %s
        )
        UPDATE identity_clusters entity
        SET evidence_count = evidence.evidence_count,
            evidence_store_count = evidence.evidence_store_count,
            priced_evidence_count = evidence.priced_evidence_count,
            status = CASE
                WHEN entity.status IN ('merged', 'rejected') THEN entity.status
                WHEN entity.identity_strength >= 90
                     AND evidence.publishable_store_count >= 2
                    THEN 'cross_store_verified'
                WHEN evidence.publishable_count >= 1 THEN 'source_verified'
                ELSE 'candidate'
            END,
            last_seen_at = COALESCE(evidence.last_seen_at, entity.last_seen_at),
            updated_at = NOW()
        FROM evidence
        WHERE entity.entity_id = %s
        RETURNING entity.*,
                  evidence.publishable_count,
                  evidence.publishable_store_count
        """,
        (entity_id, entity_id),
    ).fetchone()


def _source_verified_catalog_variant(
    conn,
    *,
    entity: dict[str, Any],
    store_id: str,
    item: dict[str, Any],
) -> tuple[str, bool]:
    """Create the public catalog shape for a validated source observation."""

    identity_key = str(entity["identity_key"])
    digest = hashlib.sha256(identity_key.encode("utf-8")).hexdigest().upper()
    variant_id = f"VAR-SRC-{digest[:18]}"
    product_id = f"PRD-SRC-{digest[:18]}"
    title = str(item.get("title") or entity["canonical_title"]).strip()[:1000]
    brand = str(item.get("brand") or entity.get("brand") or "").strip() or None
    manufacturer_sku = (
        str(item.get("manufacturer_sku") or item.get("sku") or entity.get("manufacturer_sku") or "").strip()
        or None
    )
    gtin = str(item.get("gtin") or entity.get("gtin") or "").strip() or None
    image_url = str(item.get("image_url") or entity.get("image_url") or "").strip() or None

    store = conn.execute(
        "SELECT primary_category FROM stores WHERE store_id = %s",
        (store_id,),
    ).fetchone()
    section = str((store or {}).get("primary_category") or "").strip() or None
    category_id = None
    if section:
        category_digest = hashlib.sha256(normalize_text(section).encode("utf-8")).hexdigest().upper()
        category = conn.execute(
            """
            SELECT category_id FROM categories
            WHERE level = 1 AND LOWER(TRIM(name_ar)) = LOWER(TRIM(%s))
            ORDER BY created_at LIMIT 1
            """,
            (section,),
        ).fetchone()
        if not category:
            category_id = f"CAT-SRC-{category_digest[:16]}"
            category = conn.execute(
                """
                INSERT INTO categories (
                    category_id, source_key, slug, name_ar, name_en, level, metadata
                )
                VALUES (%s, %s, %s, %s, %s, 1, %s)
                ON CONFLICT (source_key) DO UPDATE SET
                    name_ar = EXCLUDED.name_ar,
                    updated_at = NOW()
                RETURNING category_id
                """,
                (
                    category_id,
                    f"catalog_source_section:{normalize_text(section)}",
                    f"catalog-source-{category_digest[:16].lower()}",
                    section,
                    section,
                    Jsonb({"catalog_source_verified": True}),
                ),
            ).fetchone()
        category_id = str(category["category_id"])

    brand_id = None
    if brand:
        normalized_brand = normalize_text(brand)
        brand_digest = hashlib.sha256(normalized_brand.encode("utf-8")).hexdigest().upper()
        brand_row = conn.execute(
            """
            INSERT INTO brands (brand_id, slug, name, normalized_name, metadata)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (normalized_name) DO UPDATE SET
                name = CASE
                    WHEN LENGTH(EXCLUDED.name) > LENGTH(brands.name)
                    THEN EXCLUDED.name ELSE brands.name
                END,
                updated_at = NOW()
            RETURNING brand_id
            """,
            (
                f"BRD-SRC-{brand_digest[:16]}",
                f"catalog-source-{brand_digest[:16].lower()}",
                brand,
                normalized_brand,
                Jsonb({"catalog_source_verified": True}),
            ),
        ).fetchone()
        brand_id = str(brand_row["brand_id"])

    source_status = (
        "catalog_verified" if str(entity["status"]) == "cross_store_verified" else "catalog_source_verified"
    )
    technical_specs = catalog_technical_specs(title)
    model_name = manufacturer_sku or title
    existing_model = conn.execute(
        """
        SELECT product_id
        FROM products
        WHERE category_id IS NOT DISTINCT FROM %s
          AND brand_id IS NOT DISTINCT FROM %s
          AND model IS NOT DISTINCT FROM %s
        ORDER BY created_at
        LIMIT 1
        """,
        (category_id, brand_id, model_name),
    ).fetchone()
    if existing_model:
        product_id = str(existing_model["product_id"])
    conn.execute(
        """
        INSERT INTO products (
            product_id, category_id, brand_id, canonical_name, model,
            source_status, specs, active
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
        ON CONFLICT (product_id) DO UPDATE SET
            canonical_name = EXCLUDED.canonical_name,
            category_id = COALESCE(products.category_id, EXCLUDED.category_id),
            brand_id = COALESCE(products.brand_id, EXCLUDED.brand_id),
            source_status = EXCLUDED.source_status,
            specs = products.specs || EXCLUDED.specs,
            active = TRUE, updated_at = NOW()
        """,
        (
            product_id,
            category_id,
            brand_id,
            title,
            model_name,
            source_status,
            Jsonb({"catalog_entity_id": entity["entity_id"]}),
        ),
    )
    created = conn.execute(
        """
        INSERT INTO variants (
            variant_id, product_id, category_id, brand_id,
            canonical_name, section, brand, model, variant_name,
            ram_gb, storage_gb, manufacturer_sku, gtin, image_url, source_status,
            search_document, specs, active
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
        ON CONFLICT (variant_id) DO UPDATE SET
            canonical_name = EXCLUDED.canonical_name,
            product_id = COALESCE(variants.product_id, EXCLUDED.product_id),
            category_id = COALESCE(variants.category_id, EXCLUDED.category_id),
            brand_id = COALESCE(variants.brand_id, EXCLUDED.brand_id),
            brand = COALESCE(variants.brand, EXCLUDED.brand),
            manufacturer_sku = COALESCE(
                variants.manufacturer_sku, EXCLUDED.manufacturer_sku
            ),
            gtin = COALESCE(variants.gtin, EXCLUDED.gtin),
            image_url = COALESCE(variants.image_url, EXCLUDED.image_url),
            source_status = EXCLUDED.source_status,
            search_document = EXCLUDED.search_document,
            specs = variants.specs || EXCLUDED.specs,
            active = TRUE, updated_at = NOW()
        RETURNING (xmax = 0) AS inserted
        """,
        (
            variant_id,
            product_id,
            category_id,
            brand_id,
            title,
            section,
            brand,
            model_name,
            title,
            technical_specs["ram_gb"],
            technical_specs["storage_gb"],
            manufacturer_sku,
            gtin,
            image_url,
            source_status,
            " ".join(value for value in (title, brand, manufacturer_sku, gtin) if value),
            Jsonb(
                {
                    "catalog_entity_id": entity["entity_id"],
                    "catalog_identity_strength": int(entity["identity_strength"]),
                    "source_verified": source_status == "catalog_source_verified",
                }
            ),
        ),
    ).fetchone()
    return variant_id, bool(created and created["inserted"])


def _upsert_catalog_product_observation(
    conn,
    *,
    origin_type: str,
    origin_id: str,
    store_id: str,
    item: dict[str, Any],
    existing_variant_id: str | None = None,
    match_method: str | None = None,
) -> dict[str, Any]:
    """Attach one crawler/import row to stable identity and publish safe evidence."""

    title = str(item.get("title") or "").strip()
    normalized_url = str(item.get("normalized_url") or item.get("source_url") or "").strip()
    if not title or not normalized_url:
        return {"entity_id": None, "published_price": False, "variant_created": False}

    identity = catalog_entity_identity(item, store_id=store_id)
    publishable = catalog_observation_publishable(item, origin_type=origin_type)
    observation_id = "CPO-" + hashlib.sha256(f"{origin_type}|{origin_id}".encode()).hexdigest()[:22].upper()
    previous = conn.execute(
        """
        SELECT entity_id FROM catalog_observations
        WHERE origin_type = %s AND origin_id = %s
        """,
        (origin_type, origin_id),
    ).fetchone()
    manufacturer_sku = str(item.get("manufacturer_sku") or item.get("sku") or "").strip() or None
    brand = str(item.get("brand") or "").strip() or None
    gtin = str(item.get("gtin") or "").strip() or None
    image_url = str(item.get("image_url") or "").strip() or None
    conn.execute(
        """
        INSERT INTO identity_clusters (
            entity_id, identity_key, identity_strength,
            canonical_title, normalized_title, brand, normalized_brand,
            manufacturer_sku, gtin, image_url, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (identity_key) DO UPDATE SET
            canonical_title = CASE
                WHEN LENGTH(EXCLUDED.canonical_title) >
                     LENGTH(identity_clusters.canonical_title)
                THEN EXCLUDED.canonical_title
                ELSE identity_clusters.canonical_title
            END,
            brand = COALESCE(identity_clusters.brand, EXCLUDED.brand),
            normalized_brand = COALESCE(
                identity_clusters.normalized_brand,
                EXCLUDED.normalized_brand
            ),
            manufacturer_sku = COALESCE(
                identity_clusters.manufacturer_sku,
                EXCLUDED.manufacturer_sku
            ),
            gtin = COALESCE(identity_clusters.gtin, EXCLUDED.gtin),
            image_url = COALESCE(identity_clusters.image_url, EXCLUDED.image_url),
            metadata = identity_clusters.metadata || EXCLUDED.metadata,
            last_seen_at = NOW(), updated_at = NOW()
        """,
        (
            identity.entity_id,
            identity.identity_key,
            identity.strength,
            title[:1000],
            normalize_text(title)[:1000],
            brand,
            normalize_text(brand) if brand else None,
            manufacturer_sku,
            gtin,
            image_url,
            Jsonb({"latest_origin_type": origin_type}),
        ),
    )
    entity_row = conn.execute(
        "SELECT entity_id FROM identity_clusters WHERE identity_key = %s",
        (identity.identity_key,),
    ).fetchone()
    entity_id = str(entity_row["entity_id"])
    confidence = min(100, identity.strength + (5 if publishable else 0))
    raw_payload = item.get("raw_payload") or item.get("raw") or {}
    conn.execute(
        """
        INSERT INTO catalog_observations (
            observation_id, entity_id, origin_type, origin_id, store_id,
            normalized_url, source_url, title, normalized_title, brand,
            manufacturer_sku, merchant_sku, gtin, observed_price, currency,
            availability, image_url, validation_status, publishable,
            confidence_score, raw_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (origin_type, origin_id) DO UPDATE SET
            entity_id = EXCLUDED.entity_id,
            normalized_url = EXCLUDED.normalized_url,
            source_url = EXCLUDED.source_url,
            title = EXCLUDED.title,
            normalized_title = EXCLUDED.normalized_title,
            brand = COALESCE(EXCLUDED.brand, catalog_observations.brand),
            manufacturer_sku = COALESCE(
                EXCLUDED.manufacturer_sku,
                catalog_observations.manufacturer_sku
            ),
            merchant_sku = COALESCE(
                EXCLUDED.merchant_sku,
                catalog_observations.merchant_sku
            ),
            gtin = COALESCE(EXCLUDED.gtin, catalog_observations.gtin),
            observed_price = COALESCE(
                EXCLUDED.observed_price,
                catalog_observations.observed_price
            ),
            currency = COALESCE(EXCLUDED.currency, catalog_observations.currency),
            availability = COALESCE(
                EXCLUDED.availability,
                catalog_observations.availability
            ),
            image_url = COALESCE(EXCLUDED.image_url, catalog_observations.image_url),
            validation_status = EXCLUDED.validation_status,
            publishable = EXCLUDED.publishable,
            confidence_score = EXCLUDED.confidence_score,
            raw_payload = EXCLUDED.raw_payload,
            last_seen_at = NOW(), updated_at = NOW()
        """,
        (
            observation_id,
            entity_id,
            origin_type,
            origin_id,
            store_id,
            normalized_url,
            item.get("source_url") or normalized_url,
            title[:1000],
            normalize_text(title)[:1000],
            brand,
            manufacturer_sku,
            item.get("merchant_sku"),
            gtin,
            item.get("price") or item.get("observed_price"),
            str(item.get("currency") or "EGP").upper(),
            item.get("availability"),
            image_url,
            str(item.get("validation_status") or "accepted"),
            publishable,
            confidence,
            Jsonb(_jsonable(raw_payload)),
        ),
    )
    source_table = "discovery_candidates" if origin_type == "catalog_discovery" else "import_items"
    source_id_column = "candidate_id" if origin_type == "catalog_discovery" else "item_id"
    conn.execute(
        f"UPDATE {source_table} SET entity_id = %s, updated_at = NOW() WHERE {source_id_column} = %s",
        (entity_id, origin_id),
    )
    if previous and str(previous["entity_id"]) != entity_id:
        _refresh_catalog_entity_evidence(conn, str(previous["entity_id"]))
    entity = _refresh_catalog_entity_evidence(conn, entity_id)
    if not entity:
        return {"entity_id": entity_id, "published_price": False, "variant_created": False}

    variant_id = existing_variant_id or entity.get("promoted_variant_id")
    variant_created = False
    if not variant_id and gtin:
        strong_matches = conn.execute(
            """
            SELECT variant_id FROM variants
            WHERE gtin = %s AND active = TRUE
              AND source_status <> 'catalog_provisional'
            ORDER BY updated_at DESC LIMIT 2
            """,
            (gtin,),
        ).fetchall()
        if len(strong_matches) == 1:
            variant_id = str(strong_matches[0]["variant_id"])
    if not variant_id and brand and manufacturer_sku:
        strong_matches = conn.execute(
            """
            SELECT variant_id FROM variants
            WHERE LOWER(TRIM(brand)) = LOWER(TRIM(%s))
              AND LOWER(TRIM(manufacturer_sku)) = LOWER(TRIM(%s))
              AND active = TRUE
              AND source_status <> 'catalog_provisional'
            ORDER BY updated_at DESC LIMIT 2
            """,
            (brand, manufacturer_sku),
        ).fetchall()
        if len(strong_matches) == 1:
            variant_id = str(strong_matches[0]["variant_id"])
    if not variant_id and publishable:
        variant_id, variant_created = _source_verified_catalog_variant(
            conn,
            entity=dict(entity),
            store_id=store_id,
            item=item,
        )
    elif variant_id:
        promoted_source_status = (
            "catalog_verified"
            if str(entity["status"]) == "cross_store_verified"
            else "catalog_source_verified"
        )
        conn.execute(
            """
            UPDATE variants
            SET source_status = %s, updated_at = NOW()
            WHERE variant_id = %s
              AND source_status IN ('catalog_provisional', 'catalog_source_verified')
            """,
            (promoted_source_status, variant_id),
        )
    if not variant_id:
        return {"entity_id": entity_id, "published_price": False, "variant_created": False}

    conn.execute(
        """
        UPDATE identity_clusters
        SET promoted_variant_id = %s, updated_at = NOW()
        WHERE entity_id = %s
        """,
        (variant_id, entity_id),
    )
    if not publishable:
        return {
            "entity_id": entity_id,
            "variant_id": str(variant_id),
            "published_price": False,
            "variant_created": variant_created,
        }

    mapping_id, mapping_created = _promote_catalog_mapping(
        conn,
        store_id=store_id,
        variant_id=str(variant_id),
        item={**item, "normalized_url": normalized_url, "title": title},
        match_score=float(confidence),
        match_method=match_method or f"{origin_type}_source_verified",
        refresh_store_counts=False,
    )
    cross_store_verified = str(entity["status"]) == "cross_store_verified"
    published_price = False
    if mapping_id:
        review_status = "auto_verified" if cross_store_verified else "needs_review"
        anomaly_status = "clear" if cross_store_verified else "review"
        reasons = [] if cross_store_verified else ["single_source_catalog_observation"]
        updated = conn.execute(
            """
            UPDATE current_offers
            SET cash_price = %s,
                currency = %s,
                availability = %s,
                source_method = %s,
                source_url = %s,
                last_checked_at = NOW(),
                last_success_at = NOW(),
                freshness_status = 'fresh',
                extraction_status = 'success',
                consecutive_failures = 0,
                connector_version = %s,
                active = TRUE,
                review_status = %s,
                anomaly_status = %s,
                anomaly_reasons = %s,
                match_quality_score = %s,
                decision_metadata = decision_metadata || %s,
                raw_payload = %s,
                updated_at = NOW()
            WHERE mapping_id = %s
              AND (
                    last_success_at IS NULL
                    OR extraction_status = 'pending'
                    OR source_method LIKE 'catalog_%%'
                    OR source_method LIKE '%%bootstrap%%'
              )
            RETURNING offer_id
            """,
            (
                item.get("price") or item.get("observed_price"),
                str(item.get("currency") or "EGP").upper(),
                item.get("availability"),
                f"catalog_{origin_type}",
                normalized_url,
                "catalog-import-v1" if origin_type == "catalog_import" else "catalog-generic-v1",
                review_status,
                anomaly_status,
                Jsonb(reasons),
                confidence,
                Jsonb(
                    {
                        "catalog_entity_id": entity_id,
                        "catalog_observation_id": observation_id,
                        "source_verified": not cross_store_verified,
                    }
                ),
                Jsonb(_jsonable(raw_payload)),
                mapping_id,
            ),
        ).fetchone()
        published_price = bool(updated)
        conn.execute(
            """
            UPDATE listings
            SET review_status = %s,
                match_confidence = %s,
                direct_product_url = %s,
                direct_url_status = 'verified',
                direct_url_verified_at = NOW(),
                direct_url_source = %s,
                direct_url_evidence = direct_url_evidence || %s,
                metadata = metadata || %s,
                updated_at = NOW()
            WHERE mapping_id = %s
            """,
            (
                review_status,
                "عالية" if cross_store_verified else "متوسطة",
                normalized_url,
                origin_type,
                Jsonb(
                    {
                        "catalog_entity_id": entity_id,
                        "catalog_observation_id": observation_id,
                        "publishable": True,
                    }
                ),
                Jsonb(
                    {
                        "catalog_entity_id": entity_id,
                        "prefer_direct_scrape": True,
                    }
                ),
                mapping_id,
            ),
        )
        if origin_type == "catalog_discovery":
            conn.execute(
                """
                UPDATE discovery_candidates
                SET status = 'auto_mapped', proposed_variant_id = %s,
                    mapping_id = %s, match_score = %s, match_method = %s,
                    review_status = %s, updated_at = NOW()
                WHERE candidate_id = %s
                """,
                (
                    variant_id,
                    mapping_id,
                    confidence,
                    match_method or "catalog_source_verified",
                    review_status,
                    origin_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE import_items
                SET validation_status = CASE
                        WHEN validation_status = 'mapping_created' THEN validation_status
                        WHEN %s THEN 'mapping_created'
                        ELSE 'mapping_repaired'
                    END,
                    proposed_variant_id = %s, mapping_id = %s,
                    match_score = %s, match_method = %s, updated_at = NOW()
                WHERE item_id = %s
                """,
                (
                    mapping_created,
                    variant_id,
                    mapping_id,
                    confidence,
                    match_method or "catalog_import_source_verified",
                    origin_id,
                ),
            )
    return {
        "entity_id": entity_id,
        "variant_id": str(variant_id),
        "mapping_id": mapping_id,
        "mapping_created": mapping_created,
        "published_price": published_price,
        "variant_created": variant_created,
        "cross_store_verified": cross_store_verified,
    }


def ingest_catalog_candidates(
    payload: CatalogDiscoveryTaskPayload,
    candidates: list[dict[str, Any]],
) -> dict[str, int]:
    settings = get_settings()
    stats = {
        "candidates_new": 0,
        "mappings_created": 0,
        "matched_existing_mappings": 0,
        "review_candidates": 0,
        "provisional_products": 0,
        "verified_products": 0,
        "source_verified_products": 0,
        "prices_published": 0,
    }
    with transaction() as conn:
        variant_rows = conn.execute(
            """
            SELECT * FROM variants
            WHERE source_status <> 'catalog_provisional'
            """
        ).fetchall()
        variant_index = build_catalog_variant_index(dict(row) for row in variant_rows)
        known_brands = sorted(
            (str(row["brand"]) for row in variant_rows if row.get("brand")),
            key=len,
            reverse=True,
        )
        provisional_ids: set[str] = set()
        for item in candidates:
            normalized_url = str(item["normalized_url"])
            candidate_id = (
                "CAT-"
                + hashlib.sha256(f"{payload.store_id}|{normalized_url}".encode()).hexdigest()[:22].upper()
            )
            existing_candidate = conn.execute(
                "SELECT candidate_id FROM discovery_candidates WHERE candidate_id = %s",
                (candidate_id,),
            ).fetchone()
            if not existing_candidate:
                stats["candidates_new"] += 1

            mapped = conn.execute(
                """
                SELECT mapping_id, variant_id
                FROM listings
                WHERE store_id = %s AND normalized_url = %s AND active = TRUE
                LIMIT 1
                """,
                (payload.store_id, normalized_url),
            ).fetchone()
            proposed_variant_id = str(mapped["variant_id"]) if mapped else None
            mapping_id = str(mapped["mapping_id"]) if mapped else None
            match_score = 200.0 if mapped else None
            match_method = "existing_url_mapping" if mapped else None
            status = "already_mapped" if mapped else "pending_match"
            runner_up = None

            if not mapped:
                best, best_score, method = deterministic_catalog_match(
                    variant_index,
                    item,
                    store_id=payload.store_id,
                )
                if best is None and catalog_candidate_has_match_evidence(
                    variant_index,
                    item,
                ):
                    best, best_score, method, runner_up = _match_catalog_candidate(
                        conn, payload.store_id, item
                    )
                if best:
                    margin_safe = runner_up is None or best_score - float(runner_up) >= 8.0
                    if best.get("source_status") == "catalog_provisional" and method == "exact_gtin":
                        proposed_variant_id = str(best["variant_id"])
                        match_score = best_score
                        match_method = "provisional_gtin_corroboration"
                        status = "provisional_product"
                        provisional_ids.add(proposed_variant_id)
                    elif best_score >= settings.catalog_discovery_auto_match_score and margin_safe:
                        proposed_variant_id = str(best["variant_id"])
                        match_score = best_score
                        match_method = method
                        status = "auto_matched"
                    elif best_score >= settings.catalog_discovery_review_score:
                        proposed_variant_id = str(best["variant_id"])
                        match_score = best_score
                        match_method = method
                        status = "needs_review"
                        stats["review_candidates"] += 1

            fingerprint = str(item.get("fingerprint") or "") or None
            gtin = str(item.get("gtin") or "") or None
            if not proposed_variant_id and gtin:
                brand = str(item.get("brand") or "").strip()
                normalized_title = normalize_text(item.get("title"))
                if not brand:
                    for known_brand in known_brands:
                        if normalize_text(known_brand) in normalized_title:
                            brand = known_brand
                            break
                if brand:
                    variant_digest = hashlib.sha256(f"gtin:{gtin}".encode()).hexdigest()[:18].upper()
                    proposed_variant_id = f"VAR-DISC-{variant_digest}"
                    conn.execute(
                        """
                        INSERT INTO variants (
                            variant_id, canonical_name, brand, model,
                            manufacturer_sku, gtin, source_status, specs
                        )
                        VALUES (%s, %s, %s, %s, %s, %s,
                                'catalog_provisional', %s)
                        ON CONFLICT (variant_id) DO UPDATE SET
                            canonical_name = CASE
                                WHEN variants.source_status = 'catalog_provisional'
                                THEN EXCLUDED.canonical_name
                                ELSE variants.canonical_name
                            END,
                            updated_at = NOW()
                        """,
                        (
                            proposed_variant_id,
                            item["title"],
                            brand,
                            item.get("sku"),
                            item.get("sku"),
                            gtin,
                            Jsonb(
                                {
                                    "catalog_discovery": True,
                                    "first_store_id": payload.store_id,
                                    "first_source_url": normalized_url,
                                }
                            ),
                        ),
                    )
                    status = "provisional_product"
                    match_method = "new_gtin_provisional"
                    match_score = 100.0
                    provisional_ids.add(proposed_variant_id)
                    stats["provisional_products"] += 1

            raw_payload = dict(item)
            raw_payload["text"] = str(raw_payload.get("text") or "")[:4000]
            if len(json.dumps(_jsonable(raw_payload), ensure_ascii=False).encode("utf-8")) > 80_000:
                raw_payload["raw"] = {"truncated": True}
            conn.execute(
                """
                INSERT INTO discovery_candidates (
                    candidate_id, store_id, source_id, normalized_url,
                    source_url, title, brand, sku, gtin, fingerprint,
                    currency, observed_price, availability, source_method,
                    status, proposed_variant_id, mapping_id, match_score,
                    match_method, last_run_id, raw_payload
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (candidate_id) DO UPDATE SET
                    source_id = EXCLUDED.source_id,
                    title = EXCLUDED.title,
                    brand = COALESCE(EXCLUDED.brand, discovery_candidates.brand),
                    sku = COALESCE(EXCLUDED.sku, discovery_candidates.sku),
                    gtin = COALESCE(EXCLUDED.gtin, discovery_candidates.gtin),
                    fingerprint = COALESCE(EXCLUDED.fingerprint, discovery_candidates.fingerprint),
                    currency = COALESCE(EXCLUDED.currency, discovery_candidates.currency),
                    observed_price = COALESCE(EXCLUDED.observed_price, discovery_candidates.observed_price),
                    availability = COALESCE(EXCLUDED.availability, discovery_candidates.availability),
                    source_method = EXCLUDED.source_method,
                    status = CASE
                        WHEN discovery_candidates.status IN ('already_mapped', 'auto_mapped')
                        THEN discovery_candidates.status ELSE EXCLUDED.status END,
                    proposed_variant_id = COALESCE(EXCLUDED.proposed_variant_id, discovery_candidates.proposed_variant_id),
                    mapping_id = COALESCE(EXCLUDED.mapping_id, discovery_candidates.mapping_id),
                    match_score = COALESCE(EXCLUDED.match_score, discovery_candidates.match_score),
                    match_method = COALESCE(EXCLUDED.match_method, discovery_candidates.match_method),
                    last_seen_at = NOW(), last_run_id = EXCLUDED.last_run_id,
                    raw_payload = EXCLUDED.raw_payload,
                    reconcile_version = CASE
                        WHEN ROW(
                            discovery_candidates.normalized_url,
                            discovery_candidates.title,
                            discovery_candidates.brand,
                            discovery_candidates.sku,
                            discovery_candidates.gtin,
                            discovery_candidates.currency,
                            discovery_candidates.observed_price,
                            discovery_candidates.availability
                        ) IS DISTINCT FROM ROW(
                            EXCLUDED.normalized_url,
                            EXCLUDED.title,
                            EXCLUDED.brand,
                            EXCLUDED.sku,
                            EXCLUDED.gtin,
                            EXCLUDED.currency,
                            EXCLUDED.observed_price,
                            EXCLUDED.availability
                        ) THEN 0
                        ELSE discovery_candidates.reconcile_version
                    END,
                    reconcile_checked_at = CASE
                        WHEN ROW(
                            discovery_candidates.normalized_url,
                            discovery_candidates.title,
                            discovery_candidates.brand,
                            discovery_candidates.sku,
                            discovery_candidates.gtin,
                            discovery_candidates.currency,
                            discovery_candidates.observed_price,
                            discovery_candidates.availability
                        ) IS DISTINCT FROM ROW(
                            EXCLUDED.normalized_url,
                            EXCLUDED.title,
                            EXCLUDED.brand,
                            EXCLUDED.sku,
                            EXCLUDED.gtin,
                            EXCLUDED.currency,
                            EXCLUDED.observed_price,
                            EXCLUDED.availability
                        ) THEN NULL
                        ELSE discovery_candidates.reconcile_checked_at
                    END,
                    updated_at = NOW()
                """,
                (
                    candidate_id,
                    payload.store_id,
                    payload.source_id,
                    normalized_url,
                    item["source_url"],
                    item["title"],
                    item.get("brand"),
                    item.get("sku"),
                    gtin,
                    fingerprint,
                    item.get("currency"),
                    item.get("price"),
                    item.get("availability"),
                    item.get("source_method"),
                    status,
                    proposed_variant_id,
                    mapping_id,
                    match_score,
                    match_method,
                    payload.run_id,
                    Jsonb(_jsonable(raw_payload)),
                ),
            )

            if status == "auto_matched" and proposed_variant_id:
                mapping_id, created = _promote_catalog_mapping(
                    conn,
                    store_id=payload.store_id,
                    variant_id=proposed_variant_id,
                    item=item,
                    match_score=float(match_score or 0),
                    match_method=str(match_method or "catalog_auto_match"),
                )
                if mapping_id:
                    conn.execute(
                        """
                        UPDATE discovery_candidates
                        SET status = 'auto_mapped', mapping_id = %s, updated_at = NOW()
                        WHERE candidate_id = %s
                        """,
                        (mapping_id, candidate_id),
                    )
                    if created:
                        stats["mappings_created"] += 1
                    else:
                        stats["matched_existing_mappings"] += 1

            observation = _upsert_catalog_product_observation(
                conn,
                origin_type="catalog_discovery",
                origin_id=candidate_id,
                store_id=payload.store_id,
                item={**item, "validation_status": "accepted"},
                existing_variant_id=(
                    proposed_variant_id
                    if status
                    in {
                        "already_mapped",
                        "auto_matched",
                        "provisional_product",
                    }
                    else None
                ),
                match_method=str(match_method or "catalog_source_verified"),
            )
            if observation.get("variant_created"):
                stats["source_verified_products"] += 1
            if observation.get("published_price"):
                stats["prices_published"] += 1
            if observation.get("mapping_created") and not (status == "auto_matched" and mapping_id):
                stats["mappings_created"] += 1
            if observation.get("cross_store_verified"):
                stats["verified_products"] += int(bool(observation.get("variant_created")))

        for provisional_id in provisional_ids:
            evidence = conn.execute(
                """
                SELECT COUNT(DISTINCT store_id) AS stores
                FROM discovery_candidates
                WHERE proposed_variant_id = %s
                """,
                (provisional_id,),
            ).fetchone()
            evidence_count = int(evidence["stores"] or 0)
            conn.execute(
                """
                UPDATE discovery_candidates
                SET evidence_store_count = %s, updated_at = NOW()
                WHERE proposed_variant_id = %s
                """,
                (evidence_count, provisional_id),
            )
            if evidence_count < settings.catalog_discovery_new_product_min_stores:
                continue
            conn.execute(
                """
                UPDATE variants
                SET source_status = 'catalog_verified', updated_at = NOW()
                WHERE variant_id = %s AND source_status = 'catalog_provisional'
                """,
                (provisional_id,),
            )
            related = conn.execute(
                """
                SELECT candidate_id, store_id, normalized_url AS source_url,
                       normalized_url, title, brand, sku, gtin, currency,
                       observed_price AS price, availability, source_method,
                       raw_payload AS raw
                FROM discovery_candidates
                WHERE proposed_variant_id = %s
                """,
                (provisional_id,),
            ).fetchall()
            for related_row in related:
                related_item = dict(related_row)
                mapping_id, created = _promote_catalog_mapping(
                    conn,
                    store_id=related_item["store_id"],
                    variant_id=provisional_id,
                    item=related_item,
                    match_score=200.0,
                    match_method="cross_store_gtin_verified",
                )
                if mapping_id:
                    conn.execute(
                        """
                        UPDATE discovery_candidates
                        SET status = 'auto_mapped', mapping_id = %s,
                            match_method = 'cross_store_gtin_verified',
                            match_score = 200, updated_at = NOW()
                        WHERE candidate_id = %s
                        """,
                        (mapping_id, related_item["candidate_id"]),
                    )
                    if created:
                        stats["mappings_created"] += 1
            stats["verified_products"] += 1
        if stats["mappings_created"]:
            conn.execute(
                """
                UPDATE stores
                SET current_mapping_count = (
                        SELECT COUNT(*) FROM listings m
                        WHERE m.store_id = stores.store_id AND m.active = TRUE
                    ),
                    ready_mapping_count = (
                        SELECT COUNT(*) FROM listings m
                        WHERE m.store_id = stores.store_id AND m.active = TRUE
                    ),
                    updated_at = NOW()
                WHERE store_id = %s
                """,
                (payload.store_id,),
            )
    return stats


def _catalog_bootstrap_context(conn, store_id: str) -> tuple[dict[str, Any], list[str]]:
    store = conn.execute(
        """
        SELECT s.store_id, s.name, s.base_url, s.active,
               COALESCE(c.allowed_hosts, '{}'::text[]) AS allowed_hosts
        FROM stores s
        LEFT JOIN connector_configs c ON c.store_id = s.store_id
        WHERE s.store_id = %s
        """,
        (store_id,),
    ).fetchone()
    if not store:
        raise ValueError(f"Unknown store_id: {store_id}")
    allowed_hosts = [str(value) for value in (store.get("allowed_hosts") or []) if value]
    base_host = (urlparse(str(store.get("base_url") or "")).hostname or "").lower()
    if base_host and base_host not in allowed_hosts:
        allowed_hosts.append(base_host)
    if not allowed_hosts:
        raise ValueError(f"Store {store_id} has no configured allowed host")
    return dict(store), allowed_hosts


def _bootstrap_match_item(record: dict[str, Any]) -> dict[str, Any]:
    """Expose only manufacturer-safe identifiers to the catalog matcher."""

    return {
        "source_url": record["normalized_url"],
        "normalized_url": record["normalized_url"],
        "title": record["title"],
        "brand": record.get("brand"),
        "sku": record.get("manufacturer_sku"),
        "gtin": record.get("gtin"),
        "price": record.get("price"),
        "currency": record.get("currency"),
        "availability": record.get("availability"),
        "source_method": "external_catalog_bootstrap",
        "text": record.get("title") or "",
        "raw": record.get("raw_payload") or {},
    }


def _bootstrap_preview(
    conn,
    *,
    store_id: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    variant_rows = conn.execute(
        """
        SELECT * FROM variants
        WHERE source_status <> 'catalog_provisional' AND active = TRUE
        """
    ).fetchall()
    variant_index = build_catalog_variant_index(dict(row) for row in variant_rows)
    stats = {
        "rows_received": len(records),
        "rows_accepted": 0,
        "rows_rejected": 0,
        "rows_matched": 0,
        "rows_pending_match": 0,
        "url_conflicts": 0,
        "prices_observed": 0,
    }
    previews: list[dict[str, Any]] = []
    for record in records:
        if record["validation_status"] == "rejected":
            stats["rows_rejected"] += 1
            if len(previews) < 50:
                previews.append(
                    {
                        "url": record.get("normalized_url") or record.get("source_url"),
                        "status": "rejected",
                        "reason": record.get("rejection_code"),
                    }
                )
            continue
        stats["rows_accepted"] += 1
        if record.get("price") is not None:
            stats["prices_observed"] += 1
        item = _bootstrap_match_item(record)
        best, score, method = deterministic_catalog_match(
            variant_index,
            item,
            store_id=store_id,
        )
        status = "pending_match"
        variant_id = None
        conflict = None
        if best:
            variant_id = str(best["variant_id"])
            owners = conn.execute(
                """
                SELECT DISTINCT variant_id
                FROM listings
                WHERE store_id = %s AND active = TRUE
                  AND (normalized_url = %s OR direct_product_url = %s)
                """,
                (store_id, record["normalized_url"], record["normalized_url"]),
            ).fetchall()
            conflicting = {str(row["variant_id"]) for row in owners if str(row["variant_id"]) != variant_id}
            if conflicting:
                status = "url_variant_conflict"
                conflict = sorted(conflicting)
                stats["url_conflicts"] += 1
            else:
                status = "matched"
                stats["rows_matched"] += 1
        else:
            stats["rows_pending_match"] += 1
        if len(previews) < 50:
            previews.append(
                {
                    "url": record["normalized_url"],
                    "title": record.get("title"),
                    "status": status,
                    "variant_id": variant_id,
                    "match_method": method,
                    "match_score": score if best else None,
                    "conflicting_variant_ids": conflict,
                }
            )
    return {"stats": stats, "preview": previews, "variant_index": variant_index}


def ingest_catalog_bootstrap(
    request: CatalogBootstrapImportRequest,
) -> dict[str, Any]:
    """Stage an external scrape and promote only deterministic direct links."""

    with connection() as conn:
        store, allowed_hosts = _catalog_bootstrap_context(conn, request.store_id)
        normalized = [
            item.as_dict()
            for item in normalize_external_product_records(
                request.records,
                allowed_hosts=allowed_hosts,
            )
        ]
        preview_result = _bootstrap_preview(
            conn,
            store_id=request.store_id,
            records=normalized,
        )
    public_preview = {
        "store": {
            "store_id": store["store_id"],
            "name": store["name"],
            "allowed_hosts": allowed_hosts,
        },
        "provider": request.provider,
        "external_run_id": request.external_run_id,
        "dry_run": request.dry_run,
        "stats": preview_result["stats"],
        "preview": preview_result["preview"],
    }
    if request.dry_run:
        return {"status": "dry_run", **public_preview}

    variant_index = preview_result["variant_index"]
    metadata = _jsonable(request.metadata)
    with transaction() as conn:
        run = conn.execute(
            """
            INSERT INTO import_runs (
                provider, external_run_id, store_id, status,
                rows_received, metadata, started_at, updated_at
            )
            VALUES (%s, %s, %s, 'processing', %s, %s, NOW(), NOW())
            ON CONFLICT (provider, external_run_id, store_id) DO UPDATE SET
                status = 'processing', rows_received = EXCLUDED.rows_received,
                metadata = import_runs.metadata || EXCLUDED.metadata,
                error_message = NULL, started_at = NOW(), completed_at = NULL,
                updated_at = NOW()
            RETURNING import_id::text
            """,
            (
                request.provider,
                request.external_run_id,
                request.store_id,
                len(normalized),
                Jsonb(metadata),
            ),
        ).fetchone()
        import_id = str(run["import_id"])
        promoted_mapping_ids: set[str] = set()

        for record in normalized:
            item_id = (
                "BOOT-"
                + hashlib.sha256(
                    (
                        f"{request.provider}|{request.external_run_id}|"
                        f"{request.store_id}|{record['data_hash']}"
                    ).encode()
                )
                .hexdigest()[:24]
                .upper()
            )
            previous = conn.execute(
                """
                SELECT validation_status, mapping_id, proposed_variant_id
                FROM import_items WHERE item_id = %s
                """,
                (item_id,),
            ).fetchone()
            validation_status = record["validation_status"]
            rejection_code = record.get("rejection_code")
            proposed_variant_id = None
            mapping_id = None
            match_method = None
            match_score = None

            if previous and previous["validation_status"] in {
                "mapping_created",
                "mapping_repaired",
            }:
                validation_status = str(previous["validation_status"])
                mapping_id = str(previous["mapping_id"]) if previous.get("mapping_id") else None
                proposed_variant_id = (
                    str(previous["proposed_variant_id"]) if previous.get("proposed_variant_id") else None
                )
            elif validation_status != "rejected":
                item = _bootstrap_match_item(record)
                best, score, method = deterministic_catalog_match(
                    variant_index,
                    item,
                    store_id=request.store_id,
                )
                if best:
                    proposed_variant_id = str(best["variant_id"])
                    match_method = method
                    match_score = float(score)
                    owners = conn.execute(
                        """
                        SELECT DISTINCT variant_id
                        FROM listings
                        WHERE store_id = %s AND active = TRUE
                          AND (normalized_url = %s OR direct_product_url = %s)
                        """,
                        (
                            request.store_id,
                            record["normalized_url"],
                            record["normalized_url"],
                        ),
                    ).fetchall()
                    conflicting = sorted(
                        {
                            str(row["variant_id"])
                            for row in owners
                            if str(row["variant_id"]) != proposed_variant_id
                        }
                    )
                    if conflicting:
                        validation_status = "url_variant_conflict"
                        rejection_code = "url_already_owned_by_other_variant"
                        conn.execute(
                            """
                            INSERT INTO review_cases (
                                entity_type, entity_id, issue_code, severity,
                                title, description, payload
                            )
                            VALUES (
                                'catalog_import_item', %s, 'url_variant_conflict', 'critical',
                                'External product URL conflicts with an existing variant',
                                'The link was not promoted because it is already mapped to a different variant.',
                                %s
                            )
                            ON CONFLICT (entity_type, entity_id, issue_code)
                                WHERE status IN ('open', 'in_review')
                            DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                            """,
                            (
                                item_id,
                                Jsonb(
                                    {
                                        "url": record["normalized_url"],
                                        "proposed_variant_id": proposed_variant_id,
                                        "existing_variant_ids": conflicting,
                                        "provider": request.provider,
                                        "external_run_id": request.external_run_id,
                                    }
                                ),
                            ),
                        )
                    else:
                        existing_mapping = conn.execute(
                            """
                            SELECT mapping_id
                            FROM listings
                            WHERE store_id = %s AND variant_id = %s
                              AND seller_id IS NULL AND active = TRUE
                            ORDER BY updated_at DESC LIMIT 1
                            """,
                            (request.store_id, proposed_variant_id),
                        ).fetchone()
                        mapping_id, created = _promote_catalog_mapping(
                            conn,
                            store_id=request.store_id,
                            variant_id=proposed_variant_id,
                            item=item,
                            match_score=match_score,
                            match_method=f"{request.provider}:{match_method}",
                            refresh_store_counts=False,
                        )
                        if mapping_id:
                            validation_status = "mapping_created" if created else "mapping_repaired"
                            promoted_mapping_ids.add(mapping_id)
                            conn.execute(
                                """
                                UPDATE listings
                                SET direct_product_url = %s,
                                    direct_url_status = 'verified',
                                    direct_url_verified_at = NOW(),
                                    direct_url_source = %s,
                                    direct_url_evidence = %s,
                                    title_as_seen = COALESCE(NULLIF(%s, ''), title_as_seen),
                                    match_method = %s,
                                    match_confidence = 'عالية',
                                    evidence_verified_at = NOW(),
                                    metadata = metadata || %s,
                                    updated_at = NOW()
                                WHERE mapping_id = %s
                                """,
                                (
                                    record["normalized_url"],
                                    request.provider,
                                    Jsonb(
                                        {
                                            **record["evidence"],
                                            "external_run_id": request.external_run_id,
                                            "data_hash": record["data_hash"],
                                        }
                                    ),
                                    record.get("title") or "",
                                    f"{request.provider}:{match_method}",
                                    Jsonb(
                                        {
                                            "prefer_direct_scrape": True,
                                            "catalog_bootstrap_import_id": import_id,
                                            "catalog_bootstrap_previous_mapping": bool(existing_mapping),
                                        }
                                    ),
                                    mapping_id,
                                ),
                            )
                else:
                    validation_status = "pending_match"

            conn.execute(
                """
                INSERT INTO import_items (
                    item_id, import_id, store_id, source_url, normalized_url,
                    title, brand, merchant_sku, manufacturer_sku, gtin,
                    observed_price, currency, availability, image_url,
                    validation_status, rejection_code, proposed_variant_id,
                    mapping_id, match_method, match_score, data_hash,
                    evidence, raw_payload
                )
                VALUES (
                    %s, %s::uuid, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (item_id) DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    normalized_url = EXCLUDED.normalized_url,
                    title = EXCLUDED.title,
                    brand = COALESCE(EXCLUDED.brand, import_items.brand),
                    merchant_sku = COALESCE(EXCLUDED.merchant_sku, import_items.merchant_sku),
                    manufacturer_sku = COALESCE(EXCLUDED.manufacturer_sku, import_items.manufacturer_sku),
                    gtin = COALESCE(EXCLUDED.gtin, import_items.gtin),
                    observed_price = COALESCE(EXCLUDED.observed_price, import_items.observed_price),
                    currency = COALESCE(EXCLUDED.currency, import_items.currency),
                    availability = COALESCE(EXCLUDED.availability, import_items.availability),
                    image_url = COALESCE(EXCLUDED.image_url, import_items.image_url),
                    validation_status = EXCLUDED.validation_status,
                    rejection_code = EXCLUDED.rejection_code,
                    proposed_variant_id = COALESCE(EXCLUDED.proposed_variant_id, import_items.proposed_variant_id),
                    mapping_id = COALESCE(EXCLUDED.mapping_id, import_items.mapping_id),
                    match_method = COALESCE(EXCLUDED.match_method, import_items.match_method),
                    match_score = COALESCE(EXCLUDED.match_score, import_items.match_score),
                    evidence = EXCLUDED.evidence,
                    raw_payload = EXCLUDED.raw_payload,
                    last_seen_at = NOW(), updated_at = NOW()
                """,
                (
                    item_id,
                    import_id,
                    request.store_id,
                    record.get("source_url"),
                    record.get("normalized_url"),
                    record.get("title"),
                    record.get("brand"),
                    record.get("merchant_sku"),
                    record.get("manufacturer_sku"),
                    record.get("gtin"),
                    record.get("price"),
                    record.get("currency"),
                    record.get("availability"),
                    record.get("image_url"),
                    validation_status,
                    rejection_code,
                    proposed_variant_id,
                    mapping_id,
                    match_method,
                    match_score,
                    record["data_hash"],
                    Jsonb(record["evidence"]),
                    Jsonb(record["raw_payload"]),
                ),
            )

            if record["validation_status"] != "rejected":
                observation = _upsert_catalog_product_observation(
                    conn,
                    origin_type="catalog_import",
                    origin_id=item_id,
                    store_id=request.store_id,
                    item={**record, "validation_status": "accepted"},
                    existing_variant_id=proposed_variant_id,
                    match_method=(
                        f"{request.provider}:{match_method}"
                        if match_method
                        else f"{request.provider}:source_verified"
                    ),
                )
                if observation.get("mapping_id"):
                    promoted_mapping_ids.add(str(observation["mapping_id"]))

        if promoted_mapping_ids:
            conn.execute(
                """
                UPDATE stores
                SET current_mapping_count = (
                        SELECT COUNT(*) FROM listings m
                        WHERE m.store_id = stores.store_id AND m.active = TRUE
                    ),
                    ready_mapping_count = (
                        SELECT COUNT(*) FROM listings m
                        WHERE m.store_id = stores.store_id AND m.active = TRUE
                    ),
                    file_link_status = 'روابط منتجات مباشرة متحقق منها',
                    updated_at = NOW()
                WHERE store_id = %s
                """,
                (request.store_id,),
            )

        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS rows_received,
                COUNT(*) FILTER (WHERE validation_status <> 'rejected') AS rows_accepted,
                COUNT(*) FILTER (WHERE validation_status = 'rejected') AS rows_rejected,
                COUNT(*) FILTER (WHERE validation_status IN ('mapping_created', 'mapping_repaired')) AS rows_matched,
                COUNT(*) FILTER (WHERE validation_status = 'mapping_created') AS mappings_created,
                COUNT(*) FILTER (WHERE validation_status = 'mapping_repaired') AS mappings_repaired,
                COUNT(*) FILTER (WHERE observed_price IS NOT NULL) AS prices_observed,
                COUNT(*) FILTER (WHERE validation_status IN ('pending_match', 'url_variant_conflict')) AS review_count
            FROM import_items
            WHERE import_id = %s::uuid
            """,
            (import_id,),
        ).fetchone()
        status = "completed_with_review" if int(counts["review_count"] or 0) else "completed"
        conn.execute(
            """
            UPDATE import_runs
            SET status = %s,
                rows_received = %s, rows_accepted = %s, rows_rejected = %s,
                rows_matched = %s, mappings_created = %s, mappings_repaired = %s,
                prices_observed = %s, review_count = %s,
                completed_at = NOW(), updated_at = NOW()
            WHERE import_id = %s::uuid
            """,
            (
                status,
                counts["rows_received"],
                counts["rows_accepted"],
                counts["rows_rejected"],
                counts["rows_matched"],
                counts["mappings_created"],
                counts["mappings_repaired"],
                counts["prices_observed"],
                counts["review_count"],
                import_id,
            ),
        )
        stats = _jsonable(dict(counts))
    return {
        "status": status,
        "import_id": import_id,
        **public_preview,
        "dry_run": False,
        "stats": stats,
    }


def catalog_bootstrap_status(*, store_id: str | None = None) -> dict[str, Any]:
    with connection() as conn:
        global_row = conn.execute(
            """
            SELECT
                COUNT(*) AS registered_stores,
                COUNT(*) FILTER (WHERE active_mappings > 0) AS stores_with_mappings,
                COUNT(*) FILTER (WHERE verified_direct_urls > 0) AS stores_with_verified_direct_urls,
                COALESCE(SUM(active_mappings), 0) AS active_mappings,
                COALESCE(SUM(mappings_with_direct_url), 0) AS mappings_with_direct_url,
                COALESCE(SUM(verified_direct_urls), 0) AS verified_direct_urls,
                (SELECT COUNT(*) FROM identity_clusters)
                    AS identity_clusters,
                (SELECT COUNT(*) FROM catalog_observations)
                    AS catalog_observations,
                (SELECT COUNT(*) FROM catalog_observations WHERE publishable)
                    AS publishable_observations,
                ROUND(
                    100.0 * COALESCE(SUM(verified_direct_urls), 0)
                    / NULLIF(COALESCE(SUM(active_mappings), 0), 0),
                    2
                ) AS verified_direct_url_percent
            FROM direct_link_coverage
            """
        ).fetchone()
        stores = conn.execute(
            """
            SELECT * FROM direct_link_coverage
            WHERE (%s::text IS NULL OR store_id = %s)
            ORDER BY verified_direct_url_percent ASC NULLS FIRST,
                     active_mappings DESC, store_name
            LIMIT 216
            """,
            (store_id, store_id),
        ).fetchall()
        runs = conn.execute(
            """
            SELECT import_id::text, provider, external_run_id, store_id, status,
                   rows_received, rows_accepted, rows_rejected, rows_matched,
                   mappings_created, mappings_repaired, prices_observed,
                   review_count, started_at, completed_at
            FROM import_runs
            WHERE (%s::text IS NULL OR store_id = %s)
            ORDER BY created_at DESC LIMIT 25
            """,
            (store_id, store_id),
        ).fetchall()
        return {
            "summary": _jsonable(dict(global_row or {})),
            "stores": [_jsonable(dict(row)) for row in stores],
            "recent_imports": [_jsonable(dict(row)) for row in runs],
        }


def reconcile_catalog_import_observations_batch(
    *,
    after_item_id: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Backfill accepted external rows through the unified identity pipeline."""

    limit = max(1, min(int(limit), 2000))
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                item_id, store_id, source_url, normalized_url, title, brand,
                merchant_sku, manufacturer_sku, gtin,
                observed_price AS price, currency, availability, image_url,
                validation_status, proposed_variant_id, match_method,
                evidence, raw_payload
            FROM import_items
            WHERE validation_status <> 'rejected'
              AND entity_id IS NULL
              AND (CAST(%s AS TEXT) IS NULL OR item_id > %s)
            ORDER BY item_id
            LIMIT %s
            """,
            (after_item_id, after_item_id, limit),
        ).fetchall()
        stats = {
            "processed": 0,
            "entities_linked": 0,
            "variants_created": 0,
            "mappings_created": 0,
            "prices_published": 0,
        }
        last_item_id = after_item_id
        for row in rows:
            item = dict(row)
            last_item_id = str(item["item_id"])
            stats["processed"] += 1
            result = _upsert_catalog_product_observation(
                conn,
                origin_type="catalog_import",
                origin_id=last_item_id,
                store_id=str(item["store_id"]),
                item={**item, "validation_status": "accepted"},
                existing_variant_id=(
                    str(item["proposed_variant_id"]) if item.get("proposed_variant_id") else None
                ),
                match_method=str(item.get("match_method") or "catalog_import_backfill"),
            )
            stats["entities_linked"] += int(bool(result.get("entity_id")))
            stats["variants_created"] += int(bool(result.get("variant_created")))
            stats["mappings_created"] += int(bool(result.get("mapping_created")))
            stats["prices_published"] += int(bool(result.get("published_price")))
        return {
            **stats,
            "last_item_id": last_item_id,
            "has_more": len(rows) == limit,
        }


def reconcile_catalog_candidates_batch(
    *,
    after_candidate_id: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Promote only deterministic matches from the existing discovery backlog."""

    limit = max(1, min(int(limit), 5000))
    with transaction() as conn:
        variant_rows = conn.execute(
            """
            SELECT * FROM variants
            WHERE source_status <> 'catalog_provisional'
            """
        ).fetchall()
        variant_index = build_catalog_variant_index(dict(row) for row in variant_rows)
        rows = conn.execute(
            """
            SELECT candidate_id, entity_id, store_id, normalized_url, source_url, title,
                   brand, sku, gtin, currency, observed_price AS price,
                   availability, source_method, raw_payload AS raw
            FROM discovery_candidates
            WHERE status IN ('pending_match', 'needs_review')
              AND reconcile_version < %s
              AND (CAST(%s AS TEXT) IS NULL OR candidate_id > %s)
            ORDER BY candidate_id
            LIMIT %s
            """,
            (
                CATALOG_CANDIDATE_RECONCILE_VERSION,
                after_candidate_id,
                after_candidate_id,
                limit,
            ),
        ).fetchall()
        processed = 0
        matched = 0
        mappings_created = 0
        mappings_refreshed = 0
        methods: dict[str, int] = {}
        last_candidate_id = after_candidate_id
        for row in rows:
            item = dict(row)
            processed += 1
            last_candidate_id = str(item["candidate_id"])
            conn.execute(
                """
                UPDATE discovery_candidates
                SET reconcile_version = %s, reconcile_checked_at = NOW()
                WHERE candidate_id = %s
                """,
                (
                    CATALOG_CANDIDATE_RECONCILE_VERSION,
                    item["candidate_id"],
                ),
            )
            best, score, method = deterministic_catalog_match(
                variant_index,
                item,
                store_id=str(item["store_id"]),
            )
            if not item.get("entity_id") and (
                item.get("price") is not None or item.get("gtin") or (item.get("brand") and item.get("sku"))
            ):
                observation = _upsert_catalog_product_observation(
                    conn,
                    origin_type="catalog_discovery",
                    origin_id=str(item["candidate_id"]),
                    store_id=str(item["store_id"]),
                    item={**item, "validation_status": "accepted"},
                    existing_variant_id=(str(best["variant_id"]) if best is not None else None),
                    match_method=method or "catalog_discovery_backfill",
                )
                if observation.get("mapping_id"):
                    matched += 1
                    mappings_created += int(bool(observation.get("mapping_created")))
                    mappings_refreshed += int(not bool(observation.get("mapping_created")))
                    methods["unified_catalog_observation"] = methods.get("unified_catalog_observation", 0) + 1
                    continue
            if best is None or method is None:
                continue
            mapping_id, created = _promote_catalog_mapping(
                conn,
                store_id=str(item["store_id"]),
                variant_id=str(best["variant_id"]),
                item=item,
                match_score=float(score),
                match_method=method,
            )
            if not mapping_id:
                continue
            conn.execute(
                """
                UPDATE discovery_candidates
                SET status = 'auto_mapped', proposed_variant_id = %s,
                    mapping_id = %s, match_score = %s, match_method = %s,
                    review_status = 'auto_verified', updated_at = NOW()
                WHERE candidate_id = %s
                """,
                (
                    best["variant_id"],
                    mapping_id,
                    score,
                    method,
                    item["candidate_id"],
                ),
            )
            matched += 1
            mappings_created += int(created)
            mappings_refreshed += int(not created)
            methods[method] = methods.get(method, 0) + 1
        return {
            "processed": processed,
            "matched": matched,
            "mappings_created": mappings_created,
            "mappings_refreshed": mappings_refreshed,
            "methods": methods,
            "last_candidate_id": last_candidate_id,
            "has_more": processed == limit,
        }


def reset_catalog_discovery_sources_due() -> int:
    """Schedule one fresh scan of every enabled store source."""

    with transaction() as conn:
        row = conn.execute(
            """
            WITH reset AS (
                UPDATE discovery_sources
                SET next_scan_at = NOW(), status = 'pending', updated_at = NOW()
                WHERE enabled = TRUE
                RETURNING source_id
            )
            SELECT COUNT(*) AS total FROM reset
            """
        ).fetchone()
        return int(row["total"] or 0)


def get_catalog_discovery_run(run_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        run = conn.execute(
            "SELECT * FROM discovery_runs WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        if not run:
            return None
        tasks = conn.execute(
            """
            SELECT task_id, source_id, store_id, source_url, status,
                   attempt_count, candidates_seen, candidates_new,
                   mappings_created, provisional_products, verified_products,
                   error_code, error_message, metrics, started_at, completed_at
            FROM discovery_tasks
            WHERE run_id = %s
            ORDER BY scheduled_for, task_id
            """,
            (run_id,),
        ).fetchall()
        return {
            "run": _jsonable(dict(run)),
            "tasks": [_jsonable(dict(row)) for row in tasks],
        }


def catalog_sections() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT section, COUNT(*) AS variant_count
            FROM variants
            WHERE section IS NOT NULL AND section <> ''
              AND source_status <> 'catalog_provisional'
            GROUP BY section
            ORDER BY section
            """
        ).fetchall()
        return [_jsonable(dict(row)) for row in rows]


def catalog_brands(section: str | None = None) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT brand, COUNT(*) AS variant_count
            FROM variants
            WHERE brand IS NOT NULL AND brand <> ''
              AND source_status <> 'catalog_provisional'
              AND (CAST(%s AS TEXT) IS NULL OR section = %s)
            GROUP BY brand
            ORDER BY brand
            """,
            (section, section),
        ).fetchall()
        return [_jsonable(dict(row)) for row in rows]


def catalog_product_types(section: str | None = None) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT product_type, COUNT(*) AS variant_count
            FROM variants
            WHERE product_type IS NOT NULL AND product_type <> ''
              AND source_status <> 'catalog_provisional'
              AND (CAST(%s AS TEXT) IS NULL OR section = %s)
            GROUP BY product_type
            ORDER BY product_type
            """,
            (section, section),
        ).fetchall()
        return [_jsonable(dict(row)) for row in rows]


def catalog_models(section: str | None = None, brand: str | None = None) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT model, COUNT(*) AS variant_count
            FROM variants
            WHERE model IS NOT NULL AND model <> ''
              AND source_status <> 'catalog_provisional'
              AND (CAST(%s AS TEXT) IS NULL OR section = %s)
              AND (CAST(%s AS TEXT) IS NULL OR brand = %s)
            GROUP BY model
            ORDER BY model
            """,
            (section, section, brand, brand),
        ).fetchall()
        return [_jsonable(dict(row)) for row in rows]


def catalog_variants(
    *,
    section: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT
                p.variant_id, p.canonical_name, p.section, p.product_type,
                p.brand, p.model, p.variant_name, p.ram_gb, p.storage_gb, p.color,
                s.lowest_cash_price, s.lowest_delivered_total, s.lowest_cash_total,
                s.cash_offer_count, s.installment_plan_count,
                s.lowest_periodic_payment
            FROM variants p
            LEFT JOIN offer_summary s USING (variant_id)
            WHERE p.source_status <> 'catalog_provisional'
              AND (CAST(%s AS TEXT) IS NULL OR p.section = %s)
              AND (CAST(%s AS TEXT) IS NULL OR p.brand = %s)
              AND (CAST(%s AS TEXT) IS NULL OR p.model = %s)
            ORDER BY p.storage_gb NULLS LAST, p.ram_gb NULLS LAST, p.variant_name, p.canonical_name
            LIMIT %s
            """,
            (section, section, brand, brand, model, model, max(1, min(limit, 1000))),
        ).fetchall()
        return [_jsonable(dict(row)) for row in rows]


def search_products(
    query: str,
    *,
    limit: int = 20,
    section: str | None = None,
    brand: str | None = None,
) -> list[dict[str, Any]]:
    public_query = normalize_public_search_query(query)
    pattern = f"%{public_query}%"
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT
                p.variant_id, p.canonical_name, p.section, p.product_type, p.brand,
                p.model, p.variant_name, p.ram_gb, p.storage_gb, p.color,
                s.lowest_cash_price, s.lowest_delivered_total, s.lowest_cash_total,
                s.cash_offer_count, s.installment_plan_count,
                s.lowest_periodic_payment,
                similarity(p.canonical_name, %s) AS relevance
            FROM variants p
            LEFT JOIN offer_summary s USING (variant_id)
            WHERE p.source_status <> 'catalog_provisional'
              AND (%s = '' OR p.canonical_name ILIKE %s OR p.model ILIKE %s OR p.brand ILIKE %s)
              AND (CAST(%s AS TEXT) IS NULL OR p.section = %s)
              AND (CAST(%s AS TEXT) IS NULL OR p.brand = %s)
            ORDER BY
                CASE WHEN p.canonical_name ILIKE %s THEN 0 ELSE 1 END,
                relevance DESC,
                p.canonical_name
            LIMIT %s
            """,
            (
                public_query,
                public_query,
                pattern,
                pattern,
                pattern,
                section,
                section,
                brand,
                brand,
                pattern,
                max(1, min(limit, 100)),
            ),
        ).fetchall()
        return [_jsonable(dict(row)) for row in rows]


def get_product_comparison(variant_id: str, *, include_unpriced: bool = False) -> dict[str, Any] | None:
    with connection() as conn:
        alias = conn.execute(
            """
            SELECT canonical_variant_id
            FROM variant_aliases
            WHERE alias_variant_id = %s
            """,
            (variant_id,),
        ).fetchone()
        canonical_variant_id = alias["canonical_variant_id"] if alias else variant_id
        product = conn.execute(
            """
            SELECT p.*, s.lowest_cash_price, s.lowest_delivered_total,
                   s.lowest_cash_total, s.cash_offer_count,
                   s.installment_plan_count, s.lowest_periodic_payment
            FROM variants p
            LEFT JOIN offer_summary s USING (variant_id)
            WHERE p.variant_id = %s
              AND p.source_status <> 'catalog_provisional'
            """,
            (canonical_variant_id,),
        ).fetchone()
        if not product:
            return None

        cash_rows = conn.execute(
            """
            SELECT *
            FROM public_cash_offers
            WHERE variant_id = %s
              AND (%s OR cash_price IS NOT NULL)
            ORDER BY
                eligible_for_ranking DESC,
                COALESCE(comparable_total, cash_price) NULLS LAST,
                cash_price NULLS LAST,
                CASE computed_freshness WHEN 'fresh' THEN 0 WHEN 'late' THEN 1 WHEN 'stale' THEN 2 ELSE 3 END,
                CASE availability WHEN 'available' THEN 0 WHEN 'limited' THEN 1 WHEN 'preorder' THEN 2 WHEN 'unknown' THEN 3 ELSE 4 END,
                store_name
            """,
            (canonical_variant_id, include_unpriced),
        ).fetchall()

        installment_rows = conn.execute(
            """
            SELECT *
            FROM public_installment_offers
            WHERE variant_id = %s
            ORDER BY
                eligible_for_ranking DESC,
                CASE computed_freshness WHEN 'fresh' THEN 0 WHEN 'late' THEN 1 WHEN 'stale' THEN 2 ELSE 3 END,
                starting_from_only,
                normalized_total NULLS LAST,
                periodic_payment NULLS LAST,
                months NULLS LAST,
                store_name
            """,
            (canonical_variant_id,),
        ).fetchall()

        return {
            "product": _jsonable(dict(product)),
            "cash_offers": [_jsonable(dict(row)) for row in cash_rows],
            "installment_plans": [_jsonable(dict(row)) for row in installment_rows],
        }


def get_run(
    run_id: str,
    *,
    task_limit: int = 500,
    task_offset: int = 0,
) -> dict[str, Any] | None:
    if not 1 <= task_limit <= 500:
        raise ValueError("task_limit must be between 1 and 500")
    if task_offset < 0:
        raise ValueError("task_offset must be non-negative")

    with connection() as conn:
        run = conn.execute("SELECT * FROM price_runs WHERE run_id = %s", (run_id,)).fetchone()
        if not run:
            return None
        total_task_rows = int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM price_tasks WHERE run_id = %s",
                (run_id,),
            ).fetchone()["count"]
        )
        tasks = conn.execute(
            """
            SELECT external_task_id, store_id, source_url, status, scheduled_for,
                   started_at, completed_at, cash_updates, installment_updates,
                   discovered_urls, error_code, error_message
            FROM price_tasks
            WHERE run_id = %s
            ORDER BY scheduled_for, store_id, external_task_id
            LIMIT %s OFFSET %s
            """,
            (run_id, task_limit, task_offset),
        ).fetchall()
        returned_task_rows = len(tasks)
        return {
            "run": _jsonable(dict(run)),
            "tasks": [_jsonable(dict(row)) for row in tasks],
            "pagination": {
                "limit": task_limit,
                "offset": task_offset,
                "returned_task_rows": returned_task_rows,
                "total_task_rows": total_task_rows,
                "has_more": task_offset + returned_task_rows < total_task_rows,
            },
        }


def system_stats() -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT
                (
                    SELECT COUNT(*) FROM variants
                    WHERE source_status <> 'catalog_provisional'
                ) AS products,
                (SELECT COUNT(*) FROM stores) AS registry_stores,
                (SELECT COUNT(*) FROM stores WHERE active) AS active_stores,
                (
                    SELECT COUNT(*) FROM coverage_ledger
                    WHERE price_capable
                ) AS price_capable_stores,
                (
                    SELECT COUNT(*) FROM coverage_ledger
                    WHERE NOT price_capable
                ) AS catalog_only_stores,
                (
                    SELECT COUNT(*) FROM coverage_ledger
                    WHERE discovery_configured
                ) AS catalog_configured_stores,
                (
                    SELECT COUNT(*) FROM coverage_ledger
                    WHERE latest_catalog_scan IS NOT NULL
                ) AS catalog_attempted_stores,
                (
                    SELECT COUNT(*) FROM coverage_ledger
                    WHERE latest_catalog_success IS NOT NULL
                ) AS catalog_successful_stores,
                (
                    SELECT COUNT(*) FROM coverage_ledger
                    WHERE coverage_stage = 'live_price'
                ) AS live_price_coverage_stores,
                (
                    SELECT COUNT(DISTINCT store_id)
                    FROM listings
                    WHERE active
                ) AS connected_stores,
                (
                    SELECT COUNT(DISTINCT store_id)
                    FROM public_cash_offers
                    WHERE eligible_for_ranking
                ) AS priced_stores,
                (
                    SELECT COUNT(DISTINCT variant_id)
                    FROM public_cash_offers
                    WHERE eligible_for_ranking
                ) AS priced_products,
                (SELECT COUNT(*) FROM listings WHERE active) AS active_mappings,
                (
                    SELECT COUNT(*) FROM listings
                    WHERE active AND NULLIF(direct_product_url, '') IS NOT NULL
                ) AS mappings_with_direct_url,
                (
                    SELECT COUNT(*) FROM listings
                    WHERE active AND direct_url_status = 'verified'
                ) AS verified_direct_urls,
                (SELECT COUNT(*) FROM public_cash_offers WHERE eligible_for_ranking) AS priced_cash_offers,
                (SELECT COUNT(DISTINCT store_id) FROM public_cash_offers) AS visible_priced_stores,
                (SELECT COUNT(DISTINCT variant_id) FROM public_cash_offers) AS visible_priced_products,
                (SELECT COUNT(*) FROM public_cash_offers) AS visible_cash_offers,
                (SELECT COUNT(*) FROM public_installment_offers WHERE eligible_for_ranking) AS active_installment_plans,
                (SELECT MAX(last_success_at) FROM current_offers) AS latest_cash_update,
                (SELECT MAX(last_success_at) FROM current_installment_offers) AS latest_installment_update,
                (
                    SELECT run_id::text FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_id,
                (
                    SELECT status FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_status,
                (
                    SELECT started_at FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_started_at,
                (
                    SELECT run_slot FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_slot,
                (
                    SELECT CASE
                        WHEN status = 'enqueue_failed'
                        THEN LEFT(metadata ->> 'enqueue_error', 2000)
                        ELSE NULL
                    END
                    FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_control_error,
                (
                    SELECT completed_at FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_completed_at,
                (
                    SELECT queued_task_count FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_queued_tasks,
                (
                    SELECT completed_task_count FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_completed_tasks,
                (
                    SELECT successful_task_count FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_successful_tasks,
                (
                    SELECT failed_task_count FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_failed_tasks,
                (
                    SELECT COALESCE(
                        jsonb_object_agg(error_code, error_count),
                        '{}'::jsonb
                    )
                    FROM (
                        SELECT error_code, COUNT(*) AS error_count
                        FROM price_tasks
                        WHERE run_id = (
                            SELECT run_id FROM price_runs
                            ORDER BY started_at DESC LIMIT 1
                        )
                          AND error_code IS NOT NULL
                        GROUP BY error_code
                    ) AS latest_price_error_counts
                ) AS latest_price_run_error_codes,
                (
                    SELECT COALESCE(
                        jsonb_object_agg(signature, error_count),
                        '{}'::jsonb
                    )
                    FROM (
                        SELECT
                            CONCAT(
                                error_code,
                                ':',
                                CASE
                                    WHEN (metrics ->> 'failure_location') ~
                                         '^(app|dependency)/[A-Za-z0-9_./-]+:[A-Za-z0-9_<>-]+:[0-9]+$'
                                        THEN metrics ->> 'failure_location'
                                    ELSE 'legacy'
                                END
                            ) AS signature,
                            COUNT(*) AS error_count
                        FROM price_tasks
                        WHERE run_id = (
                            SELECT run_id FROM price_runs
                            ORDER BY started_at DESC LIMIT 1
                        )
                          AND error_code LIKE 'internal_%'
                        GROUP BY signature
                    ) AS latest_price_internal_signatures
                ) AS latest_price_run_internal_error_signatures,
                (
                    SELECT COALESCE(
                        jsonb_object_agg(task_status, task_count),
                        '{}'::jsonb
                    )
                    FROM (
                        SELECT status AS task_status, COUNT(*) AS task_count
                        FROM price_tasks
                        WHERE run_id = (
                            SELECT run_id FROM price_runs
                            ORDER BY started_at DESC LIMIT 1
                        )
                        GROUP BY status
                    ) AS latest_price_task_states
                ) AS latest_price_run_task_states,
                (
                    SELECT cash_updates FROM price_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_price_run_cash_updates,
                (
                    SELECT run_id::text FROM discovery_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_run_id,
                (
                    SELECT status FROM discovery_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_run_status,
                (
                    SELECT queued_task_count FROM discovery_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_run_queued_tasks,
                (
                    SELECT completed_task_count FROM discovery_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_run_completed_tasks,
                (
                    SELECT successful_task_count FROM discovery_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_run_successful_tasks,
                (
                    SELECT failed_task_count FROM discovery_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_run_failed_tasks,
                (
                    SELECT mappings_created FROM discovery_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_run_mappings_created,
                (
                    SELECT candidates_seen FROM discovery_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_run_candidates_seen,
                (
                    SELECT COALESCE(
                        jsonb_object_agg(error_code, error_count),
                        '{}'::jsonb
                    )
                    FROM (
                        SELECT error_code, COUNT(*) AS error_count
                        FROM discovery_tasks
                        WHERE run_id = (
                            SELECT run_id FROM discovery_runs
                            ORDER BY created_at DESC
                            LIMIT 1
                        )
                          AND error_code IS NOT NULL
                        GROUP BY error_code
                    ) AS latest_error_counts
                ) AS latest_catalog_run_error_codes,
                (
                    SELECT COALESCE(
                        jsonb_object_agg(task_status, task_count),
                        '{}'::jsonb
                    )
                    FROM (
                        SELECT status AS task_status, COUNT(*) AS task_count
                        FROM discovery_tasks
                        WHERE run_id = (
                            SELECT run_id FROM discovery_runs
                            ORDER BY created_at DESC LIMIT 1
                        )
                        GROUP BY status
                    ) AS latest_catalog_task_states
                ) AS latest_catalog_run_task_states,
                (
                    SELECT COALESCE(
                        jsonb_agg(to_jsonb(recent_catalog) ORDER BY recent_catalog.created_at DESC),
                        '[]'::jsonb
                    )
                    FROM (
                        SELECT
                            run_id::text AS id,
                            trigger_source AS trigger,
                            status AS state,
                            queued_task_count AS total,
                            completed_task_count AS processed,
                            successful_task_count AS succeeded,
                            failed_task_count AS failed,
                            candidates_seen AS candidates,
                            mappings_created,
                            (
                                SELECT COALESCE(
                                    jsonb_object_agg(error_code, error_count),
                                    '{}'::jsonb
                                )
                                FROM (
                                    SELECT error_code, COUNT(*) AS error_count
                                    FROM discovery_tasks
                                    WHERE run_id = recent_run.run_id
                                      AND error_code IS NOT NULL
                                    GROUP BY error_code
                                ) AS recent_error_counts
                            ) AS error_codes,
                            (
                                SELECT COALESCE(
                                    jsonb_object_agg(task_status, task_count),
                                    '{}'::jsonb
                                )
                                FROM (
                                    SELECT status AS task_status, COUNT(*) AS task_count
                                    FROM discovery_tasks
                                    WHERE run_id = recent_run.run_id
                                    GROUP BY status
                                ) AS recent_task_counts
                            ) AS task_states,
                            created_at,
                            started_at,
                            completed_at
                        FROM discovery_runs AS recent_run
                        ORDER BY created_at DESC
                        LIMIT 5
                    ) AS recent_catalog
                ) AS recent_catalog_runs,
                (
                    SELECT COUNT(*) FROM discovery_sources WHERE enabled
                ) AS catalog_sources,
                (
                    SELECT COUNT(DISTINCT store_id)
                    FROM discovery_sources WHERE enabled
                ) AS catalog_registered_stores,
                (SELECT COUNT(*) FROM discovery_candidates) AS discovery_candidates,
                (
                    SELECT COUNT(*) FROM discovery_candidates
                    WHERE status IN ('needs_review', 'pending_match')
                ) AS catalog_review_candidates,
                (
                    SELECT COUNT(*) FROM discovery_candidates
                    WHERE status = 'pending_match'
                ) AS catalog_pending_match_candidates,
                (
                    SELECT COUNT(*) FROM discovery_candidates
                    WHERE status = 'needs_review'
                ) AS catalog_needs_review_candidates,
                (
                    SELECT COUNT(*) FROM discovery_candidates
                    WHERE status = 'pending_match' AND NULLIF(brand, '') IS NOT NULL
                ) AS catalog_pending_match_with_brand,
                (
                    SELECT COUNT(*) FROM discovery_candidates
                    WHERE status = 'pending_match' AND NULLIF(brand, '') IS NULL
                ) AS catalog_pending_match_without_brand,
                (
                    SELECT COUNT(*) FROM discovery_candidates
                    WHERE status IN ('pending_match', 'needs_review')
                      AND NULLIF(gtin, '') IS NOT NULL
                ) AS catalog_review_with_gtin,
                (
                    SELECT COUNT(*) FROM discovery_candidates
                    WHERE status IN ('pending_match', 'needs_review')
                      AND NULLIF(sku, '') IS NOT NULL
                ) AS catalog_review_with_sku,
                (
                    SELECT COUNT(DISTINCT store_id) FROM discovery_candidates
                    WHERE status IN ('pending_match', 'needs_review')
                ) AS catalog_review_distinct_stores,
                (
                    SELECT COUNT(*) FROM variants
                    WHERE source_status = 'catalog_provisional'
                ) AS catalog_provisional_products,
                (
                    SELECT COUNT(*) FROM variants
                    WHERE source_status = 'catalog_verified'
                ) AS catalog_verified_products,
                (
                    SELECT COUNT(*) FROM variants
                    WHERE source_status = 'catalog_source_verified'
                ) AS catalog_source_verified_products,
                (
                    SELECT COUNT(*) FROM identity_clusters
                ) AS identity_clusters,
                (
                    SELECT COUNT(*) FROM identity_clusters
                    WHERE status = 'source_verified'
                ) AS catalog_source_verified_entities,
                (
                    SELECT COUNT(*) FROM identity_clusters
                    WHERE status = 'cross_store_verified'
                ) AS catalog_cross_store_verified_entities,
                (
                    SELECT COUNT(*) FROM catalog_observations
                ) AS catalog_observations,
                (
                    SELECT COUNT(*) FROM catalog_observations
                    WHERE publishable
                ) AS catalog_publishable_observations,
                (
                    SELECT COALESCE(SUM(recovery_count), 0)
                    FROM discovery_tasks
                ) AS catalog_task_recoveries,
                (
                    SELECT COUNT(*) FROM task_deliveries
                    WHERE status = 'lost'
                ) AS catalog_lost_deliveries,
                (
                    SELECT import_id::text FROM import_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_import_id,
                (
                    SELECT status FROM import_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_import_status,
                (
                    SELECT rows_matched FROM import_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_import_rows_matched,
                (
                    SELECT completed_at FROM import_runs
                    ORDER BY created_at DESC LIMIT 1
                ) AS latest_catalog_import_completed_at
            """
        ).fetchone()
        result = _jsonable(dict(row))
        active_mappings = int(result.get("active_mappings") or 0)
        verified_direct_urls = int(result.get("verified_direct_urls") or 0)
        result["verified_direct_url_percent"] = (
            round(100.0 * verified_direct_urls / active_mappings, 2) if active_mappings else None
        )
        settings = get_settings()
        result["refresh_interval_minutes"] = settings.refresh_interval_minutes
        result["next_update_at"] = next_refresh_at().isoformat()
        result["scheduler_timezone"] = settings.scheduler_timezone
        return result


def admin_dashboard_summary() -> dict[str, Any]:
    """Return a compact operations snapshot for the private admin dashboard."""

    with connection() as conn:
        summary_row = conn.execute("SELECT * FROM data_quality_summary").fetchone()
        review_rows = conn.execute(
            """
            SELECT severity, status, COUNT(*) AS count
            FROM review_cases
            GROUP BY severity, status
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    ELSE 3
                END,
                status
            """
        ).fetchall()
        recent_runs = conn.execute(
            """
            SELECT run_id::text, trigger_source, status, mapping_count,
                   queued_task_count, completed_task_count, failed_task_count,
                   started_at, completed_at
            FROM price_runs
            ORDER BY started_at DESC
            LIMIT 8
            """
        ).fetchall()
        store_attention = conn.execute(
            """
            SELECT
                s.store_id,
                s.name,
                s.priority,
                COUNT(DISTINCT m.mapping_id) FILTER (WHERE m.active) AS mappings,
                MAX(o.last_success_at) AS latest_price_update,
                COALESCE(MAX(c.consecutive_failures), 0) AS connector_failures,
                COUNT(DISTINCT q.review_id) FILTER (WHERE q.status = 'open') AS open_reviews
            FROM stores s
            LEFT JOIN listings m ON m.store_id = s.store_id
            LEFT JOIN current_offers o ON o.store_id = s.store_id AND o.active
            LEFT JOIN connector_configs c ON c.store_id = s.store_id
            LEFT JOIN review_cases q
                ON q.status = 'open'
               AND (
                    (q.entity_type = 'store_product_mapping' AND q.entity_id = m.mapping_id)
                    OR (q.entity_type = 'store' AND q.entity_id = s.store_id)
               )
            WHERE s.active
            GROUP BY s.store_id, s.name, s.priority
            ORDER BY
                COUNT(DISTINCT q.review_id) FILTER (WHERE q.status = 'open') DESC,
                COALESCE(MAX(c.consecutive_failures), 0) DESC,
                MAX(o.last_success_at) ASC NULLS FIRST,
                s.name
            LIMIT 12
            """
        ).fetchall()
        return {
            "summary": _jsonable(dict(summary_row or {})),
            "reviews": [_jsonable(dict(row)) for row in review_rows],
            "recent_runs": [_jsonable(dict(row)) for row in recent_runs],
            "stores_needing_attention": [_jsonable(dict(row)) for row in store_attention],
        }


def admin_review_queue(
    *,
    status: str | None = "open",
    severity: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if offset < 0:
        raise ValueError("offset must be non-negative")

    with connection() as conn:
        total = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM review_cases
            WHERE (%s::text IS NULL OR status = %s)
              AND (%s::text IS NULL OR severity = %s)
            """,
            (status, status, severity, severity),
        ).fetchone()["count"]
        rows = conn.execute(
            """
            SELECT review_id::text, entity_type, entity_id, issue_code,
                   severity, status, title, description, payload,
                   assigned_to, resolution, created_at, updated_at, resolved_at
            FROM review_cases
            WHERE (%s::text IS NULL OR status = %s)
              AND (%s::text IS NULL OR severity = %s)
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    ELSE 3
                END,
                created_at DESC
            LIMIT %s OFFSET %s
            """,
            (status, status, severity, severity, limit, offset),
        ).fetchall()
        return {
            "items": [_jsonable(dict(row)) for row in rows],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(rows),
                "total": int(total),
                "has_more": offset + len(rows) < int(total),
            },
        }


def admin_products(
    *,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    clean_query = (query or "").strip()
    pattern = f"%{clean_query}%"

    with connection() as conn:
        total = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM public_products
            WHERE (%s = '' OR product_name ILIKE %s OR brand_name ILIKE %s OR model ILIKE %s)
            """,
            (clean_query, pattern, pattern, pattern),
        ).fetchone()["count"]
        rows = conn.execute(
            """
            SELECT *
            FROM public_products
            WHERE (%s = '' OR product_name ILIKE %s OR brand_name ILIKE %s OR model ILIKE %s)
            ORDER BY
                connected_store_count DESC,
                cash_offer_count DESC,
                product_name
            LIMIT %s OFFSET %s
            """,
            (clean_query, pattern, pattern, pattern, limit, offset),
        ).fetchall()
        return {
            "items": [_jsonable(dict(row)) for row in rows],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(rows),
                "total": int(total),
                "has_more": offset + len(rows) < int(total),
            },
        }


def resolve_review_item(
    review_id: str,
    *,
    decision: str,
    resolution: str,
    actor: str,
) -> dict[str, Any] | None:
    allowed_decisions = {"resolved", "rejected", "ignored"}
    if decision not in allowed_decisions:
        raise ValueError("decision must be resolved, rejected, or ignored")
    clean_actor = actor.strip()
    clean_resolution = resolution.strip()
    if not clean_actor:
        raise ValueError("actor is required")
    if not clean_resolution:
        raise ValueError("resolution is required")

    with transaction() as conn:
        conn.execute("SELECT set_config('app.actor', %s, TRUE)", (clean_actor,))
        previous = conn.execute(
            "SELECT * FROM review_cases WHERE review_id = %s::uuid FOR UPDATE",
            (review_id,),
        ).fetchone()
        if not previous:
            return None

        entity_type = str(previous["entity_type"])
        entity_id = str(previous["entity_id"])
        entity_before: dict[str, Any] | None = None
        entity_after: dict[str, Any] | None = None

        if entity_type == "store_product_mapping":
            entity_before_row = conn.execute(
                "SELECT * FROM listings WHERE mapping_id = %s FOR UPDATE",
                (entity_id,),
            ).fetchone()
            if entity_before_row:
                entity_before = _jsonable(dict(entity_before_row))
                mapping_status = {
                    "resolved": "approved",
                    "rejected": "rejected",
                    "ignored": "ignored",
                }[decision]
                entity_after_row = conn.execute(
                    """
                    UPDATE listings
                    SET review_status = %s,
                        active = CASE WHEN %s = 'rejected' THEN FALSE ELSE active END,
                        metadata = metadata || %s,
                        updated_at = NOW()
                    WHERE mapping_id = %s
                    RETURNING *
                    """,
                    (
                        mapping_status,
                        decision,
                        Jsonb(
                            {
                                "last_admin_decision": decision,
                                "last_admin_resolution": clean_resolution,
                                "last_admin_actor": clean_actor,
                            }
                        ),
                        entity_id,
                    ),
                ).fetchone()
                entity_after = _jsonable(dict(entity_after_row))

        elif entity_type == "catalog_candidate":
            entity_before_row = conn.execute(
                "SELECT * FROM discovery_candidates WHERE candidate_id = %s FOR UPDATE",
                (entity_id,),
            ).fetchone()
            if entity_before_row:
                entity_before = _jsonable(dict(entity_before_row))
                candidate_status = {
                    "resolved": "review_approved",
                    "rejected": "review_rejected",
                    "ignored": "review_ignored",
                }[decision]
                entity_after_row = conn.execute(
                    """
                    UPDATE discovery_candidates
                    SET status = %s,
                        review_status = %s,
                        review_notes = %s,
                        updated_at = NOW()
                    WHERE candidate_id = %s
                    RETURNING *
                    """,
                    (
                        candidate_status,
                        decision,
                        clean_resolution,
                        entity_id,
                    ),
                ).fetchone()
                entity_after = _jsonable(dict(entity_after_row))

        row = conn.execute(
            """
            UPDATE review_cases
            SET status = %s,
                resolution = %s,
                assigned_to = %s,
                resolved_at = NOW(),
                updated_at = NOW()
            WHERE review_id = %s::uuid
            RETURNING review_id::text, entity_type, entity_id, issue_code,
                      severity, status, title, description, payload,
                      assigned_to, resolution, created_at, updated_at, resolved_at
            """,
            (decision, clean_resolution, clean_actor, review_id),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO audit_events (
                entity_type, entity_id, action, actor, before_data, after_data
            ) VALUES (
                'review_cases', %s, 'review_decision', %s, %s, %s
            )
            """,
            (
                review_id,
                clean_actor,
                Jsonb(_jsonable(dict(previous))),
                Jsonb(_jsonable(dict(row))),
            ),
        )
        if entity_type == "catalog_candidate" and entity_before is not None and entity_after is not None:
            conn.execute(
                """
                INSERT INTO audit_events (
                    entity_type, entity_id, action, actor, before_data, after_data
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    entity_type,
                    entity_id,
                    f"admin_review_{decision}",
                    clean_actor,
                    Jsonb(entity_before),
                    Jsonb(entity_after),
                ),
            )
        return _jsonable(dict(row))
