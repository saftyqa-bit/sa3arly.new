from pathlib import Path


def test_price_dispatch_capacity_preserves_six_worker_slots_of_headroom() -> None:
    workflow = Path(".github/workflows/start-price-refresh.yml").read_text(
        encoding="utf-8"
    )
    runtime = Path("scripts/ensure_price_collection_runtime.sh").read_text(
        encoding="utf-8"
    )

    assert 'WORKER_MAX_INSTANCES: "48"' in workflow
    assert 'WORKER_CONTAINER_CONCURRENCY: "2"' in workflow
    assert 'PRICE_MAX_CONCURRENT_DISPATCHES: "80"' in workflow
    assert 'ensure_queue "$PRICE_QUEUE" 12 80' in runtime
    assert 'ensure_queue "$CATALOG_QUEUE" 10 10' in runtime
    assert 48 * 2 - 80 - 10 == 6
    assert '--update-env-vars="DB_POOL_MIN=1,DB_POOL_MAX=2"' in workflow
