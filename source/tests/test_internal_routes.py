from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app import routes_internal
from app.schemas import CatalogBootstrapImportRequest, ScrapeGroupPayload
from app.settings import Settings


def payload() -> ScrapeGroupPayload:
    now = datetime(2026, 7, 31, tzinfo=UTC)
    return ScrapeGroupPayload(
        task_id="TASK-1",
        run_id="RUN-1",
        run_slot=now,
        scheduled_for=now,
        store_id="STORE-1",
        store_name="Store One",
        source_url="https://shop.example/product",
        allowed_hosts=["shop.example"],
    )


def test_retryable_task_returns_503_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
):
    class Engine:
        def process(self, *args, **kwargs):
            return {
                "task_id": "TASK-1",
                "status": "retryable_failed",
                "error": "rate_limited",
                "retry_after_seconds": 120,
            }

    monkeypatch.setattr(routes_internal, "ScrapeEngine", Engine)
    monkeypatch.setattr(
        routes_internal,
        "get_settings",
        lambda: Settings(_env_file=None, cloud_tasks_max_attempts=3),
    )

    with pytest.raises(HTTPException) as raised:
        routes_internal.scrape_task(payload(), cloud_tasks_retry_count=0)

    assert raised.value.status_code == 503
    assert raised.value.headers == {"Retry-After": "120"}


def test_run_status_route_is_separate_from_worker_task_routes():
    worker_paths = {route.path for route in routes_internal.router.routes}
    status_paths = {route.path for route in routes_internal.status_router.routes}

    assert "/internal/tasks/scrape" in worker_paths
    assert "/internal/runs/{run_id}" not in worker_paths
    assert status_paths == {
        "/internal/runs/{run_id}",
        "/internal/catalog/runs/{run_id}",
        "/internal/catalog/bootstrap/status",
        "/internal/admin/summary",
        "/internal/admin/review-queue",
        "/internal/admin/products",
        "/internal/admin/review-queue/{review_id}/decision",
    }


def test_run_status_forwards_bounded_pagination(monkeypatch: pytest.MonkeyPatch):
    calls = []

    class Repository:
        def get_run(self, run_id: str, *, task_limit: int, task_offset: int):
            calls.append((run_id, task_limit, task_offset))
            return {
                "run": {"run_id": run_id},
                "tasks": [],
                "pagination": {
                    "limit": task_limit,
                    "offset": task_offset,
                    "returned_task_rows": 0,
                    "total_task_rows": 1149,
                    "has_more": True,
                },
            }

    monkeypatch.setattr(routes_internal, "repository", Repository())

    result = routes_internal.run_status("RUN-1", limit=250, offset=500)

    assert calls == [("RUN-1", 250, 500)]
    assert result["pagination"]["limit"] == 250
    assert result["pagination"]["offset"] == 500


def test_run_status_preserves_404(monkeypatch: pytest.MonkeyPatch):
    class Repository:
        def get_run(self, run_id: str, *, task_limit: int, task_offset: int):
            return None

    monkeypatch.setattr(routes_internal, "repository", Repository())

    with pytest.raises(HTTPException) as raised:
        routes_internal.run_status("MISSING", limit=500, offset=0)

    assert raised.value.status_code == 404


def test_refresh_route_has_legacy_scheduler_alias():
    worker_paths = {route.path for route in routes_internal.router.routes}

    assert "/internal/scheduler/refresh" in worker_paths
    assert "/internal/scheduler/hourly" in worker_paths
    assert "/internal/scheduler/catalog-discovery" in worker_paths
    assert "/internal/scheduler/catalog-recovery" in worker_paths
    assert "/internal/scheduler/price-finalization" in worker_paths
    assert "/internal/tasks/catalog-discovery" in worker_paths
    assert "/internal/catalog/bootstrap/import" in worker_paths


def test_price_finalizer_uses_half_refresh_interval(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[int] = []

    class Repository:
        def finalize_overdue_price_runs(self, deadline_minutes: int):
            calls.append(deadline_minutes)
            return {
                "deadline_minutes": deadline_minutes,
                "runs_finalized": 1,
                "tasks_finalized": 94,
                "run_ids": ["00000000-0000-0000-0000-000000000001"],
            }

    monkeypatch.setattr(routes_internal, "repository", Repository())
    monkeypatch.setattr(
        routes_internal,
        "get_settings",
        lambda: Settings(_env_file=None, refresh_interval_minutes=720),
    )

    result = routes_internal.price_finalization_scheduler()

    assert calls == [355]
    assert result["status"] == "finalized"
    assert result["tasks_finalized"] == 94


def test_catalog_bootstrap_routes_delegate_to_postgres_repository(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple] = []

    class Repository:
        def ingest_catalog_bootstrap(self, request):
            calls.append(("import", request.external_run_id, request.store_id))
            return {"status": "completed", "import_id": "IMPORT-1"}

        def catalog_bootstrap_status(self, **kwargs):
            calls.append(("status", kwargs))
            return {"summary": {"verified_direct_urls": 3}}

    monkeypatch.setattr(routes_internal, "repository", Repository())
    request = CatalogBootstrapImportRequest(
        external_run_id="UWS-RUN-1",
        store_id="EG-013",
        records=[{"url": "https://btech.com/ar/p/example"}],
        dry_run=True,
    )

    imported = routes_internal.catalog_bootstrap_import(request)
    status = routes_internal.catalog_bootstrap_status(store_id="EG-013")

    assert imported["status"] == "completed"
    assert status["summary"]["verified_direct_urls"] == 3
    assert calls == [
        ("import", "UWS-RUN-1", "EG-013"),
        ("status", {"store_id": "EG-013"}),
    ]


def test_admin_routes_delegate_to_repository(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple] = []

    class Repository:
        def admin_dashboard_summary(self):
            calls.append(("summary",))
            return {"summary": {"products": 2}}

        def admin_review_queue(self, **kwargs):
            calls.append(("reviews", kwargs))
            return {"items": [], "pagination": {"total": 0}}

        def admin_products(self, **kwargs):
            calls.append(("products", kwargs))
            return {"items": [], "pagination": {"total": 0}}

        def resolve_review_item(self, review_id: str, **kwargs):
            calls.append(("decision", review_id, kwargs))
            return {"review_id": review_id, "status": kwargs["decision"]}

    monkeypatch.setattr(routes_internal, "repository", Repository())

    assert routes_internal.admin_summary()["summary"]["products"] == 2
    routes_internal.admin_review_queue(status="open", severity="high", limit=25, offset=10)
    routes_internal.admin_products(query="iphone", limit=30, offset=0)
    result = routes_internal.admin_review_decision(
        routes_internal.ReviewDecisionRequest(
            decision="resolved",
            resolution="Verified manually",
            actor="admin@example.com",
        ),
        "00000000-0000-0000-0000-000000000001",
    )

    assert result["status"] == "resolved"
    assert calls == [
        ("summary",),
        ("reviews", {"status": "open", "severity": "high", "limit": 25, "offset": 10}),
        ("products", {"query": "iphone", "limit": 30, "offset": 0}),
        (
            "decision",
            "00000000-0000-0000-0000-000000000001",
            {
                "decision": "resolved",
                "resolution": "Verified manually",
                "actor": "admin@example.com",
            },
        ),
    ]


def test_admin_review_decision_preserves_404(monkeypatch: pytest.MonkeyPatch):
    class Repository:
        def resolve_review_item(self, *args, **kwargs):
            return None

    monkeypatch.setattr(routes_internal, "repository", Repository())

    with pytest.raises(HTTPException) as raised:
        routes_internal.admin_review_decision(
            routes_internal.ReviewDecisionRequest(
                decision="rejected",
                resolution="Not the same product",
                actor="admin@example.com",
            ),
            "00000000-0000-0000-0000-000000000002",
        )

    assert raised.value.status_code == 404


def test_admin_routes_require_postgres_capabilities(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes_internal, "repository", object())

    with pytest.raises(HTTPException) as raised:
        routes_internal.admin_summary()

    assert raised.value.status_code == 503
