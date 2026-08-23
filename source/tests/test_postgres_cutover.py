from __future__ import annotations

import ast
import re
from decimal import Decimal
from pathlib import Path

from scripts.migrate_firestore_runtime_to_postgres import (
    SourceDocument,
    minor_to_amount,
    source_counts,
    verify_counts,
)

ROOT = Path(__file__).resolve().parents[1]


def deployment_source() -> str:
    wrapper = (ROOT / "infra" / "gcp" / "deploy.sh").read_text(encoding="utf-8")
    implementation = (ROOT / "infra" / "gcp" / "deploy_legacy_impl.sh").read_text(
        encoding="utf-8"
    )
    assert "deploy_legacy_impl.sh" in wrapper
    return f"{wrapper}\n{implementation}"


def test_minor_money_is_converted_exactly() -> None:
    assert minor_to_amount(12345) == Decimal("123.45")
    assert minor_to_amount("50") == Decimal("0.50")
    assert minor_to_amount(None) is None


def test_source_counts_include_runtime_and_archive_collections() -> None:
    def docs(name: str, count: int, *, active: bool = False) -> list[SourceDocument]:
        return [
            SourceDocument(name, str(index), {"active": active})
            for index in range(count)
        ]

    source = {
        "product_variants": docs("product_variants", 2),
        "stores": docs("stores", 1),
        "mappings": docs("mappings", 2, active=True),
        "cash_offers": docs("cash_offers", 2),
        "installment_plans": docs("installment_plans", 1),
        "cash_offer_history": docs("cash_offer_history", 3),
        "installment_plan_history": docs("installment_plan_history", 4),
        "installment_discovery": docs("installment_discovery", 2),
        "scrape_runs": docs("scrape_runs", 1),
        "scrape_task_runs": docs("scrape_task_runs", 2),
        "system": docs("system", 1),
    }
    summary = source_counts(source)
    assert summary["active_mappings"] == 2
    assert summary["installment_discovery"] == 2
    assert summary["archived_run_records"] == 4


def test_cutover_verification_requires_every_runtime_row_to_match() -> None:
    source = {
        "products": 2,
        "stores": 1,
        "mappings": 2,
        "active_mappings": 2,
        "cash_offers": 2,
        "priced_cash_offers": 1,
        "installment_plans": 1,
        "installment_discovery": 2,
        "cash_history": 1,
        "installment_history": 1,
        "archived_run_records": 2,
    }
    target = {
        **source,
        "orphan_mappings": 0,
        "orphan_cash_offers": 0,
        "orphan_installment_plans": 0,
    }
    migrated = {
        "mapping_runtime": 2,
        "cash_offers": 2,
        "installment_plans": 1,
        "installment_discovery": 2,
        "legacy_archive": 2,
    }
    assert verify_counts(source, target, migrated)["ok"] is True

    migrated["cash_offers"] = 1
    verification = verify_counts(source, target, migrated)
    assert verification["ok"] is False
    assert verification["checks"]["cash_runtime_matched"] is False


def test_migration_execute_calls_have_matching_positional_parameters() -> None:
    path = ROOT / "scripts" / "migrate_firestore_runtime_to_postgres.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "execute" or len(node.args) < 2:
            continue
        sql_node, params_node = node.args[:2]
        if not isinstance(sql_node, ast.Constant) or not isinstance(sql_node.value, str):
            continue
        if "%s" not in sql_node.value or not isinstance(params_node, ast.Tuple):
            continue
        checked += 1
        assert len(re.findall(r"(?<!%)%s", sql_node.value)) == len(params_node.elts)
    assert checked >= 10


def test_production_scripts_preserve_twice_daily_price_cadence() -> None:
    deploy = deployment_source()
    activate = (ROOT / "START_SA3ARLY_COLLECTION.sh").read_text(encoding="utf-8")
    hourly_cutover = (ROOT / "ENABLE_HOURLY_COLLECTION.sh").read_text(encoding="utf-8")
    catalog_deploy = (ROOT / "DEPLOY_CATALOG_DISCOVERY.sh").read_text(encoding="utf-8")
    assert 'REFRESH_CRON=0 10,20 * * *' in deploy
    assert '--schedule="0 10,20 * * *"' in deploy
    assert "pause_queue_if_present" in deploy
    assert 'pause_scheduler_if_present "$SCHEDULER_JOB"' in deploy
    assert 'WORKER_URL="https://${WORKER_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"' in deploy
    assert 'SCHEDULER_JOB="${SCHEDULER_JOB:-sa3arly-hourly-refresh}"' in activate
    assert 'env.get("REFRESH_INTERVAL_MINUTES") == "720"' in activate
    assert 'job.get("schedule") == "0 10,20 * * *"' in activate
    assert "legacy hourly activator is disabled" in hourly_cutover
    assert 'PRICE_SCHEDULE" != "0 10,20 * * *"' in catalog_deploy
    assert "CATALOG_SCHEDULER=PAUSED" in catalog_deploy


def test_catalog_v051_hotfix_is_worker_only_and_keeps_discovery_paused() -> None:
    hotfix = (ROOT / "DEPLOY_CATALOG_HOTFIX_V0_5_1.sh").read_text(encoding="utf-8")

    assert 'PRICE_SCHEDULE" != "0 10,20 * * *"' in hotfix
    assert 'CATALOG_STATE" != "PAUSED"' in hotfix
    assert 'gcloud run services update "$WORKER_SERVICE"' in hotfix
    assert "API_SERVICE=" not in hotfix
    assert 'gcloud scheduler jobs resume "$CATALOG_SCHEDULER_JOB"' not in hotfix
    assert 'FINAL_CATALOG_STATE" != "PAUSED"' in hotfix
    assert '"catalog-canary-v0.5.1"' in hotfix


def test_cloud_sql_uses_enterprise_custom_shape_explicitly() -> None:
    deploy = deployment_source()
    assert "--edition=enterprise" in deploy
    assert '--cpu="$DB_CPU"' in deploy
    assert '--memory="$DB_MEMORY"' in deploy
    assert '--tier="$DB_TIER"' not in deploy


def test_bootstrap_batches_use_psycopg_cursor() -> None:
    bootstrap = (ROOT / "scripts" / "bootstrap_db.py").read_text(encoding="utf-8")
    assert "conn.executemany(" not in bootstrap
    assert "with conn.cursor() as cursor:" in bootstrap
    assert "cursor.executemany(statement, payload)" in bootstrap
    assert bootstrap.count("execute_many(") == 15
