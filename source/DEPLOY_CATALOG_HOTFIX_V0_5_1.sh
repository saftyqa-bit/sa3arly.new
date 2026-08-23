#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
WORKER_SERVICE="${WORKER_SERVICE:-sa3arly-worker}"
PRICE_SCHEDULER_JOB="${PRICE_SCHEDULER_JOB:-sa3arly-hourly-refresh}"
CATALOG_SCHEDULER_JOB="${CATALOG_SCHEDULER_JOB:-sa3arly-catalog-discovery}"
CATALOG_QUEUE="${CATALOG_QUEUE:-sa3arly-catalog-discovery}"
REPOSITORY="${REPOSITORY:-sa3arly}"
DB_INSTANCE="${DB_INSTANCE:-sa3arly-postgres}"
DB_SECRET="${DB_SECRET:-sa3arly-database-url}"
RESET_JOB="${RESET_JOB:-sa3arly-catalog-v0-5-1-reset}"
BOOTSTRAP_SA="${BOOTSTRAP_SA:-sa3arly-bootstrap@${PROJECT_ID}.iam.gserviceaccount.com}"
FAILED_CATALOG_RUN_ID="${FAILED_CATALOG_RUN_ID:-0bcb9db6-0df5-41ec-9cdf-1e24bdf237ac}"
RELEASE_ID="${RELEASE_ID:-catalog-v0-5-1-$(date -u +%Y%m%d-%H%M%S)}"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"
WORKER_IMAGE="${IMAGE_BASE}/worker:${RELEASE_ID}"

gcloud config set project "$PROJECT_ID" >/dev/null

PRICE_SCHEDULE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(schedule)')"
PRICE_TIMEZONE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(timeZone)')"
PRICE_STATE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(state)')"
CATALOG_STATE="$(gcloud scheduler jobs describe "$CATALOG_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(state)')"
if [[ "$PRICE_SCHEDULE" != "0 10,20 * * *" \
  || "$PRICE_TIMEZONE" != "Africa/Cairo" \
  || "$PRICE_STATE" != "ENABLED" ]]; then
  echo "PRICE_SCHEDULER_PREFLIGHT=FAIL" >&2
  echo "Expected ENABLED / 0 10,20 * * * / Africa/Cairo; no changes were made." >&2
  exit 2
fi
if [[ "$CATALOG_STATE" != "PAUSED" ]]; then
  echo "Catalog scheduler must remain paused during the v0.5.1 repair." >&2
  exit 2
fi

BACKEND="$(gcloud run services describe "$WORKER_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" --format=json | python3 -c 'import json,sys
data=json.load(sys.stdin)
env=data["spec"]["template"]["spec"]["containers"][0].get("env",[])
print(next((x.get("value","") for x in env if x.get("name")=="PERSISTENCE_BACKEND"),""))')"
if [[ "$BACKEND" != "postgres" ]]; then
  echo "PERSISTENCE_BACKEND=${BACKEND:-unset}; refusing catalog hotfix." >&2
  exit 2
fi

echo "CATALOG_HOTFIX_PREFLIGHT=PASS"
echo "Building catalog discovery v0.5.1 worker image..."
gcloud builds submit --project="$PROJECT_ID" \
  --config=infra/gcp/cloudbuild-worker.yaml \
  --substitutions="_IMAGE=${WORKER_IMAGE}" .

gcloud run services update "$WORKER_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --image="$WORKER_IMAGE"
gcloud run services update-traffic "$WORKER_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" --to-latest

INSTANCE_CONNECTION="${PROJECT_ID}:${REGION}:${DB_INSTANCE}"
gcloud run jobs deploy "$RESET_JOB" \
  --project="$PROJECT_ID" \
  --image="$WORKER_IMAGE" \
  --region="$REGION" \
  --service-account="$BOOTSTRAP_SA" \
  --set-cloudsql-instances="$INSTANCE_CONNECTION" \
  --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
  --set-env-vars="FAILED_CATALOG_RUN_ID=${FAILED_CATALOG_RUN_ID}" \
  --command=python \
  --args=scripts/reset_failed_catalog_canary_sources.py \
  --cpu=1 \
  --memory=512Mi \
  --max-retries=0 \
  --task-timeout=300s
gcloud run jobs execute "$RESET_JOB" \
  --project="$PROJECT_ID" --region="$REGION" --wait

gcloud tasks queues resume "$CATALOG_QUEUE" \
  --location="$TASKS_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1 || true

PRICE_URI="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(httpTarget.uri)')"
WORKER_URL="${PRICE_URI%/internal/scheduler/refresh}"
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

with open("catalog-canary-run.json", encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("duplicate") or int(data.get("task_count") or 0) != 5:
    raise SystemExit(f"v0.5.1 canary did not queue exactly five new tasks: {data}")
print("CATALOG_CANARY_RUN_ID=" + str(data["run_id"]))
print("CATALOG_CANARY_TASKS=" + str(data["task_count"]))
PY

FINAL_PRICE_SCHEDULE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(schedule)')"
FINAL_PRICE_STATE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(state)')"
FINAL_CATALOG_STATE="$(gcloud scheduler jobs describe "$CATALOG_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(state)')"
if [[ "$FINAL_PRICE_SCHEDULE" != "0 10,20 * * *" \
  || "$FINAL_PRICE_STATE" != "ENABLED" \
  || "$FINAL_CATALOG_STATE" != "PAUSED" ]]; then
  echo "Scheduler state changed unexpectedly; catalog activation remains blocked." >&2
  exit 2
fi

echo "CATALOG_HOTFIX_V0_5_1=CANARY_RUNNING"
echo "CATALOG_SCHEDULER=PAUSED"
echo "PRICE_SCHEDULER=ENABLED_10AM_8PM"
echo "NEXT_STEP=Run ./VERIFY_CATALOG_DISCOVERY.sh after the five tasks drain."
