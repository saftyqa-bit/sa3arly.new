from pathlib import Path

WORKFLOW = Path(".github/workflows/recover-catalog-deliveries.yml")
RUNTIME = Path("scripts/ensure_price_collection_runtime.sh")


def test_recovery_uses_existing_github_oidc_identity_every_fifteen_minutes() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'cron: "*/15 * * * *"' in workflow
    assert "token_format: id_token" in workflow
    assert "id_token_audience: ${{ steps.runtime.outputs.worker_url }}" in workflow
    assert 'Authorization: Bearer $ID_TOKEN' in workflow
    assert '"$WORKER_URL/internal/scheduler/catalog-recovery"' in workflow
    assert "int(result.get(\"failed\") or 0) != 0" in workflow


def test_runtime_does_not_require_scheduler_service_account_act_as() -> None:
    runtime = RUNTIME.read_text(encoding="utf-8")

    assert "CATALOG_RECOVERY=MANAGED_BY_GITHUB_OIDC_SCHEDULE" in runtime
    assert "CATALOG_RECOVERY_SCHEDULER_JOB" not in runtime
    assert '"*/15 * * * *"' not in runtime
