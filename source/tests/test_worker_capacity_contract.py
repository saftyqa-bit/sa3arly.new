from pathlib import Path

DEPLOYMENT = Path("DEPLOY_PRODUCT_CENTRIC_V0_6_1.sh")
RUNTIME = Path("scripts/ensure_price_collection_runtime.sh")


def test_worker_capacity_leaves_headroom_for_control_routes() -> None:
    deployment = DEPLOYMENT.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")

    assert 'WORKER_MAX_INSTANCES="${WORKER_MAX_INSTANCES:-48}"' in deployment
    assert 'WORKER_MAX_INSTANCES="${WORKER_MAX_INSTANCES:-48}"' in runtime
    assert 'WORKER_CONTAINER_CONCURRENCY="${WORKER_CONTAINER_CONCURRENCY:-2}"' in runtime
    assert '--max="$WORKER_MAX_INSTANCES"' in runtime
    assert '--concurrency="$WORKER_CONTAINER_CONCURRENCY"' in runtime
    assert 'ensure_queue "$PRICE_QUEUE" 12 80' in runtime
    assert 'ensure_queue "$CATALOG_QUEUE" 10 10' in runtime


def test_price_refresh_start_has_an_independent_control_plane() -> None:
    deployment = DEPLOYMENT.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")

    assert 'PRICE_START_JOB="${PRICE_START_JOB:-sa3arly-price-refresh-start}"' in deployment
    assert 'deploy_price_control_job "$PRICE_START_JOB" current' in deployment
    assert 'deploy_price_control_job "$PRICE_FORCE_JOB" next' in deployment
    assert 'gcloud run jobs deploy "$job_name"' in deployment
    assert "scripts.start_price_refresh_job" in deployment
    assert '--service-account="$WORKER_SA"' in deployment
    assert '--set-cloudsql-instances="$INSTANCE_CONNECTION"' in deployment
    assert "TASKS_MODE=cloud" in deployment
    assert "PRICE_REFRESH_SLOT_MODE=${slot_mode}" in deployment
    assert "MAX_URL_GROUPS_PER_RUN=50000" in deployment
    assert "PRICE_REFRESH_SCHEDULE=MANAGED_BY_INDEPENDENT_CLOUD_RUN_JOBS" in runtime
    assert 'gcloud scheduler jobs run "$PRICE_SCHEDULER_JOB"' not in runtime
    assert '"${FORCE_PRICE_REFRESH:-0}" == "1"' in runtime
    assert "PRICE_REFRESH=FORCE_REQUESTED:${PRICE_FORCE_JOB}" in runtime


def test_finalizer_and_failed_retry_have_independent_cloud_run_jobs() -> None:
    deployment = DEPLOYMENT.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")

    assert 'PRICE_FINALIZER_JOB="${PRICE_FINALIZER_JOB:-sa3arly-price-run-finalizer}"' in deployment
    assert "scripts.finalize_price_runs_job" in deployment
    assert 'PRICE_RETRY_JOB="${PRICE_RETRY_JOB:-sa3arly-price-failed-retry}"' in deployment
    assert "scripts.retry_failed_price_tasks_job" in deployment
    assert 'PRICE_FINALIZER_SCHEDULER_JOB="${PRICE_FINALIZER_SCHEDULER_JOB:-sa3arly-price-run-finalizer}"' in deployment
    assert "https://run.googleapis.com/v2/projects/" in deployment
    assert '--schedule="*/5 * * * *"' in deployment
    assert '--oauth-service-account-email="$WORKER_SA"' in deployment
    assert "PRICE_RUN_FINALIZATION=MANAGED_BY_CLOUD_SCHEDULER_JOB" in runtime
