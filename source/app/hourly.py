from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from app.repository_provider import repository
from app.schedule import refresh_slot_at
from app.schemas import ScrapeGroupPayload
from app.settings import get_settings
from app.tasks_client import TaskEnqueuer

logger = logging.getLogger(__name__)

TASK_ID_SCHEME = "legacy-first-full-group-suffix-v2"
TASK_NAME_COLLISION_REPAIR_TRIGGER = "repair-task-name-collision"


def _slot_now(scheduled_at: datetime | None = None) -> datetime:
    return refresh_slot_at(scheduled_at)


def _legacy_task_id(run_slot: datetime, key: tuple) -> str:
    store_id, source_url = key[0], key[1]
    legacy_stable = hashlib.sha1(f"{run_slot.isoformat()}|{store_id}|{source_url}".encode()).hexdigest()
    return f"{run_slot:%Y%m%d%H}-{store_id}-{legacy_stable[:16]}"


def _task_id_for_group(
    run_slot: datetime,
    key: tuple,
    *,
    legacy_occurrence: int,
) -> str:
    """Keep legacy IDs stable and disambiguate repeated store/URL groups.

    The original ID intentionally remains unchanged for the first occurrence so
    an interrupted legacy enqueue can be resumed without duplicating tasks that
    already exist. Groups after the first receive a suffix derived from every
    field that participates in grouping.
    """

    legacy_task_id = _legacy_task_id(run_slot, key)
    if legacy_occurrence == 0:
        return legacy_task_id

    full_group_signature = json.dumps(
        [run_slot.isoformat(), *key],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    full_group_hash = hashlib.sha1(full_group_signature.encode("utf-8")).hexdigest()
    return f"{legacy_task_id}-{full_group_hash[:12]}"


def create_refresh_run(
    trigger: str = "scheduler",
    scheduled_at: datetime | None = None,
    *,
    retry_source_run_id: str | None = None,
) -> dict:
    settings = get_settings()
    repository.reconcile_stale_runs(settings.stale_task_after_minutes)
    if retry_source_run_id:
        repository.repair_terminal_price_run(retry_source_run_id)
        run_slot = (scheduled_at or datetime.now(UTC)).astimezone(UTC)
    else:
        run_slot = _slot_now(scheduled_at)
    run, created = repository.create_or_get_run(run_slot, trigger)
    existing_status = str(run.get("status") or "created")
    run_metadata = run.get("metadata") or {}
    enqueue_complete = bool(run_metadata.get("enqueue_complete"))
    collision_repair_eligible = (
        not retry_source_run_id
        and not created
        and trigger == TASK_NAME_COLLISION_REPAIR_TRIGGER
        and existing_status in {"queued", "enqueuing"}
        and enqueue_complete
        and run_metadata.get("task_id_scheme") != TASK_ID_SCHEME
        and int(run.get("completed_task_count") or 0) == 0
        and int(run.get("successful_task_count") or 0) == 0
        and int(run.get("failed_task_count") or 0) == 0
    )
    resumable = (
        existing_status in {"created", "enqueue_failed"} or not enqueue_complete or collision_repair_eligible
    )
    if not created and not resumable:
        return {
            "run_id": str(run["run_id"]),
            "run_slot": run_slot.isoformat(),
            "status": existing_status,
            "duplicate": True,
            "message": "This hourly slot is already active or complete; no duplicate tasks were queued.",
        }

    run_id = str(run["run_id"])
    resumed = not created
    rows = (
        repository.load_failed_mapping_rows(retry_source_run_id)
        if retry_source_run_id
        else repository.load_active_mapping_rows()
    )
    grouped: dict[tuple, list[dict]] = defaultdict(list)

    for row in rows:
        effective_url = row.get("effective_source_url") or row["source_url"]
        effective_url_type = row.get("effective_url_type") or row.get("url_type") or ""
        key = (
            row["store_id"],
            effective_url,
            effective_url_type,
            tuple(row.get("allowed_hosts") or []),
            row.get("connector_mode") or "auto",
            row.get("connector_version") or "generic-v1",
            bool(row.get("respect_robots", True)),
            bool(row.get("browser_required", False)),
            int(row.get("requests_per_minute") or 10),
            int(row.get("max_concurrency") or 1),
        )
        grouped[key].append(row)

    groups = list(grouped.items())
    if collision_repair_eligible:
        existing_identities = repository.load_registered_task_identities(run_id)
        legacy_positions: dict[str, list[int]] = defaultdict(list)
        for index, (key, _group_rows) in enumerate(groups):
            legacy_positions[_legacy_task_id(run_slot, key)].append(index)

        for legacy_task_id, positions in legacy_positions.items():
            if len(positions) < 2:
                continue
            existing = existing_identities.get(legacy_task_id)
            if not existing:
                raise RuntimeError(f"Collision repair cannot identify existing task {legacy_task_id}")
            matches = [
                position
                for position in positions
                if groups[position][0][1] == existing.get("source_url")
                and (groups[position][0][2] or "") == (existing.get("url_type") or "")
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    f"Collision repair could not uniquely match the existing payload for {legacy_task_id}"
                )
            first_position = positions[0]
            matched_position = matches[0]
            if matched_position != first_position:
                groups[first_position], groups[matched_position] = (
                    groups[matched_position],
                    groups[first_position],
                )
    if len(groups) > settings.max_url_groups_per_run:
        raise RuntimeError(
            f"Scheduled run has {len(groups)} URL groups, above the configured "
            f"MAX_URL_GROUPS_PER_RUN={settings.max_url_groups_per_run}; refusing "
            "to truncate coverage silently"
        )
    if settings.tasks_mode == "inline" and len(groups) > settings.inline_max_url_groups:
        raise RuntimeError(
            f"Inline mode is limited to {settings.inline_max_url_groups} URL groups "
            "for deterministic local tests. Use Cloud Tasks or a real local queue "
            "for an operational scheduled refresh."
        )
    task_payloads: list[ScrapeGroupPayload] = []
    mapping_count = 0
    store_positions: dict[str, int] = defaultdict(int)
    legacy_task_occurrences: dict[str, int] = defaultdict(int)
    disambiguated_task_count = 0
    max_scheduled_offset_seconds = 0
    scheduling_window_seconds = settings.price_run_scheduling_window_seconds
    enqueue_started_at = datetime.now(UTC)

    for key, group_rows in groups:
        (
            store_id,
            source_url,
            url_type,
            allowed_hosts_tuple,
            connector_mode,
            connector_version,
            respect_robots,
            browser_required,
            requests_per_minute,
            max_concurrency,
        ) = key
        mapping_count += len(group_rows)
        legacy_stable = hashlib.sha1(f"{run_slot.isoformat()}|{store_id}|{source_url}".encode()).hexdigest()
        spacing_seconds = max(1.0, 60.0 / max(requests_per_minute, 1))
        position = store_positions[store_id]
        store_positions[store_id] += 1
        deterministic_micro_jitter = int(legacy_stable[:4], 16) % max(int(spacing_seconds), 1)
        offset_seconds = int(position * spacing_seconds + deterministic_micro_jitter)
        if offset_seconds > scheduling_window_seconds:
            required_minutes = round(offset_seconds / 60.0, 2)
            available_minutes = round(scheduling_window_seconds / 60.0, 2)
            message = (
                "Price refresh cannot fit inside its half-interval runtime budget: "
                f"store {store_id} needs {required_minutes} minutes but only "
                f"{available_minutes} are available. Increase this store's safe RPM, "
                "consolidate its source feed, or split the connector before retrying."
            )
            repository.mark_run_enqueue_failed(
                run_id,
                message,
                successfully_queued=0,
                planned_tasks=len(groups),
            )
            raise RuntimeError(message)
        max_scheduled_offset_seconds = max(max_scheduled_offset_seconds, offset_seconds)
        scheduled_for = enqueue_started_at + timedelta(seconds=offset_seconds)
        legacy_task_id = f"{run_slot:%Y%m%d%H}-{store_id}-{legacy_stable[:16]}"
        legacy_occurrence = legacy_task_occurrences[legacy_task_id]
        legacy_task_occurrences[legacy_task_id] += 1
        task_id = _task_id_for_group(
            run_slot,
            key,
            legacy_occurrence=legacy_occurrence,
        )
        if legacy_occurrence:
            disambiguated_task_count += 1
        first = group_rows[0]
        task_payloads.append(
            ScrapeGroupPayload(
                task_id=task_id,
                run_id=run_id,
                run_slot=run_slot,
                scheduled_for=scheduled_for,
                store_id=store_id,
                store_name=first["store_name"],
                source_url=source_url,
                url_type=url_type,
                allowed_hosts=list(allowed_hosts_tuple),
                connector_mode=connector_mode,
                connector_version=connector_version,
                connector_config=first.get("connector_config") or {},
                requests_per_minute=requests_per_minute,
                max_concurrency=max_concurrency,
                respect_robots=respect_robots,
                browser_required=browser_required,
                mapping_ids=[row["mapping_id"] for row in group_rows],
            )
        )

    # Cloud Tasks receives tasks in due-time order instead of one store at a
    # time.  This prevents a large catalog from occupying every worker request
    # with advisory-lock waiters while other stores are starved.
    task_payloads.sort(
        key=lambda payload: (
            payload.scheduled_for,
            payload.store_id,
            payload.task_id,
        )
    )
    task_ids = [payload.task_id for payload in task_payloads]
    if len(set(task_ids)) != len(task_ids):
        raise RuntimeError("Task ID generation produced duplicates after full-group disambiguation")

    repository.mark_run_enqueuing(
        run_id,
        mapping_count=mapping_count,
        url_group_count=len(groups),
        queued_task_count=len(task_payloads),
        metadata={
            "refresh_interval_minutes": settings.refresh_interval_minutes,
            "configured_legacy_stagger_seconds": settings.task_stagger_seconds,
            "scheduling_window_seconds": scheduling_window_seconds,
            "max_scheduled_offset_seconds": max_scheduled_offset_seconds,
            "timezone": settings.scheduler_timezone,
            "resumed_after_enqueue_failure": resumed,
            "task_id_scheme": TASK_ID_SCHEME,
            "task_id_collisions_disambiguated": disambiguated_task_count,
            "collision_repair_requested": collision_repair_eligible,
            "retry_source_run_id": retry_source_run_id,
            "retry_failed_tasks_only": bool(retry_source_run_id),
        },
    )

    enqueuer = TaskEnqueuer()
    queued = 0
    registered_task_count = 0
    try:
        for payload in task_payloads:
            repository.register_task_run(payload)
            enqueuer.enqueue(payload)
            queued += 1
        registered_task_count = repository.count_registered_tasks(run_id)
        if registered_task_count != len(task_payloads):
            raise RuntimeError(
                "Registered task row count does not match the planned URL groups: "
                f"{registered_task_count} != {len(task_payloads)}"
            )
    except Exception as exc:
        logger.exception("Failed while enqueuing refresh run", extra={"run_id": run_id})
        repository.mark_run_enqueue_failed(
            run_id,
            str(exc),
            successfully_queued=queued,
            planned_tasks=len(task_payloads),
        )
        raise

    repository.mark_run_enqueue_complete(run_id)

    return {
        "run_id": run_id,
        "run_slot": run_slot.isoformat(),
        "status": "queued" if settings.tasks_mode == "cloud" else "processed_inline",
        "duplicate": False,
        "resumed": resumed,
        "mapping_count": mapping_count,
        "unique_url_groups": len(groups),
        "task_count": queued,
        "registered_task_count": registered_task_count,
        "task_id_scheme": TASK_ID_SCHEME,
        "task_id_collisions_disambiguated": disambiguated_task_count,
        "collision_repair": collision_repair_eligible,
        "staggered_over_seconds": max_scheduled_offset_seconds,
        "scheduling_window_seconds": scheduling_window_seconds,
        "retry_source_run_id": retry_source_run_id,
    }


# Backward-compatible import for older deployment helpers. New code should use
# create_refresh_run; the scheduler route itself is also kept as a legacy alias.
create_hourly_run = create_refresh_run
