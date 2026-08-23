from pathlib import Path

from scripts.copy_legacy_postgres_to_core_v2 import (
    CRITICAL_COPIES,
    NULLABLE_FOREIGN_KEY_REPAIRS,
    OWNED_SEQUENCES,
    TABLE_COPIES,
    TABLE_STAGE_EXIT_CODES,
)

COPY_SCRIPT = Path("scripts/copy_legacy_postgres_to_core_v2.py")
DEPLOYMENT = Path("DEPLOY_PRODUCT_CENTRIC_V0_6_1.sh")
WORKFLOW = Path(".github/workflows/deploy-production.yml")


def test_cutover_copies_price_dependencies_in_foreign_key_order() -> None:
    sources = [spec.source for spec in TABLE_COPIES]

    assert sources.index("product_variants") < sources.index("store_product_mappings")
    assert sources.index("stores") < sources.index("store_product_mappings")
    assert sources.index("store_product_mappings") < sources.index("current_cash_offers")
    assert sources.index("current_cash_offers") < sources.index("cash_offer_history")
    assert set(CRITICAL_COPIES) == {
        "product_variants",
        "stores",
        "store_product_mappings",
        "current_cash_offers",
    }
    assert len(set(TABLE_STAGE_EXIT_CODES.values())) == len(TABLE_COPIES)
    assert TABLE_STAGE_EXIT_CODES["currencies"] == 20
    assert TABLE_STAGE_EXIT_CODES["comparison_shares"] < 70
    assert (
        "governance",
        "audit_events",
        "audit_id",
        "governance.audit_events_audit_id_seq",
    ) in OWNED_SEQUENCES
    assert NULLABLE_FOREIGN_KEY_REPAIRS[("merchant", "listings", "seller_id")] == (
        "merchant",
        "sellers",
        "seller_id",
    )


def test_cutover_is_transactional_validated_and_non_destructive() -> None:
    source = COPY_SCRIPT.read_text(encoding="utf-8")

    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" in source
    assert "pg_advisory_xact_lock" in source
    assert "validate_copy" in source
    assert "CORE_V2_DATA_COPY=PASS" in source
    assert "raise SystemExit(_current_stage_exit_code)" in source
    assert "setval(CAST(%s AS regclass), %s, true)" in source
    assert "if maximum is None:" in source
    assert 'mark_stage(f"sequence-setval:{table_name}", 61)' in source
    assert 'mark_stage("sync-audit-sequences", 69)' in source
    assert "THEN {source_column} ELSE NULL END" in source
    assert "DROP TABLE" not in source
    assert "TRUNCATE" not in source
    assert "DELETE FROM public" not in source


def test_production_deploy_copies_then_smoke_tests_without_forcing_refresh() -> None:
    deployment = DEPLOYMENT.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    copy_position = deployment.index("core-v2-data-copy")
    cutover_position = deployment.index("api-worker-cutover")
    smoke_position = deployment.index("core-v2-api-smoke")
    assert copy_position < cutover_position < smoke_position
    assert "--args=-m,scripts.copy_legacy_postgres_to_core_v2" in deployment
    assert "--args=scripts/copy_legacy_postgres_to_core_v2.py" not in deployment
    assert "Core V2 API smoke test failed" in deployment
    assert "Core V2 copy execution diagnostics" in deployment
    assert 'resource.type=\\"cloud_run_job\\"' in deployment
    assert 'START_PRICE_REFRESH: "0"' in workflow
    assert 'FORCE_PRICE_REFRESH: "1"' not in workflow
