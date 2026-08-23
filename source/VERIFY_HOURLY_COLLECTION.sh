#!/usr/bin/env bash
echo "This hourly verifier is obsolete in v0.5.1. Use VERIFY_CATALOG_DISCOVERY.sh." >&2
exit 2
set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
API_SERVICE="${API_SERVICE:-sa3arly-api}"
WORKER_SERVICE="${WORKER_SERVICE:-sa3arly-worker}"
QUEUE="${QUEUE:-sa3arly-scrape}"
SCHEDULER_JOB="${SCHEDULER_JOB:-sa3arly-hourly-refresh}"
MIN_SCHEDULER_SUCCESSES="${MIN_SCHEDULER_SUCCESSES:-1}"

gcloud config set project "$PROJECT_ID" >/dev/null
API_URL="$(gcloud run services describe "$API_SERVICE" --region="$REGION" \
  --project="$PROJECT_ID" --format='value(status.url)')"

READY="$(curl --fail-with-body --silent --show-error --max-time 120 "${API_URL}/readyz")"
STATUS="$(curl --fail-with-body --silent --show-error --max-time 120 "${API_URL}/api/v1/status")"
QUEUE_JSON="$(gcloud tasks queues describe "$QUEUE" --location="$TASKS_LOCATION" \
  --project="$PROJECT_ID" --format=json)"
SCHEDULER_JSON="$(gcloud scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" \
  --project="$PROJECT_ID" --format=json)"
API_JSON="$(gcloud run services describe "$API_SERVICE" --region="$REGION" \
  --project="$PROJECT_ID" --format=json)"
WORKER_JSON="$(gcloud run services describe "$WORKER_SERVICE" --region="$REGION" \
  --project="$PROJECT_ID" --format=json)"
TASK_COUNT="$(gcloud tasks list --queue="$QUEUE" --location="$TASKS_LOCATION" \
  --project="$PROJECT_ID" --limit=10000 --format='value(name)' | awk 'NF {n++} END {print n+0}')"
SCHEDULER_LOG_JSON="$(gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="sa3arly-worker" AND httpRequest.requestUrl:"/internal/scheduler/refresh"' \
  --project="$PROJECT_ID" --freshness=3h --limit=20 --format=json)"

python3 - "$READY" "$STATUS" "$QUEUE_JSON" "$SCHEDULER_JSON" \
  "$API_JSON" "$WORKER_JSON" "$TASK_COUNT" "$SCHEDULER_LOG_JSON" \
  "$MIN_SCHEDULER_SUCCESSES" <<'PY'
import json, sys
from datetime import UTC, datetime

ready, status, queue, scheduler, api, worker = map(json.loads, sys.argv[1:7])
task_count = int(sys.argv[7])
scheduler_logs = json.loads(sys.argv[8])
minimum_successes = int(sys.argv[9])

def env(service):
    containers = service.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    return {item.get("name"): item.get("value") for item in containers[0].get("env", [])}

api_env = env(api)
worker_env = env(worker)
target = scheduler.get("httpTarget", {})

def parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)

now = datetime.now(UTC)
cash_time = parse_time(status.get("latest_cash_update"))
installment_time = parse_time(status.get("latest_installment_update"))
cash_age_minutes = (now - cash_time).total_seconds() / 60 if cash_time else None
installment_age_minutes = (
    (now - installment_time).total_seconds() / 60 if installment_time else None
)
scheduler_successes = []
for entry in scheduler_logs:
    request = entry.get("httpRequest") or {}
    status_code = int(request.get("status") or 0)
    timestamp = parse_time(entry.get("timestamp"))
    if 200 <= status_code < 300 and timestamp:
        scheduler_successes.append(timestamp)
successful_hour_slots = {item.replace(minute=0, second=0, microsecond=0) for item in scheduler_successes}

checks = {
    "database_ready": ready.get("ok") is True,
    "products_present": int(status.get("products") or 0) > 0,
    "mappings_present": int(status.get("active_mappings") or 0) > 0,
    "hourly_status": status.get("refresh_interval_minutes") == 60,
    "cash_fresh_within_150_minutes": cash_age_minutes is not None and cash_age_minutes <= 150,
    "installments_fresh_within_150_minutes": (
        installment_age_minutes is not None and installment_age_minutes <= 150
    ),
    "api_hourly_env": api_env.get("REFRESH_INTERVAL_MINUTES") == "60",
    "worker_hourly_env": worker_env.get("REFRESH_INTERVAL_MINUTES") == "60",
    "queue_running": queue.get("state") == "RUNNING",
    "scheduler_enabled": scheduler.get("state") == "ENABLED",
    "scheduler_hourly": scheduler.get("schedule") == "0 * * * *",
    "scheduler_timezone": scheduler.get("timeZone") == "Africa/Cairo",
    "refresh_route": str(target.get("uri") or "").endswith("/internal/scheduler/refresh"),
    "scheduler_success_count": len(scheduler_successes) >= minimum_successes,
    "scheduler_distinct_hour_slots": len(successful_hour_slots) >= minimum_successes,
}
print("HOURLY_VERIFICATION=" + ("PASS" if all(checks.values()) else "FAIL"))
print("CHECKS=" + json.dumps(checks, ensure_ascii=False, separators=(",", ":")))
print("PENDING_TASKS=" + str(task_count))
print("SCHEDULER_SUCCESS_COUNT=" + str(len(scheduler_successes)))
print("SCHEDULER_DISTINCT_HOUR_SLOTS=" + str(len(successful_hour_slots)))
print("CASH_AGE_MINUTES=" + (str(round(cash_age_minutes, 2)) if cash_age_minutes is not None else "null"))
print("INSTALLMENT_AGE_MINUTES=" + (str(round(installment_age_minutes, 2)) if installment_age_minutes is not None else "null"))
print("STATUS=" + json.dumps(status, ensure_ascii=False, separators=(",", ":")))
if not all(checks.values()):
    raise SystemExit(1)
PY

gcloud scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" \
  --project="$PROJECT_ID" \
  --format='yaml(name,state,schedule,timeZone,lastAttemptTime,status,httpTarget.uri,httpTarget.oidcToken.serviceAccountEmail,httpTarget.oidcToken.audience)'

echo "Recent scheduler requests to the worker:"
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="sa3arly-worker" AND httpRequest.requestUrl:"/internal/scheduler/refresh"' \
  --project="$PROJECT_ID" --freshness=3h --limit=10 \
  --format='table(timestamp,httpRequest.status,httpRequest.latency,resource.labels.revision_name)'

echo "Recent worker task outcomes:"
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="sa3arly-worker" AND httpRequest.requestUrl:"/internal/tasks/scrape"' \
  --project="$PROJECT_ID" --freshness=3h --limit=30 \
  --format='table(timestamp,httpRequest.status,httpRequest.latency,resource.labels.revision_name)'
