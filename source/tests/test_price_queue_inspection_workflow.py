from pathlib import Path


def test_price_queue_inspection_is_read_only_and_bounded() -> None:
    workflow = Path(".github/workflows/inspect-price-queue.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "gcloud tasks queues describe sa3arly-scrape" in workflow
    assert "gcloud tasks list --queue=sa3arly-scrape" in workflow
    assert "--sort-by=scheduleTime --limit=50" in workflow
    assert "PRICE_QUEUE_INSPECTION=" in workflow
    assert "gcloud tasks queues update" not in workflow
    assert "gcloud tasks delete" not in workflow
    assert "gcloud tasks queues purge" not in workflow
