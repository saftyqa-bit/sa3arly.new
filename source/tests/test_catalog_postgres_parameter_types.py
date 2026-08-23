from __future__ import annotations

import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app import catalog, repository

ROOT = Path(__file__).resolve().parents[1]


class EmptyRows:
    def fetchall(self):
        return []


class CapturingConnection:
    def __init__(self):
        self.query = ""
        self.params = ()

    def execute(self, query, params):
        self.query = " ".join(query.split())
        self.params = params
        return EmptyRows()


def test_catalog_match_casts_optional_gtin_and_sku_before_null_checks():
    conn = CapturingConnection()

    result = repository._match_catalog_candidate(
        conn,
        "EG-001",
        {
            "title": "Example phone without structured identifiers",
            "normalized_url": "https://shop.example/products/example-phone",
            "gtin": None,
            "sku": None,
        },
    )

    assert result == (None, -10000.0, None, None)
    assert conn.params[1] is None
    assert conn.params[3] is None
    assert conn.query.count("CAST(%s AS TEXT) IS NOT NULL") == 4


def test_repository_has_no_untyped_null_only_placeholders():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    assert re.search(r"(?<!TEXT\) )%s IS (?:NOT )?NULL", source) is None


def test_catalog_matching_does_not_filter_on_nonexistent_variant_active_column():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    assert "WHERE active = TRUE AND source_status <> 'catalog_provisional'" not in source


def test_manual_catalog_canary_uses_its_requested_minute_slot(monkeypatch):
    scheduled_at = datetime(2026, 8, 4, 0, 17, 42, tzinfo=UTC)
    captured = {}

    class FakeRepository:
        def sync_catalog_discovery_sources(self):
            return 5

        def create_or_get_catalog_discovery_run(
            self, run_slot, trigger, *, full_coverage=False
        ):
            captured["run_slot"] = run_slot
            captured["trigger"] = trigger
            captured["full_coverage"] = full_coverage
            return {"run_id": "00000000-0000-0000-0000-000000000001", "status": "created"}, True

        def load_due_catalog_discovery_sources(
            self, *, limit, include_not_due=False
        ):
            captured["include_not_due"] = include_not_due
            return []

        def mark_catalog_discovery_run_enqueuing(self, *args, **kwargs):
            return None

        def mark_catalog_discovery_run_enqueue_complete(self, *args, **kwargs):
            return None

        def count_due_catalog_discovery_sources(self):
            return 0

    monkeypatch.setattr(catalog, "repository", FakeRepository())

    catalog.create_catalog_discovery_run(
        "catalog-canary-v0.5.1",
        scheduled_at=scheduled_at,
        store_limit=5,
    )

    assert captured["trigger"] == "catalog-canary-v0.5.1"
    assert captured["run_slot"] == datetime(2026, 8, 4, 0, 17, tzinfo=UTC)
    assert captured["include_not_due"] is False


def test_manual_full_catalog_run_can_cover_every_registered_store(monkeypatch):
    scheduled_at = datetime(2026, 8, 5, 12, 41, 22, tzinfo=UTC)
    captured = {}

    class FakeRepository:
        def sync_catalog_discovery_sources(self):
            return 216

        def create_or_get_catalog_discovery_run(
            self, run_slot, trigger, *, full_coverage=False
        ):
            captured["run_slot"] = run_slot
            captured["trigger"] = trigger
            captured["full_coverage"] = full_coverage
            return {"run_id": "00000000-0000-0000-0000-000000000002", "status": "created"}, True

        def load_due_catalog_discovery_sources(
            self, *, limit, include_not_due=False
        ):
            captured["include_not_due"] = include_not_due
            captured["limit"] = limit
            return []

        def mark_catalog_discovery_run_enqueuing(self, *args, **kwargs):
            captured["metadata"] = kwargs["metadata"]

        def mark_catalog_discovery_run_enqueue_complete(self, *args, **kwargs):
            return None

        def count_due_catalog_discovery_sources(self):
            return 216

    monkeypatch.setattr(catalog, "repository", FakeRepository())

    catalog.create_catalog_discovery_run(
        "catalog-full-production",
        scheduled_at=scheduled_at,
        store_limit=209,
    )

    assert captured["run_slot"] == datetime(2026, 8, 5, 12, 41, tzinfo=UTC)
    assert captured["limit"] == 500
    assert captured["include_not_due"] is True
    assert captured["full_coverage"] is True
    assert captured["metadata"]["full_coverage"] is True

def test_public_store_directory_uses_valid_installment_schema():
    source = (ROOT / "app" / "public_stores.py").read_text(encoding="utf-8")

    assert "FROM current_installment_offers" not in source
    assert "FROM public_installment_offers" in source
    assert "WHERE eligible_for_ranking" in source
    assert source.count("CAST(%s AS TEXT) IS NULL") == 2
    assert "%s IS NULL" not in source.replace("CAST(%s AS TEXT) IS NULL", "")
    assert "BOOL_OR(enabled) AS discovery_configured" in source
    assert 'item["coverage_stage"] = "connector_missing"' in source


def test_runtime_does_not_patch_existing_scheduler_oidc_jobs():
    source = (
        ROOT / "scripts" / "ensure_price_collection_runtime.sh"
    ).read_text(encoding="utf-8")
    existing_job_branch = source.split(
        'if gcloud scheduler jobs describe "$job"', 1
    )[1].split("  else", 1)[0]
    new_job_branch = source.split("  else", 1)[1].split("  fi", 1)[0]

    assert "gcloud scheduler jobs update http" not in existing_job_branch
    assert "--oidc-service-account-email" not in existing_job_branch
    assert "--oidc-token-audience" not in existing_job_branch
    assert '--oidc-service-account-email="$SCHEDULER_SA"' in new_job_branch
    assert '--oidc-token-audience="$WORKER_URL"' in new_job_branch

def test_production_nightly_catalog_trigger_covers_every_registered_store(monkeypatch):
    scheduled_at = datetime(2026, 8, 5, 17, 45, 22, tzinfo=UTC)
    captured = {}

    class FakeRepository:
        def sync_catalog_discovery_sources(self):
            return 216

        def create_or_get_catalog_discovery_run(
            self, run_slot, trigger, *, full_coverage=False
        ):
            captured["run_slot"] = run_slot
            captured["trigger"] = trigger
            captured["full_coverage"] = full_coverage
            return {"run_id": "00000000-0000-0000-0000-000000000003", "status": "created"}, True

        def load_due_catalog_discovery_sources(
            self, *, limit, include_not_due=False
        ):
            captured["include_not_due"] = include_not_due
            captured["limit"] = limit
            return []

        def mark_catalog_discovery_run_enqueuing(self, *args, **kwargs):
            captured["metadata"] = kwargs["metadata"]

        def mark_catalog_discovery_run_enqueue_complete(self, *args, **kwargs):
            return None

        def count_due_catalog_discovery_sources(self):
            return 216

    monkeypatch.setattr(catalog, "repository", FakeRepository())

    catalog.create_catalog_discovery_run(
        "catalog-discovery-nightly",
        scheduled_at=scheduled_at,
    )

    assert captured["run_slot"] == datetime(2026, 8, 5, 17, 45, tzinfo=UTC)
    assert captured["limit"] == 500
    assert captured["include_not_due"] is True
    assert captured["full_coverage"] is True
    assert captured["metadata"]["full_coverage"] is True

def test_public_status_reports_sanitized_latest_catalog_error_counts():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    assert "latest_catalog_run_error_codes" in source
    assert "jsonb_object_agg(error_code, error_count)" in source


def test_public_status_reports_verified_direct_link_coverage():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    assert "verified_direct_urls" in source
    assert "verified_direct_url_percent" in source
    assert "latest_catalog_import_status" in source
    assert "latest_catalog_import_rows_matched" in source


def test_verified_direct_links_are_always_used_by_price_refresh():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    active_loader = source[source.index("def load_active_mapping_rows"):source.index("def load_mapping_targets")]
    target_loader = source[source.index("def load_mapping_targets"):source.index("def register_task_run")]
    assert "m.direct_url_status = 'verified'" in active_loader
    assert "THEN m.direct_product_url" in active_loader
    assert "m.direct_url_status = 'verified'" in target_loader
    assert "THEN m.direct_product_url" in target_loader
    assert '"prefer_direct_scrape": True' in source


def test_full_catalog_run_refuses_to_overlap_an_active_registry_scan(monkeypatch):
    scheduled_at = datetime(2026, 8, 10, 10, 15, tzinfo=UTC)

    class FakeRepository:
        def reconcile_stale_catalog_discovery_runs(self, _minutes):
            return 0

        def reconcile_overlapping_catalog_discovery_runs(self):
            return 0

        def sync_catalog_discovery_sources(self):
            return 216

        def create_or_get_catalog_discovery_run(
            self, run_slot, trigger, *, full_coverage=False
        ):
            assert full_coverage is True
            return {
                "run_id": "00000000-0000-0000-0000-000000000099",
                "run_slot": datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
                "status": "running",
                "_overlap_active": True,
            }, False

    monkeypatch.setattr(catalog, "repository", FakeRepository())

    result = catalog.create_catalog_discovery_run(
        "catalog-full-production",
        scheduled_at=scheduled_at,
    )

    assert result["duplicate"] is True
    assert result["status"] == "running"
    assert "already active" in result["message"]


def test_full_catalog_run_replaces_a_partial_registry_scan_after_store_growth():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    creator = source[
        source.index("def create_or_get_catalog_discovery_run") : source.index(
            "def reconcile_stale_catalog_discovery_runs"
        )
    ]
    assert "COUNT(DISTINCT src.store_id) AS total" in creator
    assert "active_store_count >= expected_store_count" in creator
    assert "superseded_incomplete_registry_run" in creator
    assert '"superseded_reason": "registry_growth"' in creator


def test_runtime_does_not_queue_overlapping_refreshes():
    source = (
        ROOT / "scripts" / "ensure_price_collection_runtime.sh"
    ).read_text(encoding="utf-8")

    bash = shutil.which("bash")
    if bash is not None:
        subprocess.run(
            [bash, "-n", str(ROOT / "scripts" / "ensure_price_collection_runtime.sh")],
            check=True,
        )
    assert '"${API_URL}/api/v1/status"' in source
    assert "STATUS_JSON=" in source
    assert "PRICE_RUN_STATE=" in source
    assert "CATALOG_RUN_STATE=" in source
    assert source.count("created|enqueuing|queued|running)") >= 2
    assert "PRICE_REFRESH=ALREADY_ACTIVE" in source
    assert "CATALOG_REFRESH=ALREADY_ACTIVE" in source

def test_catalog_internal_failures_keep_safe_exception_type_codes():
    source = (
        ROOT / "app" / "scraping" / "catalog_discovery.py"
    ).read_text(encoding="utf-8")

    assert 'type(exc).__name__.lower()' in source
    assert 'error_code="internal_error"' not in source
    assert 'return f"internal_{error_type}"[:80]' in source
    assert "error_code = _internal_error_code(exc)" in source



def test_full_catalog_refresh_bypasses_source_cooldowns():
    catalog_source = (ROOT / "app" / "catalog.py").read_text(encoding="utf-8")
    repository_source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    assert "include_not_due=full_coverage" in catalog_source
    assert "include_not_due: bool = False" in repository_source
    assert "CAST(%s AS BOOLEAN) OR src.next_scan_at <= NOW()" in repository_source


def test_public_status_reports_sanitized_latest_price_diagnostics():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    assert "latest_price_run_error_codes" in source
    assert "latest_price_run_task_states" in source
    assert "latest_price_run_internal_error_signatures" in source
    assert "metrics ->> 'failure_location'" in source
    assert "FROM price_tasks" in source
    assert "jsonb_object_agg(error_code, error_count)" in source
    assert "jsonb_object_agg(task_status, task_count)" in source



def test_latest_catalog_status_uses_creation_order_and_recent_runs():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    assert "ORDER BY started_at DESC NULLS LAST, created_at DESC" not in source
    assert source.count("ORDER BY created_at DESC") >= 9
    assert "latest_catalog_run_task_states" in source
    assert "recent_catalog_runs" in source
    assert "LIMIT 5" in source



def test_typed_price_error_signatures_remain_aggregate_only():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")
    aggregate = source[
        source.index("CONCAT(") :
        source.index("AS latest_price_run_internal_error_signatures")
    ]

    assert "error_code LIKE 'internal_%'" in source
    assert "internal_typeerror" not in aggregate
    assert "metrics ->> 'failure_location'" in aggregate
    assert "^(app|dependency)/" in aggregate
    assert "error_message" not in aggregate



def test_catalog_refresh_reconciles_orphans_before_enqueuing():
    catalog_source = (ROOT / "app" / "catalog.py").read_text(encoding="utf-8")
    repository_source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")
    routes_source = (ROOT / "app" / "routes_internal.py").read_text(encoding="utf-8")

    assert "reconcile_stale_catalog_discovery_runs" in catalog_source
    assert "status NOT IN ('success', 'failed')" in repository_source
    assert "stale_task_reconciled" in repository_source
    assert '"completed_with_errors"' in repository_source
    assert "_effective_catalog_schedule_time" in routes_source
    assert "timedelta(minutes=10)" in routes_source

def test_catalog_status_exposes_active_run_errors_and_backlog_shape():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    assert "recent_run.run_id" in source
    assert "recent_error_counts" in source
    assert "recent_task_counts" in source
    assert "catalog_pending_match_candidates" in source
    assert "catalog_needs_review_candidates" in source
    assert "catalog_pending_match_with_brand" in source
    assert "catalog_pending_match_without_brand" in source
    assert "catalog_review_with_gtin" in source
    assert "catalog_review_with_sku" in source
    assert "catalog_review_distinct_stores" in source

def test_catalog_ingestion_gates_expensive_fuzzy_queries_on_match_evidence():
    source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")

    assert "catalog_candidate_has_match_evidence" in source
    assert "if best is None and catalog_candidate_has_match_evidence(" in source

def test_reconciliation_supersedes_only_duplicate_active_full_runs():
    repository_source = (ROOT / "app" / "repository.py").read_text(encoding="utf-8")
    script_source = (
        ROOT / "scripts" / "reconcile_catalog_candidates.py"
    ).read_text(encoding="utf-8")

    assert "reconcile_overlapping_catalog_discovery_runs" in repository_source
    assert "metadata ->> 'full_coverage' = 'true'" in repository_source
    assert "NOT (metadata ? 'superseded_by_run_id')" in repository_source
    assert "ORDER BY COALESCE(completed_task_count, 0) DESC, created_at" in repository_source
    assert "status NOT IN ('success', 'failed')" in repository_source
    assert "keeper_task.error_code LIKE 'internal_%%'" in repository_source
    assert "keeper_task.store_id = duplicate_task.store_id" in repository_source
    assert "superseded_duplicate_run" in repository_source
    assert "superseded_by_run_id" in repository_source
    assert "reconcile_overlapping_catalog_discovery_runs()" in script_source

