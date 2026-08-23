#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${ALLOW_LEGACY_FIRESTORE_DEPLOY:-0}" != "1" ]]; then
  echo "Legacy Firestore deployment is disabled. Use infra/gcp/deploy.sh for PostgreSQL." >&2
  echo "Set ALLOW_LEGACY_FIRESTORE_DEPLOY=1 only for an intentional rollback." >&2
  exit 2
fi

: "${PROJECT_ID:?Set PROJECT_ID to the dedicated Google Cloud project ID}"
: "${BOT_CONTACT_URL:?Set BOT_CONTACT_URL to a public bot-policy/contact URL}"

REGION="${REGION:-europe-west1}"
FIRESTORE_LOCATION="${FIRESTORE_LOCATION:-europe-west1}"
FIRESTORE_DATABASE="${FIRESTORE_DATABASE:-(default)}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
REPOSITORY="${REPOSITORY:-sa3arly}"
API_SERVICE="${API_SERVICE:-sa3arly-api}"
WORKER_SERVICE="${WORKER_SERVICE:-sa3arly-worker}"
BOOTSTRAP_JOB="${BOOTSTRAP_JOB:-sa3arly-firestore-bootstrap}"
QUEUE="${QUEUE:-sa3arly-scrape}"
SCHEDULER_JOB="${SCHEDULER_JOB:-sa3arly-hourly-refresh}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-*}"
COST_CONTROLS_CONFIRMED="${COST_CONTROLS_CONFIRMED:-0}"
if [[ ! "$BOT_CONTACT_URL" =~ ^https?:// ]]; then
  echo "BOT_CONTACT_URL must start with http:// or https://" >&2
  exit 2
fi
if [[ "$FIRESTORE_DATABASE" != "(default)" ]]; then
  echo "This release is validated only for the (default) Firestore database." >&2
  exit 2
fi
BOT_USER_AGENT="${BOT_USER_AGENT:-Sa3arlyPriceBot/1.0 (+${BOT_CONTACT_URL})}"

API_SA="sa3arly-api@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="sa3arly-worker@${PROJECT_ID}.iam.gserviceaccount.com"
BOOTSTRAP_SA="sa3arly-bootstrap@${PROJECT_ID}.iam.gserviceaccount.com"
TASKS_SA="sa3arly-tasks@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="sa3arly-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"
API_IMAGE="${IMAGE_BASE}/api:firestore"
WORKER_IMAGE="${IMAGE_BASE}/worker:firestore"

pause_scheduler_if_present() {
  if ! gcloud scheduler jobs describe "$SCHEDULER_JOB" \
    --location="$REGION" >/dev/null 2>&1; then
    return 1
  fi
  local state
  state="$(gcloud scheduler jobs describe "$SCHEDULER_JOB" \
    --location="$REGION" --format='value(state)')"
  if [[ "$state" != "PAUSED" ]]; then
    gcloud scheduler jobs pause "$SCHEDULER_JOB" \
      --location="$REGION" >/dev/null
  fi
}

pause_queue_if_present() {
  if ! gcloud tasks queues describe "$QUEUE" \
    --location="$TASKS_LOCATION" >/dev/null 2>&1; then
    return 1
  fi
  local state
  state="$(gcloud tasks queues describe "$QUEUE" \
    --location="$TASKS_LOCATION" --format='value(state)')"
  if [[ "$state" != "PAUSED" ]]; then
    gcloud tasks queues pause "$QUEUE" \
      --location="$TASKS_LOCATION" >/dev/null
  fi
}

gcloud config set project "$PROJECT_ID" >/dev/null

if [[ "$(gcloud billing projects describe "$PROJECT_ID" \
  --format='value(billingEnabled)' 2>/dev/null || true)" != "True" ]]; then
  echo "Billing is not linked to ${PROJECT_ID}; link a billing account before deployment." >&2
  exit 2
fi

echo "Enabling Firestore and serverless APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  firebaserules.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudtasks.googleapis.com \
  iamcredentials.googleapis.com

if pause_scheduler_if_present; then
  echo "Pausing the existing collection schedule during deployment..."
fi
if pause_queue_if_present; then
  echo "Pausing the existing task queue during deployment..."
fi

if gcloud firestore databases describe \
  --database="$FIRESTORE_DATABASE" >/dev/null 2>&1; then
  EXISTING_FIRESTORE_LOCATION="$(gcloud firestore databases describe \
    --database="$FIRESTORE_DATABASE" --format='value(locationId)')"
  EXISTING_FIRESTORE_TYPE="$(gcloud firestore databases describe \
    --database="$FIRESTORE_DATABASE" --format='value(type)')"
  EXISTING_FIRESTORE_EDITION="$(gcloud firestore databases describe \
    --database="$FIRESTORE_DATABASE" --format='value(databaseEdition)')"
  if [[ "$EXISTING_FIRESTORE_LOCATION" != "$FIRESTORE_LOCATION" ]] \
    || [[ "$EXISTING_FIRESTORE_TYPE" != "FIRESTORE_NATIVE" ]] \
    || [[ "$EXISTING_FIRESTORE_EDITION" != "STANDARD" ]]; then
    echo "Existing Firestore database is not the approved regional Standard database." >&2
    echo "Expected: ${FIRESTORE_LOCATION} / FIRESTORE_NATIVE / STANDARD" >&2
    echo "Found: ${EXISTING_FIRESTORE_LOCATION} / ${EXISTING_FIRESTORE_TYPE} / ${EXISTING_FIRESTORE_EDITION}" >&2
    exit 2
  fi
else
  gcloud firestore databases create \
    --database="$FIRESTORE_DATABASE" \
    --location="$FIRESTORE_LOCATION" \
    --edition=standard \
    --type=firestore-native
fi

echo "Deploying deny-all Firestore client security rules..."
ACCESS_TOKEN="$(gcloud auth print-access-token)"
RULESET_PAYLOAD="$(python -c 'import json, pathlib; print(json.dumps({"source": {"files": [{"name": "firestore.rules", "content": pathlib.Path("firestore.rules").read_text(encoding="utf-8")}]}}))')"
RULESET_RESPONSE="$(curl --fail --silent --show-error \
  -X POST \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  --data-binary "$RULESET_PAYLOAD" \
  "https://firebaserules.googleapis.com/v1/projects/${PROJECT_ID}/rulesets")"
RULESET_NAME="$(printf '%s' "$RULESET_RESPONSE" | python -c 'import json, sys; print(json.load(sys.stdin)["name"])')"
RELEASE_NAME="projects/${PROJECT_ID}/releases/cloud.firestore"
if curl --fail --silent \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://firebaserules.googleapis.com/v1/${RELEASE_NAME}" >/dev/null 2>&1; then
  RELEASE_PAYLOAD="$(python -c 'import json, sys; print(json.dumps({"release": {"name": sys.argv[1], "rulesetName": sys.argv[2]}, "updateMask": "rulesetName"}))' "$RELEASE_NAME" "$RULESET_NAME")"
  curl --fail --silent --show-error \
    -X PATCH \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    --data-binary "$RELEASE_PAYLOAD" \
    "https://firebaserules.googleapis.com/v1/${RELEASE_NAME}" >/dev/null
else
  RELEASE_PAYLOAD="$(python -c 'import json, sys; print(json.dumps({"name": sys.argv[1], "rulesetName": sys.argv[2]}))' "$RELEASE_NAME" "$RULESET_NAME")"
  curl --fail --silent --show-error \
    -X POST \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    --data-binary "$RELEASE_PAYLOAD" \
    "https://firebaserules.googleapis.com/v1/projects/${PROJECT_ID}/releases" >/dev/null
fi
unset ACCESS_TOKEN RULESET_PAYLOAD RULESET_RESPONSE RELEASE_PAYLOAD

disable_firestore_index() {
  local collection_group="$1"
  local field_path="$2"
  gcloud firestore indexes fields update "$field_path" \
    --database="$FIRESTORE_DATABASE" \
    --collection-group="$collection_group" \
    --disable-indexes \
    --quiet
}

echo "Applying Firestore index exemptions for large evidence and comparison fields..."
disable_firestore_index page_cache parsed_payload
disable_firestore_index cash_offers raw_payload
disable_firestore_index installment_plans raw_payload
disable_firestore_index cash_offer_history snapshot
disable_firestore_index installment_plan_history snapshot
disable_firestore_index mappings connector_config
disable_firestore_index comparison_docs cash_offers
disable_firestore_index comparison_docs installment_plans
disable_firestore_index comparison_summaries cash_offers
disable_firestore_index comparison_summaries installment_plans
disable_firestore_index page_cache expires_at
disable_firestore_index scrape_runs expires_at
disable_firestore_index scrape_task_runs expires_at
disable_firestore_index store_locks expires_at

enable_firestore_ttl() {
  local collection_group="$1"
  gcloud firestore fields ttls update expires_at \
    --database="$FIRESTORE_DATABASE" \
    --collection-group="$collection_group" \
    --enable-ttl \
    --async \
    --quiet >/dev/null
}

echo "Enabling Firestore TTL cleanup policies..."
enable_firestore_ttl page_cache
enable_firestore_ttl scrape_runs
enable_firestore_ttl scrape_task_runs
enable_firestore_ttl store_locks

if ! gcloud artifacts repositories describe "$REPOSITORY" \
  --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPOSITORY" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Sa3arly Firestore application images"
fi

create_sa() {
  local short_name="$1"
  local display_name="$2"
  if ! gcloud iam service-accounts describe \
    "${short_name}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$short_name" \
      --display-name="$display_name"
  fi
}

create_sa sa3arly-api "Sa3arly API"
create_sa sa3arly-worker "Sa3arly scheduled worker"
create_sa sa3arly-bootstrap "Sa3arly Firestore bootstrap"
create_sa sa3arly-tasks "Sa3arly Cloud Tasks caller"
create_sa sa3arly-scheduler "Sa3arly Cloud Scheduler caller"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" \
  --format='value(projectNumber)')"
TASKS_AGENT="service-${PROJECT_NUMBER}@gcp-sa-cloudtasks.iam.gserviceaccount.com"
SCHEDULER_AGENT="service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"

gcloud beta services identity create \
  --service=cloudtasks.googleapis.com \
  --project="$PROJECT_ID" >/dev/null
gcloud beta services identity create \
  --service=cloudscheduler.googleapis.com \
  --project="$PROJECT_ID" >/dev/null

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${TASKS_AGENT}" \
  --role="roles/cloudtasks.serviceAgent" \
  --condition=None >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SCHEDULER_AGENT}" \
  --role="roles/cloudscheduler.serviceAgent" \
  --condition=None >/dev/null

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${API_SA}" \
  --role="roles/datastore.viewer" \
  --condition=None >/dev/null

for service_account in "$WORKER_SA" "$BOOTSTRAP_SA"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${service_account}" \
    --role="roles/datastore.user" \
    --condition=None >/dev/null
done

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/cloudtasks.enqueuer" \
  --condition=None >/dev/null

gcloud iam service-accounts add-iam-policy-binding "$TASKS_SA" \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/iam.serviceAccountUser" >/dev/null

gcloud iam service-accounts add-iam-policy-binding "$TASKS_SA" \
  --member="serviceAccount:${TASKS_AGENT}" \
  --role="roles/iam.serviceAccountUser" >/dev/null

BUILD_SA="$(gcloud builds get-default-service-account --project="$PROJECT_ID")"
if [[ -z "$BUILD_SA" ]]; then
  echo "Cloud Build default service account could not be resolved." >&2
  exit 2
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BUILD_SA}" \
  --role="roles/cloudbuild.builds.builder" \
  --condition=None >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BUILD_SA}" \
  --role="roles/artifactregistry.writer" \
  --condition=None >/dev/null

echo "Building API and browser-worker images..."
gcloud builds submit --config=infra/gcp/cloudbuild-api.yaml \
  --substitutions="_IMAGE=${API_IMAGE}" .
gcloud builds submit --config=infra/gcp/cloudbuild-worker.yaml \
  --substitutions="_IMAGE=${WORKER_IMAGE}" .

COMMON_ENV="PERSISTENCE_BACKEND=firestore|FIRESTORE_PROJECT_ID=${PROJECT_ID}|FIRESTORE_DATABASE=${FIRESTORE_DATABASE}|SCHEDULER_TIMEZONE=Africa/Cairo|REFRESH_CRON=0 * * * *|REFRESH_INTERVAL_MINUTES=60|FRESHNESS_MINUTES=75|STALE_AFTER_MINUTES=150"

echo "Deploying public API..."
gcloud run deploy "$API_SERVICE" \
  --image="$API_IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --service-account="$API_SA" \
  --execution-environment=gen2 \
  --allow-unauthenticated \
  --set-env-vars="^|^SERVICE_MODE=api|${COMMON_ENV}|ALLOWED_ORIGINS=${ALLOWED_ORIGINS}|WEB_CONCURRENCY=1" \
  --cpu=1 \
  --memory=512Mi \
  --concurrency=80 \
  --min-instances=0 \
  --max-instances=1 \
  --timeout=60

echo "Deploying private scraping worker..."
gcloud run deploy "$WORKER_SERVICE" \
  --image="$WORKER_IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --service-account="$WORKER_SA" \
  --execution-environment=gen2 \
  --no-allow-unauthenticated \
  --set-env-vars="^|^SERVICE_MODE=worker|${COMMON_ENV}|TASKS_MODE=cloud|GCP_PROJECT_ID=${PROJECT_ID}|GCP_REGION=${REGION}|CLOUD_TASKS_LOCATION=${TASKS_LOCATION}|CLOUD_TASKS_QUEUE=${QUEUE}|CLOUD_TASKS_MAX_ATTEMPTS=5|TASK_DISPATCH_DEADLINE_SECONDS=900|TASKS_SERVICE_ACCOUNT_EMAIL=${TASKS_SA}|TASK_STAGGER_SECONDS=2700|STALE_TASK_AFTER_MINUTES=180|WEB_CONCURRENCY=1|USER_AGENT=${BOT_USER_AGENT}|INTERNAL_TOKEN=" \
  --cpu=1 \
  --memory=2Gi \
  --concurrency=1 \
  --min-instances=0 \
  --max-instances=1 \
  --timeout=900

WORKER_URL="https://${WORKER_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
API_URL="https://${API_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
PUBLIC_BOT_CONTACT_URL="${API_URL%/}/bot"
PUBLIC_BOT_USER_AGENT="Sa3arlyPriceBot/1.0 (+${PUBLIC_BOT_CONTACT_URL})"

gcloud run services update "$WORKER_SERVICE" \
  --region="$REGION" \
  --update-env-vars="^|^WORKER_URL=${WORKER_URL}|USER_AGENT=${PUBLIC_BOT_USER_AGENT}" >/dev/null

gcloud run services add-iam-policy-binding "$WORKER_SERVICE" \
  --region="$REGION" \
  --member="serviceAccount:${TASKS_SA}" \
  --role="roles/run.invoker" >/dev/null
gcloud run services add-iam-policy-binding "$WORKER_SERVICE" \
  --region="$REGION" \
  --member="serviceAccount:${SCHEDULER_SA}" \
  --role="roles/run.invoker" >/dev/null

if ! gcloud tasks queues describe "$QUEUE" \
  --location="$TASKS_LOCATION" >/dev/null 2>&1; then
  gcloud tasks queues create "$QUEUE" \
    --location="$TASKS_LOCATION" \
    --max-dispatches-per-second=1 \
    --max-concurrent-dispatches=1
fi
gcloud tasks queues update "$QUEUE" \
  --location="$TASKS_LOCATION" \
  --max-dispatches-per-second=1 \
  --max-concurrent-dispatches=1 \
  --max-attempts=3 \
  --min-backoff=10s \
  --max-backoff=3600s \
  --max-doublings=5
pause_queue_if_present

echo "Seeding Firestore and building ready comparison documents..."
gcloud run jobs deploy "$BOOTSTRAP_JOB" \
  --image="$API_IMAGE" \
  --region="$REGION" \
  --service-account="$BOOTSTRAP_SA" \
  --set-env-vars="^|^PERSISTENCE_BACKEND=firestore|FIRESTORE_PROJECT_ID=${PROJECT_ID}|FIRESTORE_DATABASE=${FIRESTORE_DATABASE}|FRESHNESS_MINUTES=75|STALE_AFTER_MINUTES=150" \
  --command=python \
  --args=scripts/bootstrap_firestore.py \
  --cpu=1 \
  --memory=1Gi \
  --max-retries=1 \
  --task-timeout=1800s
gcloud run jobs execute "$BOOTSTRAP_JOB" --region="$REGION" --wait

SCHEDULER_ARGS=(
  --location="$REGION"
  --schedule="0 * * * *"
  --time-zone="Africa/Cairo"
  --uri="${WORKER_URL}/internal/scheduler/refresh"
  --http-method=POST
  --attempt-deadline=900s
  --max-retry-attempts=1
  --max-retry-duration=1800s
  --min-backoff=10s
  --max-backoff=300s
  --max-doublings=4
  --oidc-service-account-email="$SCHEDULER_SA"
  --oidc-token-audience="$WORKER_URL"
  --message-body='{"trigger":"cloud-scheduler-hourly"}'
)

if pause_scheduler_if_present; then
  gcloud scheduler jobs update http "$SCHEDULER_JOB" \
    "${SCHEDULER_ARGS[@]}" \
    --update-headers="Content-Type=application/json"
else
  gcloud scheduler jobs create http "$SCHEDULER_JOB" \
    --location="$REGION" \
    --schedule="0 * * * *" \
    --time-zone="Africa/Cairo" \
    --uri="https://example.invalid/sa3arly-scheduler-disabled" \
    --http-method=POST \
    --max-retry-attempts=0
  gcloud scheduler jobs pause "$SCHEDULER_JOB" \
    --location="$REGION" >/dev/null
  gcloud scheduler jobs update http "$SCHEDULER_JOB" \
    "${SCHEDULER_ARGS[@]}" \
    --update-headers="Content-Type=application/json"
fi

pause_scheduler_if_present

IMMEDIATE_RUN_STATUS="not requested; scheduler paused pending cost controls"
if [[ "$COST_CONTROLS_CONFIRMED" == "1" ]]; then
  echo "Cost controls confirmed; enabling the schedule and starting the first cycle..."
  gcloud tasks queues resume "$QUEUE" --location="$TASKS_LOCATION"
  gcloud scheduler jobs resume "$SCHEDULER_JOB" --location="$REGION"
  gcloud scheduler jobs run "$SCHEDULER_JOB" --location="$REGION"
  IMMEDIATE_RUN_STATUS="requested after cost-control confirmation"
fi

echo
echo "Firestore deployment complete."
echo "API URL:          ${API_URL}"
echo "Worker URL:       ${WORKER_URL}"
echo "Refresh schedule: 00:00 and 12:00 (Africa/Cairo)"
echo "Immediate run:    ${IMMEDIATE_RUN_STATUS}"
echo "Health:           ${API_URL}/healthz"
echo "Status:           ${API_URL}/api/v1/status"
echo
echo "Cloud Billing spend-cap setup is a separate Console step."
echo "Create a USD 20 spend-cap budget scoped to this project and Cloud Run."
echo "Its automatic notices are 50% (USD 10), 80%, and 100%."
echo "Enforcement is not instantaneous; in-flight usage and delayed overage can be billed."
echo "An optional all-services billing budget is alert-only and is not a hard cap."
if [[ "$COST_CONTROLS_CONFIRMED" != "1" ]]; then
  echo
  echo "Scheduler and task queue are PAUSED. After the Cloud Run cap is configured, run:"
  echo "  COST_CONTROLS_CONFIRMED=1 bash START_SA3ARLY_COLLECTION.sh"
fi
