from pathlib import Path


def test_manual_catalog_refresh_starts_full_private_worker_run() -> None:
    workflow = Path(".github/workflows/start-catalog-refresh.yml").read_text(
        encoding="utf-8"
    )

    assert '"catalog-full-manual"' in workflow
    assert '"store_limit":500' in workflow
    assert '"$WORKER_URL/internal/scheduler/catalog-discovery"' in workflow
    assert "token_format: id_token" in workflow
    assert "id_token_audience: ${{ steps.runtime.outputs.worker_url }}" in workflow
    assert 'Authorization: Bearer $ID_TOKEN' in workflow
    assert "X-CloudScheduler-ScheduleTime" not in workflow
    assert "CATALOG_REFRESH_ALREADY_ACTIVE" in workflow
    assert 'result.get("task_count") or 0' in workflow
