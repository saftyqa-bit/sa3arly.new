from pathlib import Path


def test_manual_price_refresh_uses_an_independent_cloud_run_job() -> None:
    workflow = Path(".github/workflows/start-price-refresh.yml").read_text(encoding="utf-8")

    assert "concurrency:" in workflow
    assert "sa3arly-price-refresh-start" in workflow
    assert "schedule:" in workflow
    assert 'cron: "5 7,8,17,18 * * *"' in workflow
    assert 'gcloud run jobs execute "${{ steps.before.outputs.control_job }}"' in workflow
    assert "sa3arly-price-refresh-force-start" in workflow
    assert 'MAX_URL_GROUPS_PER_RUN: "50000"' in workflow
    assert 'gcloud run jobs update "$control_job"' in workflow
    assert '--update-env-vars="MAX_URL_GROUPS_PER_RUN=$MAX_URL_GROUPS_PER_RUN"' in workflow
    assert 'WORKER_MAX_INSTANCES: "48"' in workflow
    assert 'WORKER_CONTAINER_CONCURRENCY: "2"' in workflow
    assert 'PRICE_MAX_CONCURRENT_DISPATCHES: "80"' in workflow
    assert 'gcloud run services update "$WORKER_SERVICE"' in workflow
    assert '--concurrency="$WORKER_CONTAINER_CONCURRENCY"' in workflow
    assert 'gcloud tasks queues update "$PRICE_QUEUE"' in workflow
    assert 'if [[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]]' in workflow
    assert "PREVIOUS_PRICE_REFRESH_RUN_ID" in workflow
    assert "previous_run_status" in workflow
    assert "PRICE_REFRESH_QUEUED_TASKS" in workflow
    assert '"$PREVIOUS_RUN_STATUS" == "enqueue_failed"' in workflow
    assert "PRICE_REFRESH_RESUMED=true" in workflow
    assert "PRICE_REFRESH_SLOT=ALREADY_PRESENT" in workflow
    assert "PRICE_REFRESH_SLOT=ALREADY_COMPLETE" in workflow
    assert "Print bounded Cloud Run execution diagnostics" in workflow
    assert "gcloud run jobs executions describe" in workflow
    assert "gcloud run jobs executions tasks list" in workflow
    assert "gcloud run jobs executions tasks describe" in workflow
    assert "gcloud logging read" not in workflow
    assert "/internal/scheduler/refresh" not in workflow
    assert "token_format: id_token" not in workflow
    assert "gcloud secrets" not in workflow
    assert "INTERNAL_TOKEN" not in workflow
