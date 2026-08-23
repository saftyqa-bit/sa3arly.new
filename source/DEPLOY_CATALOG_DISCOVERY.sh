#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
API_SERVICE="${API_SERVICE:-sa3arly-api}"
WORKER_SERVICE="${WORKER_SERVICE:-sa3arly-worker}"
PRICE_SCHEDULER_JOB="${PRICE_SCHEDULER_JOB:-sa3arly-hourly-refresh}"
CATALOG_SCHEDULER_JOB="${CATALOG_SCHEDULER_JOB:-sa3arly-catalog-discovery}"
CATALOG_QUEUE="${CATALOG_QUEUE:-sa3arly-catalog-discovery}"
REPOSITORY="${REPOSITORY:-sa3arly}"
DB_INSTANCE="${DB_INSTANCE:-sa3arly-postgres}"
DB_SECRET="${DB_SECRET:-sa3arly-database-url}"
MIGRATION_JOB="${MIGRATION_JOB:-sa3arly-catalog-migrations}"
BOOTSTRAP_SA="${BOOTSTRAP_SA:-sa3arly-bootstrap@${PROJECT_ID}.iam.gserviceaccount.com}"
RELEASE_ID="${RELEASE_ID:-catalog-v0-5-1-$(date -u +%Y%m%d-%H%M%S)}"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"
API_IMAGE="${IMAGE_BASE}/api:${RELEASE_ID}"
WORKER_IMAGE="${IMAGE_BASE}/worker:${RELEASE_ID}"

gcloud config set project "$PROJECT_ID" >/dev/null

PRICE_SCHEDULE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --format='value(schedule)')"
PRICE_TIMEZONE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --format='value(timeZone)')"
PRICE_STATE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --format='value(state)')"
PRICE_URI="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --format='value(httpTarget.uri)')"
if [[ "$PRICE_SCHEDULE" != "0 10,20 * * *" \
  || "$PRICE_TIMEZONE" != "Africa/Cairo" \
  || "$PRICE_STATE" != "ENABLED" ]]; then
  echo "PRICE_SCHEDULER_PREFLIGHT=FAIL" >&2
  echo "Expected ENABLED / 0 10,20 * * * / Africa/Cairo; no changes were made." >&2
  exit 2
fi
WORKER_URL="${PRICE_URI%/internal/scheduler/refresh}"

BACKEND="$(gcloud run services describe "$WORKER_SERVICE" \
  --region="$REGION" --format=json | python3 -c 'import json,sys
data=json.load(sys.stdin)
env=data["spec"]["template"]["spec"]["containers"][0].get("env",[])
print(next((x.get("value","") for x in env if x.get("name")=="PERSISTENCE_BACKEND"),""))')"
if [[ "$BACKEND" != "postgres" ]]; then
  echo "PERSISTENCE_BACKEND=${BACKEND:-unset}; catalog auto-promotion requires postgres." >&2
  exit 2
fi

echo "PRICE_SCHEDULER_PREFLIGHT=PASS"
echo "Building catalog discovery v0.5.1 images..."
gcloud builds submit --project="$PROJECT_ID" \
  --config=infra/gcp/cloudbuild-api.yaml \
  --substitutions="_IMAGE=${API_IMAGE}" .
gcloud builds submit --project="$PROJECT_ID" \
  --config=infra/gcp/cloudbuild-worker.yaml \
  --substitutions="_IMAGE=${WORKER_IMAGE}" .

INSTANCE_CONNECTION="${PROJECT_ID}:${REGION}:${DB_INSTANCE}"
gcloud run jobs deploy "$MIGRATION_JOB" \
  --project="$PROJECT_ID" \
  --image="$API_IMAGE" \
  --region="$REGION" \
  --service-account="$BOOTSTRAP_SA" \
  --set-cloudsql-instances="$INSTANCE_CONNECTION" \
  --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
  --command=python \
  --args=scripts/apply_migrations.py \
  --cpu=1 \
  --memory=1Gi \
  --max-retries=0 \
  --task-timeout=900s
gcloud run jobs execute "$MIGRATION_JOB" \
  --project="$PROJECT_ID" --region="$REGION" --wait

DISCOVERY_ENV="CATALOG_DISCOVERY_CRON=30 2 * * *|CATALOG_DISCOVERY_STORES_PER_RUN=35|CATALOG_DISCOVERY_RESCAN_HOURS=168|CATALOG_DISCOVERY_MAX_CANDIDATES_PER_STORE=2000|CATALOG_DISCOVERY_MAX_SITEMAPS_PER_STORE=8|CATALOG_DISCOVERY_MAX_SOURCE_FETCHES=12|CATALOG_DISCOVERY_AUTO_MATCH_SCORE=95|CATALOG_DISCOVERY_REVIEW_SCORE=70|CATALOG_DISCOVERY_NEW_PRODUCT_MIN_STORES=2|CATALOG_TASKS_QUEUE=${CATALOG_QUEUE}"
PRICE_ENV="REFRESH_CRON=0 10,20 * * *|REFRESH_INTERVAL_MINUTES=720|FRESHNESS_MINUTES=855|STALE_AFTER_MINUTES=1710"

gcloud run services update "$WORKER_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --image="$WORKER_IMAGE" \
  --update-env-vars="^|^${PRICE_ENV}|${DISCOVERY_ENV}|WORKER_URL=${WORKER_URL}"
gcloud run services update-traffic "$WORKER_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" --to-latest

gcloud run services update "$API_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --image="$API_IMAGE" \
  --update-env-vars="^|^${PRICE_ENV}|${DISCOVERY_ENV}"
gcloud run services update-traffic "$API_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" --to-latest

if ! gcloud tasks queues describe "$CATALOG_QUEUE" \
  --location="$TASKS_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud tasks queues create "$CATALOG_QUEUE" \
    --location="$TASKS_LOCATION" --project="$PROJECT_ID" \
    --max-dispatches-per-second=0.2 \
    --max-concurrent-dispatches=1
fi
gcloud tasks queues update "$CATALOG_QUEUE" \
  --location="$TASKS_LOCATION" --project="$PROJECT_ID" \
  --max-dispatches-per-second=0.2 \
  --max-concurrent-dispatches=1 \
  --max-attempts=3 \
  --min-backoff=30s \
  --max-backoff=3600s \
  --max-doublings=5
gcloud tasks queues resume "$CATALOG_QUEUE" \
  --location="$TASKS_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1 || true

SCHEDULER_SA="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --format='value(httpTarget.oidcToken.serviceAccountEmail)')"
SCHEDULER_AUDIENCE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --format='value(httpTarget.oidcToken.audience)')"
CATALOG_SCHEDULER_ARGS=(
  --location="$REGION"
  --project="$PROJECT_ID"
  --schedule="30 2 * * *"
  --time-zone="Africa/Cairo"
  --uri="${WORKER_URL}/internal/scheduler/catalog-discovery"
  --http-method=POST
  --attempt-deadline=900s
  --max-retry-attempts=1
  --max-retry-duration=1800s
  --min-backoff=30s
  --max-backoff=300s
  --max-doublings=4
  --oidc-service-account-email="$SCHEDULER_SA"
  --oidc-token-audience="${SCHEDULER_AUDIENCE:-$WORKER_URL}"
  --message-body='{"trigger":"catalog-discovery-nightly"}'
)
if gcloud scheduler jobs describe "$CATALOG_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$CATALOG_SCHEDULER_JOB" \
    "${CATALOG_SCHEDULER_ARGS[@]}" \
    --update-headers="Content-Type=application/json"
else
  gcloud scheduler jobs create http "$CATALOG_SCHEDULER_JOB" \
    "${CATALOG_SCHEDULER_ARGS[@]}" \
    --headers="Content-Type=application/json"
fi
gcloud scheduler jobs pause "$CATALOG_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" >/dev/null

IDENTITY_TOKEN="$(gcloud auth print-identity-token --audiences="$WORKER_URL")"
curl --fail-with-body --silent --show-error --max-time 900 \
  -H "Authorization: Bearer ${IDENTITY_TOKEN}" \
  -H "Content-Type: application/json" \
  -X POST \
  -d '{"trigger":"catalog-canary-v0.5.1","store_limit":5}' \
  "${WORKER_URL}/internal/scheduler/catalog-discovery" \
  > catalog-canary-run.json

python3 - <<'PY'
import json
data=json.load(open("catalog-canary-run.json", encoding="utf-8"))
if data.get("duplicate") or int(data.get("task_count") or 0) < 1:
    raise SystemExit(f"Catalog canary did not queue new work: {data}")
print("CATALOG_CANARY_RUN_ID=" + str(data["run_id"]))
print("CATALOG_CANARY_TASKS=" + str(data["task_count"]))
PY

FINAL_PRICE_SCHEDULE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --format='value(schedule)')"
if [[ "$FINAL_PRICE_SCHEDULE" != "0 10,20 * * *" ]]; then
  echo "Price scheduler changed unexpectedly; catalog scheduler remains paused." >&2
  exit 2
fi

echo "CATALOG_DEPLOYMENT=CANARY_RUNNING"
echo "CATALOG_SCHEDULER=PAUSED"
echo "PRICE_SCHEDULER=ENABLED_10AM_8PM"
echo "NEXT_STEP=Run ./VERIFY_CATALOG_DISCOVERY.sh after the five canary tasks drain."
