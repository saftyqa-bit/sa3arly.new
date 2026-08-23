#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
WORKER_SERVICE="${WORKER_SERVICE:-sa3arly-worker}"
API_SERVICE="${API_SERVICE:-sa3arly-api}"
PRICE_QUEUE="${PRICE_QUEUE:-sa3arly-scrape}"
CATALOG_QUEUE="${CATALOG_QUEUE:-sa3arly-catalog-discovery}"
PRICE_SCHEDULER_JOB="${PRICE_SCHEDULER_JOB:-sa3arly-hourly-refresh}"
CATALOG_SCHEDULER_JOB="${CATALOG_SCHEDULER_JOB:-sa3arly-catalog-discovery}"
PRICE_FORCE_JOB="${PRICE_FORCE_JOB:-sa3arly-price-refresh-force-start}"
PRICE_FINALIZER_JOB="${PRICE_FINALIZER_JOB:-sa3arly-price-run-finalizer}"
PRICE_FINALIZER_SCHEDULER_JOB="${PRICE_FINALIZER_SCHEDULER_JOB:-sa3arly-price-run-finalizer}"
TASKS_SA="${TASKS_SA:-sa3arly-tasks@${PROJECT_ID}.iam.gserviceaccount.com}"
SCHEDULER_SA="${SCHEDULER_SA:-sa3arly-scheduler@${PROJECT_ID}.iam.gserviceaccount.com}"
WORKER_MAX_INSTANCES="${WORKER_MAX_INSTANCES:-48}"
WORKER_CONTAINER_CONCURRENCY="${WORKER_CONTAINER_CONCURRENCY:-2}"
START_PRICE_REFRESH="${START_PRICE_REFRESH:-0}"

gcloud config set project "$PROJECT_ID" >/dev/null
WORKER_URL="$(
  gcloud run services describe "$WORKER_SERVICE" \
    --project="$PROJECT_ID" --region="$REGION" \
    --format='value(status.url)'
)"
[[ "$WORKER_URL" == https://* ]] || {
  echo "Could not resolve the production worker URL." >&2
  exit 2
}

gcloud run services add-iam-policy-binding "$WORKER_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --member="serviceAccount:${TASKS_SA}" \
  --role="roles/run.invoker" >/dev/null
gcloud run services add-iam-policy-binding "$WORKER_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --member="serviceAccount:${SCHEDULER_SA}" \
  --role="roles/run.invoker" >/dev/null
gcloud run services update "$WORKER_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --max="$WORKER_MAX_INSTANCES" \
  --concurrency="$WORKER_CONTAINER_CONCURRENCY" \
  --update-env-vars="DB_POOL_MIN=1,DB_POOL_MAX=2" >/dev/null

ensure_queue() {
  local queue="$1"
  local rate="$2"
  local concurrency="$3"
  if ! gcloud tasks queues describe "$queue" \
    --project="$PROJECT_ID" --location="$TASKS_LOCATION" >/dev/null 2>&1; then
    gcloud tasks queues create "$queue" \
      --project="$PROJECT_ID" --location="$TASKS_LOCATION" \
      --max-dispatches-per-second="$rate" \
      --max-concurrent-dispatches="$concurrency"
  fi
  gcloud tasks queues update "$queue" \
    --project="$PROJECT_ID" --location="$TASKS_LOCATION" \
    --max-dispatches-per-second="$rate" \
    --max-concurrent-dispatches="$concurrency" \
    --max-attempts=3 \
    --min-backoff=10s \
    --max-backoff=3600s \
    --max-doublings=5 >/dev/null
  local state
  state="$(
    gcloud tasks queues describe "$queue" \
      --project="$PROJECT_ID" --location="$TASKS_LOCATION" \
      --format='value(state)'
  )"
  if [[ "$state" == "PAUSED" ]]; then
    gcloud tasks queues resume "$queue" \
      --project="$PROJECT_ID" --location="$TASKS_LOCATION" >/dev/null
  fi
}

ensure_scheduler() {
  local job="$1"
  local schedule="$2"
  local uri="$3"
  local body="$4"
  local common_args=(
    --project="$PROJECT_ID"
    --location="$REGION"
    --schedule="$schedule"
    --time-zone="Africa/Cairo"
    --uri="$uri"
    --http-method=POST
    --attempt-deadline=900s
    --max-retry-attempts=1
    --max-retry-duration=1800s
    --min-backoff=10s
    --max-backoff=300s
    --max-doublings=4
    --message-body="$body"
  )
  if gcloud scheduler jobs describe "$job" \
    --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
    # Existing authenticated jobs are deliberately left unchanged. Google
    # requires iam.serviceAccounts.actAs even when an unrelated field on an
    # OIDC-backed job is patched, while running/resuming the job does not.
    echo "Preserving existing authenticated scheduler job: ${job}"
  else
    gcloud scheduler jobs create http "$job" "${common_args[@]}" \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$WORKER_URL" \
      --headers="Content-Type=application/json" >/dev/null
  fi
  local state
  state="$(
    gcloud scheduler jobs describe "$job" \
      --project="$PROJECT_ID" --location="$REGION" \
      --format='value(state)'
  )"
  if [[ "$state" == "PAUSED" ]]; then
    gcloud scheduler jobs resume "$job" \
      --project="$PROJECT_ID" --location="$REGION" >/dev/null
  fi
}

ensure_queue "$PRICE_QUEUE" 12 80
ensure_queue "$CATALOG_QUEUE" 10 10
ensure_scheduler \
  "$CATALOG_SCHEDULER_JOB" \
  "30 2 * * *" \
  "${WORKER_URL}/internal/scheduler/catalog-discovery" \
  '{"trigger":"catalog-full-production","store_limit":500}'
if gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs pause "$PRICE_SCHEDULER_JOB" \
    --project="$PROJECT_ID" --location="$REGION" >/dev/null
fi
if ! gcloud run jobs describe "$PRICE_FINALIZER_JOB" \
  --project="$PROJECT_ID" --region="$REGION" >/dev/null 2>&1; then
  echo "Independent price finalizer job is missing." >&2
  exit 2
fi
if ! gcloud scheduler jobs describe "$PRICE_FINALIZER_SCHEDULER_JOB" \
  --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
  echo "Independent price finalizer schedule is missing." >&2
  exit 2
fi
if [[ "$(gcloud scheduler jobs describe "$PRICE_FINALIZER_SCHEDULER_JOB" \
  --project="$PROJECT_ID" --location="$REGION" --format='value(state)')" == "PAUSED" ]]; then
  gcloud scheduler jobs resume "$PRICE_FINALIZER_SCHEDULER_JOB" \
    --project="$PROJECT_ID" --location="$REGION" >/dev/null
fi
echo "PRICE_REFRESH_SCHEDULE=MANAGED_BY_INDEPENDENT_CLOUD_RUN_JOBS"
echo "PRICE_RUN_FINALIZATION=MANAGED_BY_CLOUD_SCHEDULER_JOB"
echo "CATALOG_RECOVERY=MANAGED_BY_GITHUB_OIDC_SCHEDULE"

# Do not place a second full refresh behind one that is already active.
# Read the status once so price and catalog decisions use the same snapshot.
API_URL="$(
  gcloud run services describe "$API_SERVICE" \
    --project="$PROJECT_ID" --region="$REGION" \
    --format='value(status.url)'
)"
STATUS_JSON=""
PRICE_RUN_STATE=""
CATALOG_RUN_STATE=""
CATALOG_STALE_REPAIR_CREATED_AT=""
if [[ "$API_URL" == https://* ]] && command -v jq >/dev/null 2>&1; then
  STATUS_JSON="$(
    curl --fail --silent --show-error --retry 5 --retry-delay 2 \
      --max-time 30 "${API_URL}/api/v1/status" || true
  )"
  if jq -e 'type == "object"' <<<"$STATUS_JSON" >/dev/null 2>&1; then
    PRICE_RUN_STATE="$(jq -r '.latest_price_run_status // empty' <<<"$STATUS_JSON")"
    CATALOG_RUN_STATE="$(
      jq -r '
        [
          .recent_catalog_runs[]?
          | select(.state == "created"
              or .state == "enqueuing"
              or .state == "queued"
              or .state == "running")
          | .state
        ][0] // empty
      ' <<<"$STATUS_JSON"
    )"
    CATALOG_STALE_REPAIR_CREATED_AT="$(
      jq -r '
        [
          .recent_catalog_runs[]?
          | select(.state == "created"
              or .state == "enqueuing"
              or .state == "queued"
              or .state == "running")
          | select((.task_states.queued // 0) > 0)
          | select((.task_states.running // 0) == 0)
          | select((.error_codes.superseded_duplicate_run // 0) > 0)
          | .created_at
        ][0] // empty
      ' <<<"$STATUS_JSON"
    )"
  fi
fi
if [[ -n "$CATALOG_STALE_REPAIR_CREATED_AT" ]]; then
  stale_repair_epoch="$(
    date -d "$CATALOG_STALE_REPAIR_CREATED_AT" +%s 2>/dev/null || true
  )"
  if [[ -n "$stale_repair_epoch" ]] \
    && (( $(date +%s) - stale_repair_epoch >= 3600 )); then
    echo "CATALOG_REFRESH=RECOVERING_STALE_COMPLEMENTARY_RUN"
    CATALOG_RUN_STATE=""
  fi
fi
if [[ "${FORCE_PRICE_REFRESH:-0}" == "1" ]]; then
  gcloud run jobs execute "$PRICE_FORCE_JOB" \
    --project="$PROJECT_ID" --region="$REGION" --async >/dev/null
  echo "PRICE_REFRESH=FORCE_REQUESTED:${PRICE_FORCE_JOB}"
elif [[ "$START_PRICE_REFRESH" == "1" ]]; then
  case "$PRICE_RUN_STATE" in
    created|enqueuing|queued|running)
      echo "PRICE_REFRESH=ALREADY_ACTIVE:${PRICE_RUN_STATE}"
      ;;
    *)
      gcloud run jobs execute "$PRICE_FORCE_JOB" \
        --project="$PROJECT_ID" --region="$REGION" --async >/dev/null
      echo "PRICE_REFRESH=REQUESTED:${PRICE_FORCE_JOB}"
      ;;
  esac
else
  echo "PRICE_REFRESH=NOT_REQUESTED"
fi
case "$CATALOG_RUN_STATE" in
  created|enqueuing|queued|running)
    echo "CATALOG_REFRESH=ALREADY_ACTIVE:${CATALOG_RUN_STATE}"
    ;;
  *)
    gcloud scheduler jobs run "$CATALOG_SCHEDULER_JOB" \
      --project="$PROJECT_ID" --location="$REGION"
    echo "CATALOG_REFRESH=REQUESTED"
    ;;
esac

echo "PRICE_COLLECTION_RUNTIME=READY"
echo "WORKER_URL=${WORKER_URL}"
