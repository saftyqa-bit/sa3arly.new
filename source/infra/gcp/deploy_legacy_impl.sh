#!/usr/bin/env bash
set -Eeuo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to the Google Cloud/Firebase project ID}"
: "${DB_PASSWORD:?Set DB_PASSWORD to a strong PostgreSQL password}"
: "${BOT_CONTACT_URL:?Set BOT_CONTACT_URL to a public bot-policy/contact URL}"

REGION="${REGION:-europe-west1}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
REPOSITORY="${REPOSITORY:-sa3arly}"
API_SERVICE="${API_SERVICE:-sa3arly-api}"
WORKER_SERVICE="${WORKER_SERVICE:-sa3arly-worker}"
BOOTSTRAP_JOB="${BOOTSTRAP_JOB:-sa3arly-postgres-bootstrap}"
MIGRATION_JOB="${MIGRATION_JOB:-sa3arly-firestore-to-postgres}"
QUEUE="${QUEUE:-sa3arly-scrape}"
SCHEDULER_JOB="${SCHEDULER_JOB:-sa3arly-hourly-refresh}"
LEGACY_SCHEDULER_JOB="${LEGACY_SCHEDULER_JOB:-sa3arly-12h-refresh}"
DB_INSTANCE="${DB_INSTANCE:-sa3arly-postgres}"
DB_NAME="${DB_NAME:-sa3arly}"
DB_USER="${DB_USER:-sa3arly}"
DB_CPU="${DB_CPU:-1}"
DB_MEMORY="${DB_MEMORY:-3840MB}"
CREATE_CLOUD_SQL="${CREATE_CLOUD_SQL:-1}"
DEPLOY_FIREBASE="${DEPLOY_FIREBASE:-0}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-*}"
FIRESTORE_DATABASE="${FIRESTORE_DATABASE:-(default)}"
RELEASE_ID="${RELEASE_ID:-postgres12h-$(date -u +%Y%m%d-%H%M%S)}"

if [[ ! "$BOT_CONTACT_URL" =~ ^https?:// ]]; then
  echo "BOT_CONTACT_URL must start with http:// or https://" >&2
  exit 2
fi
BOT_USER_AGENT="${BOT_USER_AGENT:-Sa3arlyPriceBot/1.0 (+${BOT_CONTACT_URL})}"

API_SA="sa3arly-api@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="sa3arly-worker@${PROJECT_ID}.iam.gserviceaccount.com"
BOOTSTRAP_SA="sa3arly-bootstrap@${PROJECT_ID}.iam.gserviceaccount.com"
TASKS_SA="sa3arly-tasks@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="sa3arly-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
INSTANCE_CONNECTION="${PROJECT_ID}:${REGION}:${DB_INSTANCE}"
DB_SECRET="sa3arly-database-url"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"
API_IMAGE="${IMAGE_BASE}/api:${RELEASE_ID}"
WORKER_IMAGE="${IMAGE_BASE}/worker:${RELEASE_ID}"
CUTOVER_STARTED=0
PREVIOUS_API_REVISION=""
PREVIOUS_WORKER_REVISION=""

pause_queue_if_present() {
  if gcloud tasks queues describe "$QUEUE" --location="$TASKS_LOCATION" >/dev/null 2>&1; then
    local queue_state
    queue_state="$(gcloud tasks queues describe "$QUEUE" \
      --location="$TASKS_LOCATION" --format='value(state)')"
    if [[ "$queue_state" != "PAUSED" ]]; then
      gcloud tasks queues pause "$QUEUE" --location="$TASKS_LOCATION" >/dev/null
    fi
  fi
}

pause_scheduler_if_present() {
  local job_name="$1"
  if gcloud scheduler jobs describe "$job_name" --location="$REGION" >/dev/null 2>&1; then
    local scheduler_state
    scheduler_state="$(gcloud scheduler jobs describe "$job_name" \
      --location="$REGION" --format='value(state)')"
    if [[ "$scheduler_state" != "PAUSED" ]]; then
      gcloud scheduler jobs pause "$job_name" --location="$REGION" >/dev/null
    fi
    return 0
  fi
  return 1
}

require_empty_queue_before_cutover() {
  if ! gcloud tasks queues describe "$QUEUE" \
    --location="$TASKS_LOCATION" >/dev/null 2>&1; then
    return 0
  fi
  local pending_task
  pending_task="$(gcloud tasks list \
    --queue="$QUEUE" \
    --location="$TASKS_LOCATION" \
    --limit=1 \
    --format='value(name)')"
  if [[ -n "$pending_task" ]]; then
    echo "The paused queue contains legacy tasks; refusing to purge or cut over automatically." >&2
    echo "Review or purge the old tasks explicitly, then rerun this deployment." >&2
    exit 2
  fi
}

serving_revision() {
  local service_name="$1"
  if ! gcloud run services describe "$service_name" \
    --region="$REGION" --format=json >/dev/null 2>&1; then
    return 0
  fi
  gcloud run services describe "$service_name" \
    --region="$REGION" --format=json | python3 -c 'import json, sys
service = json.load(sys.stdin)
traffic = service.get("status", {}).get("traffic", [])
traffic = [item for item in traffic if item.get("revisionName")]
traffic.sort(key=lambda item: int(item.get("percent") or 0), reverse=True)
print(traffic[0]["revisionName"] if traffic else "")'
}

rollback_on_error() {
  local exit_code="$?"
  trap - ERR
  set +e
  pause_queue_if_present
  pause_scheduler_if_present "$SCHEDULER_JOB"
  pause_scheduler_if_present "$LEGACY_SCHEDULER_JOB"
  if [[ "$CUTOVER_STARTED" == "1" ]]; then
    if [[ -n "$PREVIOUS_API_REVISION" ]]; then
      gcloud run services update-traffic "$API_SERVICE" \
        --region="$REGION" --to-revisions="${PREVIOUS_API_REVISION}=100"
    fi
    if [[ -n "$PREVIOUS_WORKER_REVISION" ]]; then
      gcloud run services update-traffic "$WORKER_SERVICE" \
        --region="$REGION" --to-revisions="${PREVIOUS_WORKER_REVISION}=100"
    fi
  fi
  echo "Deployment failed. Collection is paused and previous service traffic was restored when available." >&2
  exit "$exit_code"
}

trap rollback_on_error ERR

create_sa() {
  local short_name="$1"
  local display_name="$2"
  if ! gcloud iam service-accounts describe \
    "${short_name}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$short_name" --display-name="$display_name"
  fi
}

gcloud config set project "$PROJECT_ID" >/dev/null

echo "Pausing all collection before the migration..."
pause_queue_if_present
pause_scheduler_if_present "$SCHEDULER_JOB" || true
pause_scheduler_if_present "$LEGACY_SCHEDULER_JOB" || true
require_empty_queue_before_cutover

echo "Enabling required Google Cloud APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudtasks.googleapis.com \
  iamcredentials.googleapis.com \
  firestore.googleapis.com

if ! gcloud artifacts repositories describe "$REPOSITORY" \
  --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPOSITORY" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Sa3arly application images"
fi

create_sa sa3arly-api "Sa3arly API"
create_sa sa3arly-worker "Sa3arly scheduled worker"
create_sa sa3arly-bootstrap "Sa3arly database migration"
create_sa sa3arly-tasks "Sa3arly Cloud Tasks caller"
create_sa sa3arly-scheduler "Sa3arly Cloud Scheduler caller"

if [[ "$CREATE_CLOUD_SQL" == "1" ]]; then
  if ! gcloud sql instances describe "$DB_INSTANCE" >/dev/null 2>&1; then
    echo "Creating Cloud SQL PostgreSQL 16 instance..."
    gcloud sql instances create "$DB_INSTANCE" \
      --database-version=POSTGRES_16 \
      --region="$REGION" \
      --edition=enterprise \
      --cpu="$DB_CPU" \
      --memory="$DB_MEMORY" \
      --storage-type=SSD \
      --storage-size=20 \
      --storage-auto-increase \
      --availability-type=zonal \
      --backup-start-time=02:00 \
      --enable-point-in-time-recovery \
      --retained-backups-count=7 \
      --retained-transaction-log-days=7 \
      --retain-backups-on-delete \
      --final-backup \
      --final-backup-retention-days=30 \
      --deletion-protection
  fi

  gcloud sql instances patch "$DB_INSTANCE" \
    --backup-start-time=02:00 \
    --enable-point-in-time-recovery \
    --retained-backups-count=7 \
    --retained-transaction-log-days=7 \
    --retain-backups-on-delete \
    --final-backup \
    --final-backup-retention-days=30 \
    --deletion-protection \
    --quiet

  if ! gcloud sql databases describe "$DB_NAME" \
    --instance="$DB_INSTANCE" >/dev/null 2>&1; then
    gcloud sql databases create "$DB_NAME" --instance="$DB_INSTANCE"
  fi

  if gcloud sql users list --instance="$DB_INSTANCE" \
    --format="value(name)" | grep -Fxq "$DB_USER"; then
    gcloud sql users set-password "$DB_USER" \
      --instance="$DB_INSTANCE" \
      --password="$DB_PASSWORD"
  else
    gcloud sql users create "$DB_USER" \
      --instance="$DB_INSTANCE" \
      --password="$DB_PASSWORD"
  fi
fi

ENCODED_PASSWORD="$(python3 - "$DB_PASSWORD" <<'PY'
import sys
import urllib.parse

print(urllib.parse.quote(sys.argv[1], safe=""))
PY
)"
DATABASE_URL="postgresql://${DB_USER}:${ENCODED_PASSWORD}@/${DB_NAME}?host=/cloudsql/${INSTANCE_CONNECTION}"

if ! gcloud secrets describe "$DB_SECRET" >/dev/null 2>&1; then
  gcloud secrets create "$DB_SECRET" --replication-policy=automatic
fi
printf '%s' "$DATABASE_URL" | \
  gcloud secrets versions add "$DB_SECRET" --data-file=- >/dev/null

for sa in "$API_SA" "$WORKER_SA" "$BOOTSTRAP_SA"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${sa}" \
    --role="roles/cloudsql.client" \
    --condition=None >/dev/null
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${sa}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None >/dev/null
done

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BOOTSTRAP_SA}" \
  --role="roles/datastore.viewer" \
  --condition=None >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/cloudtasks.enqueuer" \
  --condition=None >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$TASKS_SA" \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/iam.serviceAccountUser" >/dev/null

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
TASKS_AGENT="service-${PROJECT_NUMBER}@gcp-sa-cloudtasks.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "$TASKS_SA" \
  --member="serviceAccount:${TASKS_AGENT}" \
  --role="roles/iam.serviceAccountUser" >/dev/null

echo "Building immutable PostgreSQL images: ${RELEASE_ID}"
gcloud builds submit --config=infra/gcp/cloudbuild-api.yaml \
  --substitutions="_IMAGE=${API_IMAGE}" .
gcloud builds submit --config=infra/gcp/cloudbuild-worker.yaml \
  --substitutions="_IMAGE=${WORKER_IMAGE}" .

echo "Creating the PostgreSQL schema and importing the approved seed..."
gcloud run jobs deploy "$BOOTSTRAP_JOB" \
  --image="$API_IMAGE" \
  --region="$REGION" \
  --service-account="$BOOTSTRAP_SA" \
  --set-cloudsql-instances="$INSTANCE_CONNECTION" \
  --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
  --command=python \
  --args=scripts/bootstrap_db.py \
  --cpu=1 \
  --memory=1Gi \
  --max-retries=0 \
  --task-timeout=1800s
gcloud run jobs execute "$BOOTSTRAP_JOB" --region="$REGION" --wait

echo "Copying Firestore runtime state and archiving legacy runs..."
gcloud run jobs deploy "$MIGRATION_JOB" \
  --image="$API_IMAGE" \
  --region="$REGION" \
  --service-account="$BOOTSTRAP_SA" \
  --set-cloudsql-instances="$INSTANCE_CONNECTION" \
  --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
  --set-env-vars="^|^FIRESTORE_PROJECT_ID=${PROJECT_ID}|FIRESTORE_DATABASE=${FIRESTORE_DATABASE}" \
  --command=python \
  --args=scripts/migrate_firestore_runtime_to_postgres.py \
  --cpu=1 \
  --memory=1Gi \
  --max-retries=0 \
  --task-timeout=1800s
gcloud run jobs execute "$MIGRATION_JOB" --region="$REGION" --wait

PREVIOUS_API_REVISION="$(serving_revision "$API_SERVICE")"
PREVIOUS_WORKER_REVISION="$(serving_revision "$WORKER_SERVICE")"
CUTOVER_STARTED=1

COMMON_ENV="PERSISTENCE_BACKEND=postgres|SCHEDULER_TIMEZONE=Africa/Cairo|REFRESH_CRON=0 10,20 * * *|REFRESH_INTERVAL_MINUTES=720|FRESHNESS_MINUTES=855|STALE_AFTER_MINUTES=1710|CATALOG_DISCOVERY_CRON=30 2 * * *|CATALOG_DISCOVERY_STORES_PER_RUN=35|CATALOG_TASKS_QUEUE=sa3arly-catalog-discovery"

echo "Deploying the PostgreSQL-backed public API..."
gcloud run deploy "$API_SERVICE" \
  --image="$API_IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --service-account="$API_SA" \
  --execution-environment=gen2 \
  --allow-unauthenticated \
  --add-cloudsql-instances="$INSTANCE_CONNECTION" \
  --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
  --set-env-vars="^|^SERVICE_MODE=api|${COMMON_ENV}|ALLOWED_ORIGINS=${ALLOWED_ORIGINS}|DB_POOL_MIN=1|DB_POOL_MAX=5|WEB_CONCURRENCY=1" \
  --cpu=1 \
  --memory=512Mi \
  --concurrency=80 \
  --min-instances=0 \
  --max-instances=1 \
  --timeout=60

echo "Deploying the private PostgreSQL scraping worker..."
gcloud run deploy "$WORKER_SERVICE" \
  --image="$WORKER_IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --service-account="$WORKER_SA" \
  --execution-environment=gen2 \
  --no-allow-unauthenticated \
  --add-cloudsql-instances="$INSTANCE_CONNECTION" \
  --set-secrets="DATABASE_URL=${DB_SECRET}:latest" \
  --set-env-vars="^|^SERVICE_MODE=worker|${COMMON_ENV}|TASKS_MODE=cloud|GCP_PROJECT_ID=${PROJECT_ID}|GCP_REGION=${REGION}|CLOUD_TASKS_LOCATION=${TASKS_LOCATION}|CLOUD_TASKS_QUEUE=${QUEUE}|CLOUD_TASKS_MAX_ATTEMPTS=3|TASK_DISPATCH_DEADLINE_SECONDS=900|TASKS_SERVICE_ACCOUNT_EMAIL=${TASKS_SA}|TASK_STAGGER_SECONDS=2700|STALE_TASK_AFTER_MINUTES=180|DB_POOL_MIN=1|DB_POOL_MAX=4|WEB_CONCURRENCY=1|USER_AGENT=${BOT_USER_AGENT}|INTERNAL_TOKEN=" \
  --cpu=1 \
  --memory=2Gi \
  --concurrency=1 \
  --min-instances=0 \
  --max-instances=1 \
  --timeout=900

# Use Cloud Run's deterministic project-number URLs. The older hash-based URL
# assigned to this project has previously returned 404 before reaching a revision.
WORKER_URL="https://${WORKER_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
API_URL="https://${API_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
PUBLIC_BOT_USER_AGENT="Sa3arlyPriceBot/1.0 (+${API_URL%/}/bot)"

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

SCHEDULER_ARGS=(
  --location="$REGION"
  --schedule="0 10,20 * * *"
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
  --message-body='{"trigger":"daily-10am-8pm"}'
)

if gcloud scheduler jobs describe "$SCHEDULER_JOB" \
  --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SCHEDULER_JOB" \
    "${SCHEDULER_ARGS[@]}" \
    --update-headers="Content-Type=application/json"
else
  gcloud scheduler jobs create http "$SCHEDULER_JOB" \
    "${SCHEDULER_ARGS[@]}" \
    --headers="Content-Type=application/json"
fi
pause_scheduler_if_present "$SCHEDULER_JOB"
pause_scheduler_if_present "$LEGACY_SCHEDULER_JOB" || true

echo "Verifying the cutover while collection remains paused..."
READY_RESPONSE="$(curl --fail-with-body --silent --show-error \
  --max-time 120 "${API_URL}/readyz")"
STATUS_RESPONSE="$(curl --fail-with-body --silent --show-error \
  --max-time 120 "${API_URL}/api/v1/status")"
python3 - "$READY_RESPONSE" "$STATUS_RESPONSE" db/seed/seed_metadata.json <<'PY'
import json
import sys

ready = json.loads(sys.argv[1])
status = json.loads(sys.argv[2])
metadata = json.load(open(sys.argv[3], encoding="utf-8"))
expected = metadata["counts"]
checks = {
    "database_ready": ready.get("ok") is True,
    "products": status.get("products") == expected["product_variants"],
    "stores": status.get("registry_stores") == expected["stores"],
    "active_mappings": status.get("active_mappings") == 2332,
    "refresh_interval": status.get("refresh_interval_minutes") == 60,
    "timezone": status.get("scheduler_timezone") == "Africa/Cairo",
}
print(json.dumps({"checks": checks, "status": status}, ensure_ascii=False, indent=2))
if not all(checks.values()):
    raise SystemExit("PostgreSQL cutover verification failed")
PY

QUEUE_STATE="$(gcloud tasks queues describe "$QUEUE" \
  --location="$TASKS_LOCATION" --format='value(state)')"
SCHEDULER_STATE="$(gcloud scheduler jobs describe "$SCHEDULER_JOB" \
  --location="$REGION" --format='value(state)')"
SCHEDULER_SCHEDULE="$(gcloud scheduler jobs describe "$SCHEDULER_JOB" \
  --location="$REGION" --format='value(schedule)')"
SCHEDULER_TIME_ZONE="$(gcloud scheduler jobs describe "$SCHEDULER_JOB" \
  --location="$REGION" --format='value(timeZone)')"
SCHEDULER_URI="$(gcloud scheduler jobs describe "$SCHEDULER_JOB" \
  --location="$REGION" --format='value(httpTarget.uri)')"
if [[ "$QUEUE_STATE" != "PAUSED" \
  || "$SCHEDULER_STATE" != "PAUSED" \
  || "$SCHEDULER_SCHEDULE" != "0 10,20 * * *" \
  || "$SCHEDULER_TIME_ZONE" != "Africa/Cairo" \
  || "$SCHEDULER_URI" != "${WORKER_URL}/internal/scheduler/refresh" ]]; then
  echo "Paused-state or hourly scheduler verification failed." >&2
  false
fi

if [[ "$DEPLOY_FIREBASE" == "1" ]] && command -v firebase >/dev/null 2>&1; then
  cp .firebaserc.example .firebaserc
  python3 - "$PROJECT_ID" "$API_SERVICE" "$REGION" <<'PY'
import json
import sys

project_id, service_id, region = sys.argv[1:]
with open(".firebaserc", encoding="utf-8") as handle:
    projects = json.load(handle)
projects["projects"]["default"] = project_id
with open(".firebaserc", "w", encoding="utf-8") as handle:
    json.dump(projects, handle, indent=2)

with open("firebase.json", encoding="utf-8") as handle:
    hosting = json.load(handle)
for rewrite in hosting.get("hosting", {}).get("rewrites", []):
    if "run" in rewrite:
        rewrite["run"]["serviceId"] = service_id
        rewrite["run"]["region"] = region
with open("firebase.json", "w", encoding="utf-8") as handle:
    json.dump(hosting, handle, indent=2, ensure_ascii=False)
PY
  firebase deploy --only hosting
fi

echo
echo "PostgreSQL migration and twice-daily configuration completed."
echo "API URL:          ${API_URL}"
echo "Worker URL:       ${WORKER_URL}"
echo "Refresh schedule: 10:00 and 20:00 (Africa/Cairo)"
echo "Queue:            PAUSED"
echo "Scheduler:        PAUSED"
echo "Firestore:        retained as read-only rollback source"
echo "Release:          ${RELEASE_ID}"
echo
echo "After reviewing the verification output, activate with:"
echo "  COST_CONTROLS_CONFIRMED=1 bash START_SA3ARLY_COLLECTION.sh"
