from pathlib import Path


def test_failed_price_retry_is_manual_bounded_and_independent() -> None:
    workflow = Path(".github/workflows/retry-failed-price-tasks.yml").read_text(encoding="utf-8")

    assert "source_run_id:" in workflow
    assert "sa3arly-price-failed-retry" in workflow
    assert "scripts.retry_failed_price_tasks_job" in workflow
    assert 'gcloud run jobs execute "$PRICE_RETRY_JOB"' in workflow
    assert "latest_price_run_queued_tasks" in workflow
    assert '[[ "$latest_run_id" == "$SOURCE_RUN_ID" ]]' in workflow
    assert "completed_with_errors" in workflow
    assert 'gcloud tasks queues pause "$PRICE_QUEUE"' in workflow
    assert 'gcloud tasks queues purge "$PRICE_QUEUE"' in workflow
    assert 'gcloud tasks queues resume "$PRICE_QUEUE"' in workflow
    assert 'PRICE_MAX_CONCURRENT_DISPATCHES: "80"' in workflow
    assert "/internal/scheduler/refresh" not in workflow
