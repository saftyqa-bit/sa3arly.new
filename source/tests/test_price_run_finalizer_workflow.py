from pathlib import Path


def test_price_run_finalizer_is_independent_and_recurring() -> None:
    workflow = Path(".github/workflows/finalize-price-runs.yml").read_text(encoding="utf-8")

    assert 'cron: "*/5 * * * *"' in workflow
    assert "sa3arly-price-run-finalizer" in workflow
    assert 'gcloud run jobs execute "$PRICE_FINALIZER_JOB"' in workflow
    assert "/internal/scheduler/price-finalization" not in workflow
    assert "token_format: id_token" not in workflow
    assert "latest_price_run_completed_tasks" in workflow
    assert "gcloud tasks queues purge" not in workflow
    assert "INTERNAL_TOKEN" not in workflow
