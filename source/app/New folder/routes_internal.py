from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query

from app.catalog import create_catalog_discovery_run
from app.catalog_recovery import recover_catalog_discovery_tasks
from app.hourly import create_refresh_run
from app.repository_provider import repository
from app.schemas import (
    CatalogBootstrapImportRequest,
    CatalogDiscoveryTaskPayload,
    CatalogRecoveryRequest,
    CatalogScheduleRequest,
    ReviewDecisionRequest,
    ScheduleRequest,
    ScrapeGroupPayload,
)
from app.scraping.catalog_discovery import CatalogDiscoveryEngine
from app.scraping.engine import ScrapeEngine
from app.security import require_internal_token
from app.settings import get_settings

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)

status_router = APIRouter(
    prefix="/internal",
    tags=["internal-status"],
    dependencies=[Depends(require_internal_token)],
)


def _admin_method(name: str):
    method = getattr(repository, name, None)
    if not callable(method):
        raise HTTPException(
            status_code=503,
            detail="The private admin dashboard requires the PostgreSQL backend",
        )
    return method


def _effective_catalog_schedule_time(
    trigger: str,
    scheduled_at: datetime | None,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Ignore a stale manual-run Scheduler header for full catalog refreshes."""

    if scheduled_at is None:
        return None
    full_coverage = (
        trigger.startswith("catalog-full")
        or trigger == "catalog-discovery-nightly"
    )
    if not full_coverage:
        return scheduled_at
    current = (now or datetime.now(UTC)).astimezone(UTC)
    scheduled = scheduled_at.astimezone(UTC)
    if abs((current - scheduled).total_seconds()) > timedelta(minutes=10).total_seconds():
        return None
    return scheduled_at


def _run_scheduled_refresh(
    request: ScheduleRequest,
    cloud_scheduler_schedule_time: str | None = Header(
        default=None, alias="X-CloudScheduler-ScheduleTime"
    ),
):
    scheduled_at = None
    if cloud_scheduler_schedule_time:
        try:
            scheduled_at = datetime.fromisoformat(
                cloud_scheduler_schedule_time.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid X-CloudScheduler-ScheduleTime header",
            ) from exc
    return create_refresh_run(request.trigger, scheduled_at=scheduled_at)


@router.post("/scheduler/refresh")
def refresh_scheduler(
    request: ScheduleRequest,
    cloud_scheduler_schedule_time: str | None = Header(
        default=None, alias="X-CloudScheduler-ScheduleTime"
    ),
):
    return _run_scheduled_refresh(request, cloud_scheduler_schedule_time)


@router.post("/scheduler/hourly", include_in_schema=False)
def legacy_hourly_scheduler(
    request: ScheduleRequest,
    cloud_scheduler_schedule_time: str | None = Header(
        default=None, alias="X-CloudScheduler-ScheduleTime"
    ),
):
    """Compatibility alias while old Cloud Scheduler configurations are removed."""

    return _run_scheduled_refresh(request, cloud_scheduler_schedule_time)


@router.post("/tasks/scrape")
def scrape_task(
    payload: ScrapeGroupPayload,
    cloud_tasks_retry_count: int = Header(default=0, alias="X-CloudTasks-TaskRetryCount"),
):
    result = ScrapeEngine().process(payload, cloud_tasks_retry_count=cloud_tasks_retry_count)
    if result.get("status") == "retryable_failed":
        settings = get_settings()
        attempt_number = max(1, cloud_tasks_retry_count + 1)
        if attempt_number >= settings.cloud_tasks_max_attempts:
            repository.promote_retry_exhausted(payload.task_id)
            result["status"] = "failed"
            result["retry_exhausted"] = True
            return result
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Transient store failure; Cloud Tasks should retry",
                "task_id": payload.task_id,
                "attempt": attempt_number,
            },
            headers={
                "Retry-After": str(
                    min(
                        max(1, int(result.get("retry_after_seconds") or 10)),
                        settings.max_retry_after_seconds,
                    )
                )
            },
        )
    return result


@router.post("/scheduler/catalog-discovery")
def catalog_discovery_scheduler(
    request: CatalogScheduleRequest,
    cloud_scheduler_schedule_time: str | None = Header(
        default=None, alias="X-CloudScheduler-ScheduleTime"
    ),
):
    scheduled_at = None
    if cloud_scheduler_schedule_time:
        try:
            scheduled_at = datetime.fromisoformat(
                cloud_scheduler_schedule_time.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid X-CloudScheduler-ScheduleTime header",
            ) from exc
    scheduled_at = _effective_catalog_schedule_time(
        request.trigger,
        scheduled_at,
    )
    return create_catalog_discovery_run(
        request.trigger,
        scheduled_at=scheduled_at,
        store_limit=request.store_limit,
    )


@router.post("/scheduler/catalog-recovery")
def catalog_recovery_scheduler(request: CatalogRecoveryRequest):
    return recover_catalog_discovery_tasks(limit=request.limit)


@router.post("/scheduler/price-finalization")
def price_finalization_scheduler():
    settings = get_settings()
    result = repository.finalize_overdue_price_runs(
        settings.price_run_finalization_deadline_minutes
    )
    return {
        "status": "finalized" if result["runs_finalized"] else "idle",
        **result,
    }


@router.post("/tasks/catalog-discovery")
def catalog_discovery_task(
    payload: CatalogDiscoveryTaskPayload,
    cloud_tasks_retry_count: int = Header(default=0, alias="X-CloudTasks-TaskRetryCount"),
):
    result = CatalogDiscoveryEngine().process(
        payload,
        cloud_tasks_retry_count=cloud_tasks_retry_count,
    )
    if result.get("status") == "retryable_failed":
        settings = get_settings()
        attempt_number = max(1, cloud_tasks_retry_count + 1)
        if attempt_number >= settings.cloud_tasks_max_attempts:
            repository.promote_catalog_retry_exhausted(payload.task_id)
            result["status"] = "failed"
            result["retry_exhausted"] = True
            return result
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Transient catalog source failure; Cloud Tasks should retry",
                "task_id": payload.task_id,
                "attempt": attempt_number,
            },
            headers={
                "Retry-After": str(
                    min(
                        max(1, int(result.get("retry_after_seconds") or 10)),
                        settings.max_retry_after_seconds,
                    )
                )
            },
        )
    return result


@router.post("/catalog/bootstrap/import")
def catalog_bootstrap_import(request: CatalogBootstrapImportRequest):
    try:
        return _admin_method("ingest_catalog_bootstrap")(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@status_router.get("/runs/{run_id}")
def run_status(
    run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    data = repository.get_run(
        run_id,
        task_limit=limit,
        task_offset=offset,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return data


@status_router.get("/catalog/runs/{run_id}")
def catalog_run_status(run_id: str):
    data = repository.get_catalog_discovery_run(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Catalog discovery run not found")
    return data


@status_router.get("/catalog/bootstrap/status")
def catalog_bootstrap_status(
    store_id: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
):
    return _admin_method("catalog_bootstrap_status")(store_id=store_id)


@status_router.get("/admin/summary")
def admin_summary():
    return _admin_method("admin_dashboard_summary")()


@status_router.get("/admin/review-queue")
def admin_review_queue(
    status: Annotated[str | None, Query(pattern="^(open|in_review|resolved|rejected|ignored)$")] = "open",
    severity: Annotated[str | None, Query(pattern="^(low|medium|high|critical)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return _admin_method("admin_review_queue")(
        status=status,
        severity=severity,
        limit=limit,
        offset=offset,
    )


@status_router.get("/admin/products")
def admin_products(
    query: Annotated[str | None, Query(max_length=160)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return _admin_method("admin_products")(
        query=query, limit=limit, offset=offset
    )


@status_router.post("/admin/review-queue/{review_id}/decision")
def admin_review_decision(
    request: ReviewDecisionRequest,
    review_id: Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{36}$")],
):
    item = _admin_method("resolve_review_item")(
        review_id,
        decision=request.decision,
        resolution=request.resolution,
        actor=request.actor,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    return item
