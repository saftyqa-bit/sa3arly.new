from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from app.repository_provider import repository
from app.schedule import catalog_discovery_slot_at
from app.schemas import CatalogDiscoveryTaskPayload
from app.settings import get_settings
from app.tasks_client import TaskEnqueuer


def create_catalog_discovery_run(
    trigger: str = "scheduler",
    *,
    scheduled_at: datetime | None = None,
    store_limit: int | None = None,
) -> dict:
    settings = get_settings()
    reconcile = getattr(repository, "reconcile_stale_catalog_discovery_runs", None)
    if callable(reconcile):
        reconcile(settings.stale_task_after_minutes)
    # The production nightly job is the authenticated full-registry trigger.
    # Full coverage runs use a minute slot so a manual post-deploy request does
    # not collide with an earlier scheduled run on the same day.
    full_coverage = (
        trigger.startswith("catalog-full")
        or trigger == "catalog-discovery-nightly"
    )
    reconcile_overlaps = getattr(
        repository,
        "reconcile_overlapping_catalog_discovery_runs",
        None,
    )
    if full_coverage and callable(reconcile_overlaps):
        reconcile_overlaps()
    if trigger.startswith("catalog-canary") or full_coverage:
        run_slot = (scheduled_at or datetime.now(UTC)).astimezone(UTC).replace(
            second=0,
            microsecond=0,
        )
    else:
        run_slot = catalog_discovery_slot_at(scheduled_at)
    repository.sync_catalog_discovery_sources()
    run, created = repository.create_or_get_catalog_discovery_run(
        run_slot,
        trigger,
        full_coverage=full_coverage,
    )
    if bool(run.get("_overlap_active")):
        return {
            "run_id": str(run["run_id"]),
            "run_slot": str(run.get("run_slot") or run_slot.isoformat()),
            "status": str(run.get("status") or "running"),
            "duplicate": True,
            "message": (
                "A full catalog refresh is already active; no overlapping "
                "registry scan was queued."
            ),
        }
    if not created and str(run.get("status")) not in {"created", "enqueue_failed"}:
        return {
            "run_id": str(run["run_id"]),
            "run_slot": run_slot.isoformat(),
            "status": str(run.get("status") or "unknown"),
            "duplicate": True,
        }

    maximum = (
        settings.catalog_discovery_full_store_limit
        if full_coverage
        else settings.catalog_discovery_stores_per_run
    )
    # Legacy scheduler bodies still send store_limit=209.  A full-coverage
    # trigger deliberately ignores that historical registry size and loads
    # every eligible source up to the safety ceiling.
    limit = maximum if full_coverage else min(
        max(1, int(store_limit or maximum)),
        maximum,
    )
    sources = repository.load_due_catalog_discovery_sources(
        limit=limit,
        include_not_due=full_coverage,
    )
    run_id = str(run["run_id"])
    payloads: list[CatalogDiscoveryTaskPayload] = []
    now = datetime.now(UTC)
    for position, row in enumerate(sources):
        signature = f"{run_slot.isoformat()}|{row['source_id']}"
        task_hash = hashlib.sha1(signature.encode("utf-8")).hexdigest()
        payloads.append(
            CatalogDiscoveryTaskPayload(
                task_id=f"CAT-{run_slot:%Y%m%d%H%M}-{task_hash[:20]}",
                run_id=run_id,
                run_slot=run_slot,
                scheduled_for=now + timedelta(seconds=min(position * 20, 900)),
                source_id=row["source_id"],
                store_id=row["store_id"],
                store_name=row["store_name"],
                source_url=row["source_url"],
                source_type=row.get("source_type") or "auto",
                allowed_hosts=list(row.get("allowed_hosts") or []),
                connector_version=row.get("connector_version") or "catalog-generic-v1",
                connector_config=row.get("connector_config") or {},
                requests_per_minute=max(1, int(row.get("requests_per_minute") or 6)),
                max_concurrency=1,
                respect_robots=bool(row.get("respect_robots", True)),
                # Catalog scans use public HTTP/sitemaps first. Browser actions
                # remain available only when the connector explicitly requires it.
                browser_required=bool(row.get("browser_required", False)),
            )
        )

    repository.mark_catalog_discovery_run_enqueuing(
        run_id,
        source_count=len(payloads),
        metadata={
            "store_limit": limit,
            "full_coverage": full_coverage,
            "rescan_hours": settings.catalog_discovery_rescan_hours,
            "scheduler_timezone": settings.scheduler_timezone,
        },
    )
    enqueuer = TaskEnqueuer()
    queued = 0
    try:
        for payload in payloads:
            repository.register_catalog_discovery_task(payload)
            queue_task_name = enqueuer.enqueue_catalog(payload)
            mark_enqueued = getattr(repository, "mark_catalog_task_enqueued", None)
            if callable(mark_enqueued):
                mark_enqueued(
                    payload.task_id,
                    delivery_generation=payload.delivery_generation,
                    queue_task_name=queue_task_name,
                )
            queued += 1
    except Exception as exc:
        repository.mark_catalog_discovery_run_enqueue_failed(
            run_id,
            str(exc),
            successfully_queued=queued,
            planned_tasks=len(payloads),
        )
        raise
    repository.mark_catalog_discovery_run_enqueue_complete(run_id)
    return {
        "run_id": run_id,
        "run_slot": run_slot.isoformat(),
        "status": "queued" if settings.tasks_mode == "cloud" else "processed_inline",
        "duplicate": False,
        "source_count": len(payloads),
        "task_count": queued,
        "stores_remaining_after_batch": max(
            0, repository.count_due_catalog_discovery_sources() - len(payloads)
        ),
    }
