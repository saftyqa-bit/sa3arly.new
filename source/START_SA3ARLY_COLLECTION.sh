#!/usr/bin/env bash
set -Eeuo pipefail

: "${COST_CONTROLS_CONFIRMED:?Set COST_CONTROLS_CONFIRMED=1 after configuring the Cloud Run spend cap}"
if [[ "$COST_CONTROLS_CONFIRMED" != "1" ]]; then
  echo "COST_CONTROLS_CONFIRMED must equal 1." >&2
  exit 2
fi

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
SCHEDULER_JOB="${SCHEDULER_JOB:-sa3arly-hourly-refresh}"
LEGACY_SCHEDULER_JOB="${LEGACY_SCHEDULER_JOB:-sa3arly-12h-refresh}"
QUEUE="${QUEUE:-sa3arly-scrape}"

gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
API_URL="https://sa3arly-api-${PROJECT_NUMBER}.${REGION}.run.app"

for service in sa3arly-api sa3arly-worker; do
  SERVICE_JSON="$(gcloud run services describe "$service" \
    --region="$REGION" --format=json)"
  python3 - "$service" "$SERVICE_JSON" <<'PY'
import json
import sys

service_name = sys.argv[1]
service = json.loads(sys.argv[2])
containers = service.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
env = {
    item.get("name"): item.get("value")
    for item in (containers[0].get("env", []) if containers else [])
}
checks = {
    "postgres": env.get("PERSISTENCE_BACKEND") == "postgres",
    "interval": env.get("REFRESH_INTERVAL_MINUTES") == "720",
    "cron": env.get("REFRESH_CRON") == "0 10,20 * * *",
    "max_instances": (
        service.get("spec", {}).get("template", {}).get("scaling", {}).get("maxInstanceCount") == 1
        or service.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {}).get("autoscaling.knative.dev/maxScale") == "1"
    ),
}
if not all(checks.values()):
    raise SystemExit(f"{service_name} configuration verification failed: {checks}")
PY
done

QUEUE_JSON="$(gcloud tasks queues describe "$QUEUE" \
  --location="$TASKS_LOCATION" --format=json)"
python3 - "$QUEUE_JSON" <<'PY'
import json
import sys

queue = json.loads(sys.argv[1])
rate = queue.get("rateLimits", {})
retry = queue.get("retryConfig", {})
checks = {
    "paused": queue.get("state") == "PAUSED",
    "concurrency": rate.get("maxConcurrentDispatches") == 1,
    "rate": float(rate.get("maxDispatchesPerSecond") or 0) == 1.0,
    "attempts": retry.get("maxAttempts") == 3,
}
if not all(checks.values()):
    raise SystemExit(f"Queue verification failed: {checks}")
PY

SCHEDULER_JSON="$(gcloud scheduler jobs describe "$SCHEDULER_JOB" \
  --location="$REGION" --format=json)"
python3 - "$SCHEDULER_JSON" <<'PY'
import json
import sys

job = json.loads(sys.argv[1])
checks = {
    "paused": job.get("state") == "PAUSED",
    "schedule": job.get("schedule") == "0 10,20 * * *",
    "timezone": job.get("timeZone") == "Africa/Cairo",
    "refresh_route": str(job.get("httpTarget", {}).get("uri") or "").endswith(
        "/internal/scheduler/refresh"
    ),
}
if not all(checks.values()):
    raise SystemExit(f"Scheduler verification failed: {checks}")
PY

STATUS_RESPONSE="$(curl --fail-with-body --silent --show-error \
  --max-time 120 "${API_URL}/api/v1/status")"
python3 - "$STATUS_RESPONSE" <<'PY'
import json
import sys

status = json.loads(sys.argv[1])
checks = {
    "active_mappings": status.get("active_mappings") == 2332,
    "interval": status.get("refresh_interval_minutes") == 720,
    "timezone": status.get("scheduler_timezone") == "Africa/Cairo",
}
if not all(checks.values()):
    raise SystemExit(f"API verification failed: {checks}")
PY

if gcloud scheduler jobs describe "$LEGACY_SCHEDULER_JOB" \
  --location="$REGION" >/dev/null 2>&1; then
  LEGACY_STATE="$(gcloud scheduler jobs describe "$LEGACY_SCHEDULER_JOB" \
    --location="$REGION" --format='value(state)')"
  if [[ "$LEGACY_STATE" != "PAUSED" ]]; then
    gcloud scheduler jobs pause "$LEGACY_SCHEDULER_JOB" --location="$REGION" >/dev/null
  fi
fi

gcloud tasks queues resume "$QUEUE" --location="$TASKS_LOCATION"
gcloud scheduler jobs resume "$SCHEDULER_JOB" --location="$REGION"
gcloud scheduler jobs run "$SCHEDULER_JOB" --location="$REGION"

echo "PostgreSQL price collection enabled at 10:00 and 20:00 Cairo."
echo "Immediate run: requested"
echo "API URL: ${API_URL}"
echo "Status:  ${API_URL}/api/v1/status"
