#!/usr/bin/env bash
echo "This legacy hourly activator is disabled in v0.5.1. Use DEPLOY_CATALOG_DISCOVERY.sh; price refresh remains at 10:00 and 20:00 Cairo." >&2
exit 2
set -Eeuo pipefail

: "${HOURLY_CUTOVER_CONFIRMED:?Set HOURLY_CUTOVER_CONFIRMED=1 to enable the production cutover}"
if [[ "$HOURLY_CUTOVER_CONFIRMED" != "1" ]]; then
  echo "HOURLY_CUTOVER_CONFIRMED must equal 1." >&2
  exit 2
fi

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
REPOSITORY="${REPOSITORY:-sa3arly}"
API_SERVICE="${API_SERVICE:-sa3arly-api}"
WORKER_SERVICE="${WORKER_SERVICE:-sa3arly-worker}"
QUEUE="${QUEUE:-sa3arly-scrape}"
SCHEDULER_JOB="${SCHEDULER_JOB:-sa3arly-hourly-refresh}"
LEGACY_SCHEDULER_JOB="${LEGACY_SCHEDULER_JOB:-sa3arly-12h-refresh}"
SCHEDULER_SA="${SCHEDULER_SA:-sa3arly-scheduler@${PROJECT_ID}.iam.gserviceaccount.com}"
DRAIN_TIMEOUT_SECONDS="${DRAIN_TIMEOUT_SECONDS:-14400}"
RELEASE_ID="${RELEASE_ID:-hourly-0-4-11-$(date -u +%Y%m%d-%H%M%S)}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAPSHOT_DIR="${SOURCE_DIR}/hourly-cutover-snapshot-$(date -u +%Y%m%d-%H%M%S)"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"
API_IMAGE="${IMAGE_BASE}/api:${RELEASE_ID}"
WORKER_IMAGE="${IMAGE_BASE}/worker:${RELEASE_ID}"
PREVIOUS_API_REVISION=""
PREVIOUS_WORKER_REVISION=""
CANDIDATE_API_REVISION=""
CANDIDATE_WORKER_REVISION=""
CUTOVER_STARTED=0

pause_scheduler_if_present() {
  local name="$1"
  if gcloud scheduler jobs describe "$name" \
    --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    local state
    state="$(gcloud scheduler jobs describe "$name" \
      --location="$REGION" --project="$PROJECT_ID" --format='value(state)')"
    if [[ "$state" != "PAUSED" ]]; then
      gcloud scheduler jobs pause "$name" \
        --location="$REGION" --project="$PROJECT_ID" >/dev/null
    fi
  fi
}

serving_revision() {
  local service="$1"
  gcloud run services describe "$service" \
    --region="$REGION" --project="$PROJECT_ID" --format=json | python3 -c '
import json, sys
service = json.load(sys.stdin)
traffic = [x for x in service.get("status", {}).get("traffic", []) if x.get("revisionName")]
traffic.sort(key=lambda x: int(x.get("percent") or 0), reverse=True)
print(traffic[0]["revisionName"] if traffic else "")'
}

queue_has_tasks() {
  [[ -n "$(gcloud tasks list --queue="$QUEUE" --location="$TASKS_LOCATION" \
    --project="$PROJECT_ID" --limit=1 --format='value(name)')" ]]
}

queue_task_count() {
  gcloud tasks list --queue="$QUEUE" --location="$TASKS_LOCATION" \
    --project="$PROJECT_ID" --limit=10000 --format='value(name)' | awk 'NF {count++} END {print count+0}'
}

rollback_on_error() {
  local exit_code="$?"
  trap - ERR
  set +e
  pause_scheduler_if_present "$SCHEDULER_JOB"
  pause_scheduler_if_present "$LEGACY_SCHEDULER_JOB"
  gcloud tasks queues pause "$QUEUE" --location="$TASKS_LOCATION" \
    --project="$PROJECT_ID" >/dev/null 2>&1
  if [[ "$CUTOVER_STARTED" == "1" ]]; then
    if [[ -n "$PREVIOUS_API_REVISION" ]]; then
      gcloud run services update-traffic "$API_SERVICE" --region="$REGION" \
        --project="$PROJECT_ID" --to-revisions="${PREVIOUS_API_REVISION}=100" >/dev/null
    fi
    if [[ -n "$PREVIOUS_WORKER_REVISION" ]]; then
      gcloud run services update-traffic "$WORKER_SERVICE" --region="$REGION" \
        --project="$PROJECT_ID" --to-revisions="${PREVIOUS_WORKER_REVISION}=100" >/dev/null
    fi
  fi
  echo "HOURLY_CUTOVER=FAILED_SAFE" >&2
  echo "QUEUE=PAUSED" >&2
  echo "SCHEDULER=PAUSED" >&2
  echo "SNAPSHOT_DIR=${SNAPSHOT_DIR}" >&2
  exit "$exit_code"
}

trap rollback_on_error ERR
cd "$SOURCE_DIR"

for command in gcloud curl python3 awk; do
  command -v "$command" >/dev/null || {
    echo "Missing required command: $command" >&2
    exit 2
  }
done

gcloud config set project "$PROJECT_ID" >/dev/null
ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -1)"
[[ -n "$ACTIVE_ACCOUNT" ]] || { echo "No active gcloud account." >&2; exit 2; }

mkdir -p "$SNAPSHOT_DIR"
gcloud run services describe "$API_SERVICE" --region="$REGION" \
  --project="$PROJECT_ID" --format=json >"$SNAPSHOT_DIR/api-before.json"
gcloud run services describe "$WORKER_SERVICE" --region="$REGION" \
  --project="$PROJECT_ID" --format=json >"$SNAPSHOT_DIR/worker-before.json"
gcloud tasks queues describe "$QUEUE" --location="$TASKS_LOCATION" \
  --project="$PROJECT_ID" --format=json >"$SNAPSHOT_DIR/queue-before.json"
gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" \
  --format=json >"$SNAPSHOT_DIR/schedulers-before.json"

python3 - "$SNAPSHOT_DIR/api-before.json" "$SNAPSHOT_DIR/worker-before.json" <<'PY'
import json, sys

for path, expected_mode in zip(sys.argv[1:], ("api", "worker"), strict=True):
    service = json.load(open(path, encoding="utf-8"))
    containers = service.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if len(containers) != 1:
        raise SystemExit(f"Unexpected container count in {path}")
    env = {item.get("name"): item.get("value") for item in containers[0].get("env", [])}
    if env.get("SERVICE_MODE") != expected_mode:
        raise SystemExit(f"Unexpected SERVICE_MODE in {path}: {env.get('SERVICE_MODE')!r}")
    if env.get("PERSISTENCE_BACKEND") != "postgres":
        raise SystemExit(f"Production service is not PostgreSQL-backed: {path}")
PY

PREVIOUS_API_REVISION="$(serving_revision "$API_SERVICE")"
PREVIOUS_WORKER_REVISION="$(serving_revision "$WORKER_SERVICE")"
[[ -n "$PREVIOUS_API_REVISION" && -n "$PREVIOUS_WORKER_REVISION" ]]

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
WORKER_URL="$(python3 - "$SNAPSHOT_DIR/worker-before.json" <<'PY'
import json, sys
service = json.load(open(sys.argv[1], encoding="utf-8"))
containers = service.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
env = {item.get("name"): item.get("value") for item in (containers[0].get("env", []) if containers else [])}
print(env.get("WORKER_URL") or "")
PY
)"
if [[ ! "$WORKER_URL" =~ ^https:// ]]; then
  WORKER_URL="https://${WORKER_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
fi

echo "Pausing collection and automatic triggers..."
pause_scheduler_if_present "$SCHEDULER_JOB"
pause_scheduler_if_present "$LEGACY_SCHEDULER_JOB"
gcloud tasks queues pause "$QUEUE" --location="$TASKS_LOCATION" \
  --project="$PROJECT_ID" >/dev/null

echo "Building immutable hourly images: ${RELEASE_ID}"
gcloud builds submit --project="$PROJECT_ID" --config=infra/gcp/cloudbuild-api.yaml \
  --substitutions="_IMAGE=${API_IMAGE}" .
gcloud builds submit --project="$PROJECT_ID" --config=infra/gcp/cloudbuild-worker.yaml \
  --substitutions="_IMAGE=${WORKER_IMAGE}" .

CUTOVER_STARTED=1
COMMON_ENV='^|^PERSISTENCE_BACKEND=postgres|SCHEDULER_TIMEZONE=Africa/Cairo|REFRESH_CRON=0 * * * *|REFRESH_INTERVAL_MINUTES=60|FRESHNESS_MINUTES=75|STALE_AFTER_MINUTES=150'

echo "Deploying the hourly API revision while preserving the existing service configuration..."
gcloud run services update "$API_SERVICE" --region="$REGION" --project="$PROJECT_ID" \
  --image="$API_IMAGE" \
  --update-env-vars="${COMMON_ENV}|SERVICE_MODE=api" \
  --max-instances=1 >/dev/null
CANDIDATE_API_REVISION="$(gcloud run services describe "$API_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" \
  --format='value(status.latestReadyRevisionName)')"
if [[ -z "$CANDIDATE_API_REVISION" || "$CANDIDATE_API_REVISION" == "$PREVIOUS_API_REVISION" ]]; then
  echo "Cloud Run did not produce a distinct hourly API candidate revision." >&2
  exit 3
fi
gcloud run services update-traffic "$API_SERVICE" --region="$REGION" \
  --project="$PROJECT_ID" --to-revisions="${CANDIDATE_API_REVISION}=100" >/dev/null

echo "Deploying the hourly worker revision while preserving the existing service configuration..."
gcloud run services update "$WORKER_SERVICE" --region="$REGION" --project="$PROJECT_ID" \
  --image="$WORKER_IMAGE" \
  --update-env-vars="${COMMON_ENV}|SERVICE_MODE=worker|WORKER_URL=${WORKER_URL}" \
  --max-instances=1 >/dev/null
CANDIDATE_WORKER_REVISION="$(gcloud run services describe "$WORKER_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" \
  --format='value(status.latestReadyRevisionName)')"
if [[ -z "$CANDIDATE_WORKER_REVISION" || "$CANDIDATE_WORKER_REVISION" == "$PREVIOUS_WORKER_REVISION" ]]; then
  echo "Cloud Run did not produce a distinct hourly worker candidate revision." >&2
  exit 3
fi
gcloud run services update-traffic "$WORKER_SERVICE" --region="$REGION" \
  --project="$PROJECT_ID" --to-revisions="${CANDIDATE_WORKER_REVISION}=100" >/dev/null

gcloud tasks queues update "$QUEUE" --location="$TASKS_LOCATION" \
  --project="$PROJECT_ID" \
  --max-dispatches-per-second=1 \
  --max-concurrent-dispatches=1 \
  --max-attempts=3 \
  --min-backoff=10s \
  --max-backoff=3600s \
  --max-doublings=5 >/dev/null

gcloud run services add-iam-policy-binding "$WORKER_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHEDULER_SA}" \
  --role="roles/run.invoker" >/dev/null

SCHEDULER_ARGS=(
  --location="$REGION"
  --project="$PROJECT_ID"
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
  --update-headers="Content-Type=application/json"
  --message-body='{"trigger":"cloud-scheduler-hourly"}'
)

if ! gcloud scheduler jobs describe "$SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud scheduler jobs create http "$SCHEDULER_JOB" \
    --location="$REGION" --project="$PROJECT_ID" \
    --schedule="0 * * * *" \
    --time-zone="Africa/Cairo" \
    --uri="https://example.invalid/sa3arly-scheduler-disabled" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"trigger":"disabled-during-cutover"}' >/dev/null
fi
pause_scheduler_if_present "$SCHEDULER_JOB"
gcloud scheduler jobs update http "$SCHEDULER_JOB" "${SCHEDULER_ARGS[@]}" >/dev/null
pause_scheduler_if_present "$SCHEDULER_JOB"
pause_scheduler_if_present "$LEGACY_SCHEDULER_JOB"

API_URL="$(gcloud run services describe "$API_SERVICE" --region="$REGION" \
  --project="$PROJECT_ID" --format='value(status.url)')"
READY_RESPONSE="$(curl --fail-with-body --silent --show-error --max-time 120 "${API_URL}/readyz")"
STATUS_BEFORE="$(curl --fail-with-body --silent --show-error --max-time 120 "${API_URL}/api/v1/status")"
python3 - "$READY_RESPONSE" "$STATUS_BEFORE" <<'PY'
import json, sys
ready = json.loads(sys.argv[1])
status = json.loads(sys.argv[2])
checks = {
    "ready": ready.get("ok") is True,
    "products_present": int(status.get("products") or 0) > 0,
    "mappings_present": int(status.get("active_mappings") or 0) > 0,
    "hourly_interval": status.get("refresh_interval_minutes") == 60,
    "timezone": status.get("scheduler_timezone") == "Africa/Cairo",
}
if not all(checks.values()):
    raise SystemExit(f"Hourly API preflight failed: {checks}; status={status}")
print("HOURLY_API_PREFLIGHT=PASS")
print("STATUS_BEFORE=" + json.dumps(status, ensure_ascii=False, separators=(",", ":")))
PY

PENDING_BEFORE="$(queue_task_count)"
echo "PENDING_TASKS_BEFORE_DRAIN=${PENDING_BEFORE}"
if (( PENDING_BEFORE > 0 )); then
  echo "Resuming the queue to finish existing tasks without purging them..."
  gcloud tasks queues resume "$QUEUE" --location="$TASKS_LOCATION" \
    --project="$PROJECT_ID" >/dev/null
  deadline=$(( $(date +%s) + DRAIN_TIMEOUT_SECONDS ))
  while queue_has_tasks; do
    if (( $(date +%s) >= deadline )); then
      echo "Existing queue did not drain within ${DRAIN_TIMEOUT_SECONDS}s." >&2
      exit 3
    fi
    echo "PENDING_TASKS=$(queue_task_count)"
    sleep 30
  done
else
  gcloud tasks queues resume "$QUEUE" --location="$TASKS_LOCATION" \
    --project="$PROJECT_ID" >/dev/null
fi

echo "Existing queue is empty. Enabling the hourly scheduler and starting one immediate run..."
gcloud scheduler jobs resume "$SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" >/dev/null
gcloud scheduler jobs run "$SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" >/dev/null

deadline=$(( $(date +%s) + 300 ))
while ! queue_has_tasks; do
  if (( $(date +%s) >= deadline )); then
    echo "Scheduler invocation did not create Cloud Tasks within five minutes." >&2
    exit 4
  fi
  sleep 10
done

gcloud run services describe "$API_SERVICE" --region="$REGION" \
  --project="$PROJECT_ID" --format=json >"$SNAPSHOT_DIR/api-after.json"
gcloud run services describe "$WORKER_SERVICE" --region="$REGION" \
  --project="$PROJECT_ID" --format=json >"$SNAPSHOT_DIR/worker-after.json"
gcloud tasks queues describe "$QUEUE" --location="$TASKS_LOCATION" \
  --project="$PROJECT_ID" --format=json >"$SNAPSHOT_DIR/queue-after.json"
gcloud scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" \
  --project="$PROJECT_ID" --format=json >"$SNAPSHOT_DIR/scheduler-after.json"

python3 - "$SNAPSHOT_DIR/worker-after.json" "$SNAPSHOT_DIR/queue-after.json" \
  "$SNAPSHOT_DIR/scheduler-after.json" "$WORKER_URL" "$SCHEDULER_SA" <<'PY'
import json, sys

worker = json.load(open(sys.argv[1], encoding="utf-8"))
queue = json.load(open(sys.argv[2], encoding="utf-8"))
scheduler = json.load(open(sys.argv[3], encoding="utf-8"))
worker_url = sys.argv[4].rstrip("/")
scheduler_sa = sys.argv[5]

containers = worker.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
env = {item.get("name"): item.get("value") for item in containers[0].get("env", [])}
target = scheduler.get("httpTarget", {})
oidc = target.get("oidcToken", {})
rate = queue.get("rateLimits", {})
retry = queue.get("retryConfig", {})
checks = {
    "worker_interval": env.get("REFRESH_INTERVAL_MINUTES") == "60",
    "worker_cron": env.get("REFRESH_CRON") == "0 * * * *",
    "freshness": env.get("FRESHNESS_MINUTES") == "75",
    "stale": env.get("STALE_AFTER_MINUTES") == "150",
    "queue_running": queue.get("state") == "RUNNING",
    "queue_rate": float(rate.get("maxDispatchesPerSecond") or 0) == 1.0,
    "queue_concurrency": int(rate.get("maxConcurrentDispatches") or 0) == 1,
    "queue_attempts": int(retry.get("maxAttempts") or 0) == 3,
    "scheduler_enabled": scheduler.get("state") == "ENABLED",
    "scheduler_cron": scheduler.get("schedule") == "0 * * * *",
    "scheduler_timezone": scheduler.get("timeZone") == "Africa/Cairo",
    "scheduler_uri": str(target.get("uri") or "").rstrip("/") == worker_url + "/internal/scheduler/refresh",
    "scheduler_sa": oidc.get("serviceAccountEmail") == scheduler_sa,
    "scheduler_audience": str(oidc.get("audience") or "").rstrip("/") == worker_url,
}
if not all(checks.values()):
    raise SystemExit(f"Final hourly configuration verification failed: {checks}")
print("HOURLY_CONFIGURATION=PASS")
PY

trap - ERR
echo "HOURLY_CUTOVER=SUCCESS"
echo "QUEUE=RUNNING"
echo "SCHEDULER=${SCHEDULER_JOB}:ENABLED"
echo "SCHEDULE=0 * * * *"
echo "TIMEZONE=Africa/Cairo"
echo "PENDING_TASKS_AFTER_IMMEDIATE_RUN=$(queue_task_count)"
echo "API_URL=${API_URL}"
echo "SNAPSHOT_DIR=${SNAPSHOT_DIR}"
echo "NEXT_STEP=Run ./VERIFY_HOURLY_COLLECTION.sh after this immediate cycle drains."
