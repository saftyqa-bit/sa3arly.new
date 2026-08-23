#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
API_SERVICE="${API_SERVICE:-sa3arly-api}"
WORKER_SERVICE="${WORKER_SERVICE:-sa3arly-worker}"
PRICE_SCHEDULER_JOB="${PRICE_SCHEDULER_JOB:-sa3arly-hourly-refresh}"
CATALOG_SCHEDULER_JOB="${CATALOG_SCHEDULER_JOB:-sa3arly-catalog-discovery}"
PRICE_QUEUE="${PRICE_QUEUE:-sa3arly-scrape}"
CATALOG_QUEUE="${CATALOG_QUEUE:-sa3arly-catalog-discovery}"
REPOSITORY="${REPOSITORY:-sa3arly}"
DB_INSTANCE="${DB_INSTANCE:-sa3arly-postgres}"
DB_SECRET="${DB_SECRET:-sa3arly-database-url}"
MIGRATION_JOB="${MIGRATION_JOB:-sa3arly-product-centric-migrations}"
CORE_V2_COPY_JOB="${CORE_V2_COPY_JOB:-sa3arly-core-v2-copy}"
RECONCILE_JOB="${RECONCILE_JOB:-sa3arly-catalog-reconcile}"
PRICE_START_JOB="${PRICE_START_JOB:-sa3arly-price-refresh-start}"
PRICE_FORCE_JOB="${PRICE_FORCE_JOB:-sa3arly-price-refresh-force-start}"
PRICE_FINALIZER_JOB="${PRICE_FINALIZER_JOB:-sa3arly-price-run-finalizer}"
PRICE_FINALIZER_SCHEDULER_JOB="${PRICE_FINALIZER_SCHEDULER_JOB:-sa3arly-price-run-finalizer}"
PRICE_RETRY_JOB="${PRICE_RETRY_JOB:-sa3arly-price-failed-retry}"
BOOTSTRAP_SA="${BOOTSTRAP_SA:-sa3arly-bootstrap@${PROJECT_ID}.iam.gserviceaccount.com}"
TASKS_SA="${TASKS_SA:-sa3arly-tasks@${PROJECT_ID}.iam.gserviceaccount.com}"
WORKER_MAX_INSTANCES="${WORKER_MAX_INSTANCES:-48}"
WORKER_CONTAINER_CONCURRENCY="${WORKER_CONTAINER_CONCURRENCY:-2}"
RELEASE_ID="${RELEASE_ID:-product-centric-v0-6-1-$(date -u +%Y%m%d-%H%M%S)}"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"
API_IMAGE="${IMAGE_BASE}/api:${RELEASE_ID}"
WORKER_IMAGE="${IMAGE_BASE}/worker:${RELEASE_ID}"
IMAGE_BUILD_MODE="${IMAGE_BUILD_MODE:-cloud-build}"

command -v gcloud >/dev/null || { echo "gcloud is required" >&2; exit 69; }
gcloud config set project "$PROJECT_ID" >/dev/null

service_revision() {
  gcloud run services describe "$1" --region="$REGION" --project="$PROJECT_ID" \
    --format='value(status.traffic[0].revisionName)'
}

scheduler_state() {
  gcloud scheduler jobs describe "$1" --location="$REGION" --project="$PROJECT_ID" \
    --format='value(state)' 2>/dev/null || true
}

queue_state() {
  gcloud tasks queues describe "$1" --location="$TASKS_LOCATION" --project="$PROJECT_ID" \
    --format='value(state)' 2>/dev/null || true
}

PREVIOUS_API_REVISION="$(service_revision "$API_SERVICE")"
PREVIOUS_WORKER_REVISION="$(service_revision "$WORKER_SERVICE")"
WORKER_SA="$(gcloud run services describe "$WORKER_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" \
  --format='value(spec.template.spec.serviceAccountName)')"
[[ -n "$WORKER_SA" ]] || { echo "Worker service account is required" >&2; exit 2; }
PRICE_SCHEDULER_STATE="$(scheduler_state "$PRICE_SCHEDULER_JOB")"
CATALOG_SCHEDULER_STATE="$(scheduler_state "$CATALOG_SCHEDULER_JOB")"
PRICE_QUEUE_STATE="$(queue_state "$PRICE_QUEUE")"
CATALOG_QUEUE_STATE="$(queue_state "$CATALOG_QUEUE")"
CUTOVER_STARTED=0
PHASE="preflight"

restore_runtime_state() {
  local name state
  for name in "$PRICE_SCHEDULER_JOB:$PRICE_SCHEDULER_STATE" "$CATALOG_SCHEDULER_JOB:$CATALOG_SCHEDULER_STATE"; do
    state="${name#*:}"; name="${name%%:*}"
    [[ -n "$state" ]] || continue
    if [[ "$state" == "ENABLED" ]]; then
      gcloud scheduler jobs resume "$name" --location="$REGION" --project="$PROJECT_ID" >/dev/null
    else
      gcloud scheduler jobs pause "$name" --location="$REGION" --project="$PROJECT_ID" >/dev/null
    fi
  done
  for name in "$PRICE_QUEUE:$PRICE_QUEUE_STATE" "$CATALOG_QUEUE:$CATALOG_QUEUE_STATE"; do
    state="${name#*:}"; name="${name%%:*}"
    [[ -n "$state" ]] || continue
    if [[ "$state" == "RUNNING" ]]; then
      gcloud tasks queues resume "$name" --location="$TASKS_LOCATION" --project="$PROJECT_ID" >/dev/null
    else
      gcloud tasks queues pause "$name" --location="$TASKS_LOCATION" --project="$PROJECT_ID" >/dev/null
    fi
  done
}

rollback() {
  local rc="$?"
  trap - ERR
  set +e
  if [[ "$CUTOVER_STARTED" == "1" ]]; then
    [[ -z "$PREVIOUS_API_REVISION" ]] || gcloud run services update-traffic "$API_SERVICE" \
      --region="$REGION" --project="$PROJECT_ID" \
      --to-revisions="${PREVIOUS_API_REVISION}=100"
    [[ -z "$PREVIOUS_WORKER_REVISION" ]] || gcloud run services update-traffic "$WORKER_SERVICE" \
      --region="$REGION" --project="$PROJECT_ID" \
      --to-revisions="${PREVIOUS_WORKER_REVISION}=100"
  fi
  restore_runtime_state
  echo "PRODUCT_CENTRIC_FAILED_PHASE=${PHASE}" >&2
  echo "PRODUCT_CENTRIC_DEPLOYMENT=ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback ERR

BACKEND="$(gcloud run services describe "$API_SERVICE" --region="$REGION" --project="$PROJECT_ID" \
  --format=json | python3 -c 'import json,sys
x=json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env",[])
print(next((i.get("value","") for i in x if i.get("name")=="PERSISTENCE_BACKEND"),""))')"
[[ "$BACKEND" == "postgres" ]] || { echo "PostgreSQL backend is required" >&2; exit 2; }

PHASE="build-api-image"
echo "Building API and worker images..."
if [[ "$IMAGE_BUILD_MODE" == "local" ]]; then
  command -v docker >/dev/null || { echo "docker is required for local image builds" >&2; exit 69; }
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
  docker build --file Dockerfile.api --tag "$API_IMAGE" .
  docker push "$API_IMAGE"
  PHASE="build-worker-image"
  docker build --file Dockerfile.worker --tag "$WORKER_IMAGE" .
  docker push "$WORKER_IMAGE"
else
  gcloud builds submit --project="$PROJECT_ID" --config=infra/gcp/cloudbuild-api.yaml \
    --substitutions="_IMAGE=${API_IMAGE}" .
  PHASE="build-worker-image"
  gcloud builds submit --project="$PROJECT_ID" --config=infra/gcp/cloudbuild-worker.yaml \
    --substitutions="_IMAGE=${WORKER_IMAGE}" .
fi

PHASE="cloud-sql-backup"
echo "Creating an on-demand Cloud SQL backup..."
gcloud sql backups create --instance="$DB_INSTANCE" --project="$PROJECT_ID" --quiet

for scheduler in "$PRICE_SCHEDULER_JOB" "$CATALOG_SCHEDULER_JOB"; do
  [[ -z "$(scheduler_state "$scheduler")" ]] || gcloud scheduler jobs pause "$scheduler" \
    --location="$REGION" --project="$PROJECT_ID" >/dev/null
done
for queue in "$PRICE_QUEUE" "$CATALOG_QUEUE"; do
  [[ -z "$(queue_state "$queue")" ]] || gcloud tasks queues pause "$queue" \
    --location="$TASKS_LOCATION" --project="$PROJECT_ID" >/dev/null
done

INSTANCE_CONNECTION="${PROJECT_ID}:${REGION}:${DB_INSTANCE}"
PHASE="schema-migrations"
gcloud run jobs deploy "$MIGRATION_JOB" --project="$PROJECT_ID" --region="$REGION" \
  --image="$API_IMAGE" --service-account="$BOOTSTRAP_SA" \
  --set-cloudsql-instances="$INSTANCE_CONNECTION" \
  --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
  --command=python --args=scripts/apply_migrations.py \
  --cpu=1 --memory=1Gi --max-retries=0 --task-timeout=1200s
gcloud run jobs execute "$MIGRATION_JOB" --project="$PROJECT_ID" --region="$REGION" --wait

PHASE="core-v2-data-copy"
gcloud run jobs deploy "$CORE_V2_COPY_JOB" --project="$PROJECT_ID" --region="$REGION" \
  --image="$API_IMAGE" --service-account="$BOOTSTRAP_SA" \
  --set-cloudsql-instances="$INSTANCE_CONNECTION" \
  --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
  --command=python --args=-m,scripts.copy_legacy_postgres_to_core_v2 \
  --cpu=2 --memory=2Gi --max-retries=0 --task-timeout=3600s
if ! gcloud run jobs execute "$CORE_V2_COPY_JOB" \
  --project="$PROJECT_ID" --region="$REGION" --wait; then
  COPY_EXECUTION="$(
    gcloud run jobs executions list \
      --job="$CORE_V2_COPY_JOB" --project="$PROJECT_ID" --region="$REGION" \
      --sort-by='~metadata.creationTimestamp' --limit=1 \
      --format='value(metadata.name)'
  )"
  echo "Core V2 copy execution diagnostics: ${COPY_EXECUTION}"
  gcloud run jobs executions describe "$COPY_EXECUTION" \
    --project="$PROJECT_ID" --region="$REGION" --format=json || true
  gcloud run jobs executions tasks list \
    --execution="$COPY_EXECUTION" --project="$PROJECT_ID" --region="$REGION" \
    --format=json || true
  gcloud logging read \
    "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${CORE_V2_COPY_JOB}\"" \
    --project="$PROJECT_ID" --freshness=2h --order=asc --limit=200 \
    --format='value(timestamp,severity,textPayload,jsonPayload.message)' || true
  false
fi

PHASE="catalog-reconciliation"
gcloud run jobs deploy "$RECONCILE_JOB" --project="$PROJECT_ID" --region="$REGION" --image="$API_IMAGE" --service-account="$BOOTSTRAP_SA" --set-cloudsql-instances="$INSTANCE_CONNECTION" --set-secrets="DATABASE_URL=${DB_SECRET}:latest" --command=python --args=-m,scripts.reconcile_catalog_job --cpu=2 --memory=2Gi --max-retries=0 --task-timeout=3600s --quiet
if gcloud run jobs execute "$RECONCILE_JOB" \
  --project="$PROJECT_ID" --region="$REGION" --wait; then
  RECONCILE_RC=0
else
  RECONCILE_RC="$?"
fi
RECONCILE_EXECUTION="$(gcloud run jobs executions list --job="$RECONCILE_JOB" --project="$PROJECT_ID" --region="$REGION" --sort-by='~metadata.creationTimestamp' --limit=1 --format='value(metadata.name)')"
RECONCILE_TASK="$(gcloud run jobs executions tasks list --execution="$RECONCILE_EXECUTION" --project="$PROJECT_ID" --region="$REGION" --limit=1 --format='value(metadata.name)')"
echo "Catalog reconciliation task status:"
gcloud run jobs executions tasks describe "$RECONCILE_TASK" --project="$PROJECT_ID" --region="$REGION" --format=json || true
[[ "$RECONCILE_RC" == "0" ]] || false
PHASE="api-worker-cutover"
CUTOVER_STARTED=1
gcloud run services update "$API_SERVICE" --project="$PROJECT_ID" --region="$REGION" \
  --image="$API_IMAGE"
gcloud run services update "$WORKER_SERVICE" --project="$PROJECT_ID" --region="$REGION" \
  --image="$WORKER_IMAGE" \
  --max="$WORKER_MAX_INSTANCES" \
  --concurrency="$WORKER_CONTAINER_CONCURRENCY" \
  --update-env-vars="DB_POOL_MIN=1,DB_POOL_MAX=2"
gcloud run services update-traffic "$API_SERVICE" --project="$PROJECT_ID" --region="$REGION" --to-latest
gcloud run services update-traffic "$WORKER_SERVICE" --project="$PROJECT_ID" --region="$REGION" --to-latest

PHASE="api-readiness"
for service in "$API_SERVICE" "$WORKER_SERVICE"; do
  revision_state="$(gcloud run services describe "$service" \
    --region="$REGION" --project="$PROJECT_ID" \
    --format='value(status.latestCreatedRevisionName,status.latestReadyRevisionName)')"
  created_revision="${revision_state%%$'\t'*}"
  ready_revision="${revision_state#*$'\t'}"
  [[ -n "$created_revision" && "$created_revision" == "$ready_revision" ]] || {
    echo "$service latest revision is not ready: $revision_state" >&2
    false
  }
done

PHASE="core-v2-api-smoke"
API_URL="$(gcloud run services describe "$API_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" \
  --format='value(status.url)')"
READY_RESPONSE="$(curl --fail-with-body --silent --show-error \
  --retry 8 --retry-delay 3 --max-time 120 "${API_URL}/readyz")"
STATUS_RESPONSE="$(curl --fail-with-body --silent --show-error \
  --retry 8 --retry-delay 3 --max-time 120 "${API_URL}/api/v1/status")"
python3 - "$READY_RESPONSE" "$STATUS_RESPONSE" <<'PY'
import json
import sys

ready = json.loads(sys.argv[1])
status = json.loads(sys.argv[2])
checks = {
    "ready": ready.get("ok") is True,
    "products": int(status.get("products") or 0) >= 1_000,
    "stores": int(status.get("registry_stores") or 0) >= 216,
    "mappings": int(status.get("active_mappings") or 0) >= 1_000,
    "latest_cash_update": bool(status.get("latest_cash_update")),
}
print(json.dumps({"checks": checks}, indent=2))
if not all(checks.values()):
    raise SystemExit("Core V2 API smoke test failed")
PY

PHASE="price-refresh-control-jobs"
WORKER_URL="$(gcloud run services describe "$WORKER_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" \
  --format='value(status.url)')"
[[ "$WORKER_URL" == https://* ]] || {
  echo "Could not resolve the production worker URL" >&2
  false
}
deploy_price_control_job() {
  local job_name="$1"
  local slot_mode="$2"
  gcloud run jobs deploy "$job_name" \
    --project="$PROJECT_ID" --region="$REGION" \
    --image="$WORKER_IMAGE" \
    --service-account="$WORKER_SA" \
    --set-cloudsql-instances="$INSTANCE_CONNECTION" \
    --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
    --set-env-vars="^|^SERVICE_MODE=worker|PERSISTENCE_BACKEND=postgres|TASKS_MODE=cloud|GCP_PROJECT_ID=${PROJECT_ID}|GCP_REGION=${REGION}|CLOUD_TASKS_LOCATION=${TASKS_LOCATION}|CLOUD_TASKS_QUEUE=${PRICE_QUEUE}|CATALOG_TASKS_QUEUE=${CATALOG_QUEUE}|TASKS_SERVICE_ACCOUNT_EMAIL=${TASKS_SA}|WORKER_URL=${WORKER_URL}|SCHEDULER_TIMEZONE=Africa/Cairo|REFRESH_INTERVAL_MINUTES=720|FRESHNESS_MINUTES=855|STALE_AFTER_MINUTES=1710|TASK_STAGGER_SECONDS=2700|STALE_TASK_AFTER_MINUTES=180|MAX_URL_GROUPS_PER_RUN=50000|DB_POOL_MIN=1|DB_POOL_MAX=4|PRICE_REFRESH_SLOT_MODE=${slot_mode}|INTERNAL_TOKEN=" \
    --command=python --args=-m,scripts.start_price_refresh_job \
    --cpu=1 --memory=2Gi --max-retries=0 --task-timeout=3600s --quiet
}
deploy_price_control_job "$PRICE_START_JOB" current
deploy_price_control_job "$PRICE_FORCE_JOB" next

PHASE="price-finalizer-control-job"
gcloud run jobs deploy "$PRICE_FINALIZER_JOB" \
  --project="$PROJECT_ID" --region="$REGION" \
  --image="$API_IMAGE" \
  --service-account="$WORKER_SA" \
  --set-cloudsql-instances="$INSTANCE_CONNECTION" \
  --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
  --set-env-vars="PERSISTENCE_BACKEND=postgres,REFRESH_INTERVAL_MINUTES=720,PRICE_RUN_FINALIZER_CADENCE_MINUTES=5,DB_POOL_MIN=1,DB_POOL_MAX=4" \
  --command=python --args=-m,scripts.finalize_price_runs_job \
  --cpu=1 --memory=1Gi --max-retries=1 --task-timeout=900s --quiet
gcloud run jobs add-iam-policy-binding "$PRICE_FINALIZER_JOB" \
  --project="$PROJECT_ID" --region="$REGION" \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/run.invoker" --quiet >/dev/null

# The deployer can already act as WORKER_SA because every worker job uses it.
# Reusing that identity avoids broadening IAM solely for the finalizer clock.
FINALIZER_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${PRICE_FINALIZER_JOB}:run"
FINALIZER_SCHEDULER_ARGS=(
  --project="$PROJECT_ID"
  --location="$REGION"
  --schedule="*/5 * * * *"
  --time-zone="Etc/UTC"
  --uri="$FINALIZER_URI"
  --http-method=POST
  --oauth-service-account-email="$WORKER_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --headers="Content-Type=application/json"
  --message-body='{}'
  --attempt-deadline=900s
  --max-retry-attempts=3
  --min-backoff=10s
  --max-backoff=60s
  --max-doublings=3
)
if gcloud scheduler jobs describe "$PRICE_FINALIZER_SCHEDULER_JOB" \
  --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$PRICE_FINALIZER_SCHEDULER_JOB" \
    "${FINALIZER_SCHEDULER_ARGS[@]}" --quiet
else
  gcloud scheduler jobs create http "$PRICE_FINALIZER_SCHEDULER_JOB" \
    "${FINALIZER_SCHEDULER_ARGS[@]}" --quiet
fi
gcloud scheduler jobs resume "$PRICE_FINALIZER_SCHEDULER_JOB" \
  --project="$PROJECT_ID" --location="$REGION" --quiet >/dev/null 2>&1 || true

PHASE="price-failed-retry-job"
gcloud run jobs deploy "$PRICE_RETRY_JOB" \
  --project="$PROJECT_ID" --region="$REGION" \
  --image="$WORKER_IMAGE" \
  --service-account="$WORKER_SA" \
  --set-cloudsql-instances="$INSTANCE_CONNECTION" \
  --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
  --set-env-vars="^|^SERVICE_MODE=worker|PERSISTENCE_BACKEND=postgres|TASKS_MODE=cloud|GCP_PROJECT_ID=${PROJECT_ID}|GCP_REGION=${REGION}|CLOUD_TASKS_LOCATION=${TASKS_LOCATION}|CLOUD_TASKS_QUEUE=${PRICE_QUEUE}|CATALOG_TASKS_QUEUE=${CATALOG_QUEUE}|TASKS_SERVICE_ACCOUNT_EMAIL=${TASKS_SA}|WORKER_URL=${WORKER_URL}|SCHEDULER_TIMEZONE=Africa/Cairo|REFRESH_INTERVAL_MINUTES=720|PRICE_RUN_FINALIZER_CADENCE_MINUTES=5|MAX_URL_GROUPS_PER_RUN=50000|DB_POOL_MIN=1|DB_POOL_MAX=4|INTERNAL_TOKEN=" \
  --command=python --args=-m,scripts.retry_failed_price_tasks_job \
  --cpu=1 --memory=2Gi --max-retries=0 --task-timeout=3600s --quiet

restore_runtime_state
PHASE="complete"
trap - ERR

echo "PRODUCT_CENTRIC_DEPLOYMENT=SUCCESS"
echo "RELEASE_ID=${RELEASE_ID}"
echo "API_IMAGE=${API_IMAGE}"
echo "WORKER_IMAGE=${WORKER_IMAGE}"
echo "PRICE_START_JOB=${PRICE_START_JOB}"
echo "PRICE_FORCE_JOB=${PRICE_FORCE_JOB}"
echo "PRICE_FINALIZER_JOB=${PRICE_FINALIZER_JOB}"
echo "PRICE_FINALIZER_SCHEDULER_JOB=${PRICE_FINALIZER_SCHEDULER_JOB}"
echo "PRICE_RETRY_JOB=${PRICE_RETRY_JOB}"
echo "CORE_V2_COPY_JOB=${CORE_V2_COPY_JOB}"
echo "BACKEND_READY_FOR_WEB=YES"
