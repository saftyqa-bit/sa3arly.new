#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="sa3arly-prod-972741"
REGION="europe-west1"
QUEUE="sa3arly-scrape"
RUN_ID="ca67a2bc-e7c1-4249-8242-7d374eee69ed"
RUN_SLOT="2026-07-31T21:00:00Z"
API_EXPECTED="sa3arly-api-00005-fox"
WORKER_EXPECTED="sa3arly-worker-oidcauth-055107"
EXPECTED_TASKS_BEFORE="1141"
EXPECTED_TASKS_AFTER="1149"
EXPECTED_COLLISIONS="8"
TAG="taskidfixpilot"
REV_SUFFIX="taskidfix-$(date -u +%H%M%S)"
RELEASE_ID="taskidfix-$(date -u +%Y%m%d-%H%M%S)"

active_revision() {
  gcloud run services describe "$1" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format=json |
    python3 -c '
import json, sys
service = json.load(sys.stdin)
active = [
    item.get("revisionName")
    for item in service.get("status", {}).get("traffic", [])
    if int(item.get("percent") or 0) == 100
]
if len(active) != 1:
    raise SystemExit(f"Expected one 100% revision; found {active}")
print(active[0])
'
}

scheduler_state() {
  local job_name="$1"
  if gcloud scheduler jobs describe "$job_name" \
    --project="$PROJECT_ID" \
    --location="$REGION" >/dev/null 2>&1
  then
    gcloud scheduler jobs describe "$job_name" \
      --project="$PROJECT_ID" \
      --location="$REGION" \
      --format='value(state)'
  else
    echo "ABSENT"
  fi
}

queue_state() {
  gcloud tasks queues describe "$QUEUE" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --format='value(state)'
}

cloud_task_count() {
  gcloud tasks list \
    --project="$PROJECT_ID" \
    --queue="$QUEUE" \
    --location="$REGION" \
    --format='value(name)' |
    wc -l |
    xargs
}

WORKER_OLD=""
CANDIDATE=""

safe_error() {
  local rc=$?
  trap - ERR
  set +e

  gcloud tasks queues pause "$QUEUE" \
    --project="$PROJECT_ID" \
    --location="$REGION" >/dev/null 2>&1

  if [[ -n "$WORKER_OLD" ]]
  then
    gcloud run services update-traffic sa3arly-worker \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --clear-tags \
      --to-revisions="${WORKER_OLD}=100" \
      --quiet >/dev/null 2>&1
  fi

  echo
  echo "=== SAFE ABORT ==="
  echo "WORKER_ROLLBACK=${WORKER_OLD:-NOT_NEEDED}"
  echo "CANDIDATE=${CANDIDATE:-NOT_CREATED}"
  echo "QUEUE_REPAUSED=YES"
  echo "NO_SCHEDULER_CREATED=YES"
  exit "$rc"
}

trap safe_error ERR

gcloud config set project "$PROJECT_ID" >/dev/null

WORKER_OLD="$(active_revision sa3arly-worker)"
API_ACTIVE="$(active_revision sa3arly-api)"
QUEUE_BEFORE="$(queue_state)"
SCHEDULER_12H_BEFORE="$(scheduler_state sa3arly-12h-refresh)"
SCHEDULER_HOURLY_BEFORE="$(scheduler_state sa3arly-hourly-refresh)"
TASKS_BEFORE="$(cloud_task_count)"

echo "=== TASK-ID FIX PRECHECK ==="
echo "API_ACTIVE=${API_ACTIVE}"
echo "WORKER_ACTIVE=${WORKER_OLD}"
echo "QUEUE=${QUEUE_BEFORE}"
echo "SCHEDULER_12H=${SCHEDULER_12H_BEFORE}"
echo "SCHEDULER_HOURLY=${SCHEDULER_HOURLY_BEFORE}"
echo "CLOUD_TASK_COUNT=${TASKS_BEFORE}"

[[ "$API_ACTIVE" == "$API_EXPECTED" ]]
[[ "$WORKER_OLD" == "$WORKER_EXPECTED" ]]
[[ "$QUEUE_BEFORE" == "PAUSED" ]]
[[ "$SCHEDULER_12H_BEFORE" == "ABSENT" ]]
[[ "$SCHEDULER_HOURLY_BEFORE" == "ABSENT" ]]
[[ "$TASKS_BEFORE" == "$EXPECTED_TASKS_BEFORE" ]]

API_URL="$(gcloud run services describe sa3arly-api \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format='value(status.url)')"

RUN_BEFORE="$(curl \
  --fail-with-body \
  --silent \
  --show-error \
  --max-time 120 \
  --header 'X-Internal-Token: change-this-local-token' \
  "${API_URL}/internal/runs/${RUN_ID}")"

python3 -c '
import json
import sys

data = json.load(sys.stdin)
run = data.get("run", {})
expected = {
    "run_id": "ca67a2bc-e7c1-4249-8242-7d374eee69ed",
    "status": "queued",
    "mapping_count": 2332,
    "url_group_count": 1149,
    "queued_task_count": 1149,
    "completed_task_count": 0,
    "successful_task_count": 0,
    "failed_task_count": 0,
}
for key, value in expected.items():
    if run.get(key) != value:
        raise SystemExit(f"Unexpected pre-repair {key}: {run.get(key)!r} != {value!r}")
print("POSTGRES_RUN_PRECHECK=PASS")
' <<<"$RUN_BEFORE"

echo
echo "=== PACKAGE VERIFICATION ==="
sha256sum -c MANIFEST_SHA256.txt >/dev/null
python3 -m compileall -q app scripts tests
grep -F 'TASK_ID_SCHEME = "legacy-first-full-group-suffix-v2"' app/hourly.py >/dev/null
echo "PACKAGE_VERIFICATION=PASS"

CURRENT_IMAGE="$(gcloud run revisions describe "$WORKER_OLD" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format='value(spec.containers[0].image)')"
IMAGE_REPOSITORY="${CURRENT_IMAGE%@*}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY%:*}"
WORKER_IMAGE="${IMAGE_REPOSITORY}:${RELEASE_ID}"

echo
echo "=== BUILDING FIXED WORKER IMAGE ==="
echo "WORKER_IMAGE=${WORKER_IMAGE}"

gcloud builds submit \
  --project="$PROJECT_ID" \
  --config=infra/gcp/cloudbuild-worker.yaml \
  --substitutions="_IMAGE=${WORKER_IMAGE}" \
  --quiet \
  .

echo
echo "=== CREATING ZERO-TRAFFIC CANDIDATE ==="

gcloud run services update sa3arly-worker \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --image="$WORKER_IMAGE" \
  --revision-suffix="$REV_SUFFIX" \
  --no-traffic \
  --tag="$TAG" \
  --quiet

CANDIDATE="$(gcloud run services describe sa3arly-worker \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format='value(status.latestCreatedRevisionName)')"

[[ -n "$CANDIDATE" ]]
[[ "$CANDIDATE" != "$WORKER_OLD" ]]

SERVICE_JSON="$(gcloud run services describe sa3arly-worker \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format=json)"

TAG_URL="$(python3 -c '
import json, sys
service = json.load(sys.stdin)
tag = sys.argv[1]
urls = [
    item.get("url")
    for item in service.get("status", {}).get("traffic", [])
    if item.get("tag") == tag
]
if len(urls) != 1 or not urls[0]:
    raise SystemExit(f"Expected one URL for tag {tag}; found {urls}")
print(urls[0])
' "$TAG" <<<"$SERVICE_JSON")"

gcloud run revisions describe "$CANDIDATE" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format=json |
  python3 -c '
import json, sys
revision = json.load(sys.stdin)
container = revision["spec"]["containers"][0]
env = {item.get("name"): item.get("value", "") for item in container.get("env", [])}
expected = {
    "PERSISTENCE_BACKEND": "postgres",
    "TASKS_MODE": "cloud",
    "REFRESH_INTERVAL_MINUTES": "720",
}
for key, value in expected.items():
    if env.get(key) != value:
        raise SystemExit(f"Unexpected {key}: {env.get(key)!r} != {value!r}")
if env.get("INTERNAL_TOKEN", "") != "":
    raise SystemExit("INTERNAL_TOKEN is not empty on the OIDC worker")
print("CANDIDATE_CONFIG=PASS")
print("CANDIDATE_IMAGE=" + str(container.get("image")))
'

IDENTITY_TOKEN="$(gcloud auth print-identity-token)"

echo
echo "=== ZERO-TRAFFIC CANDIDATE PROBES ==="

curl \
  --fail-with-body \
  --silent \
  --show-error \
  --max-time 120 \
  --header "Authorization: Bearer ${IDENTITY_TOKEN}" \
  "${TAG_URL}/readyz"
echo

PROBE_RAW="$(curl \
  --silent \
  --show-error \
  --max-time 120 \
  --header "Authorization: Bearer ${IDENTITY_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data-binary '{' \
  --write-out $'\n%{http_code}' \
  "${TAG_URL}/internal/scheduler/refresh" || true)"

PROBE_HTTP="${PROBE_RAW##*$'\n'}"
echo "SAFE_PROBE_HTTP=${PROBE_HTTP}"
[[ "$PROBE_HTTP" == "422" ]]
[[ "$(queue_state)" == "PAUSED" ]]
[[ "$(cloud_task_count)" == "$EXPECTED_TASKS_BEFORE" ]]

echo
echo "=== SWITCHING TO FIXED WORKER ==="

gcloud run services update-traffic sa3arly-worker \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --clear-tags \
  --to-revisions="${CANDIDATE}=100" \
  --quiet

[[ "$(active_revision sa3arly-worker)" == "$CANDIDATE" ]]
[[ "$(queue_state)" == "PAUSED" ]]

WORKER_URL="$(gcloud run services describe sa3arly-worker \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format='value(status.url)')"

echo
echo "=== REPAIRING THE EXISTING PAUSED RUN ==="

REPAIR_RESPONSE="$(curl \
  --fail-with-body \
  --silent \
  --show-error \
  --max-time 1800 \
  --header "Authorization: Bearer ${IDENTITY_TOKEN}" \
  --header 'Content-Type: application/json' \
  --header "X-CloudScheduler-ScheduleTime: ${RUN_SLOT}" \
  --data '{"trigger":"repair-task-name-collision"}' \
  "${WORKER_URL}/internal/scheduler/refresh")"

echo "$REPAIR_RESPONSE"

python3 -c '
import json
import sys

data = json.load(sys.stdin)
expected = {
    "run_id": "ca67a2bc-e7c1-4249-8242-7d374eee69ed",
    "duplicate": False,
    "resumed": True,
    "mapping_count": 2332,
    "unique_url_groups": 1149,
    "task_count": 1149,
    "registered_task_count": 1149,
    "task_id_scheme": "legacy-first-full-group-suffix-v2",
    "task_id_collisions_disambiguated": 8,
    "collision_repair": True,
}
for key, value in expected.items():
    if data.get(key) != value:
        raise SystemExit(f"Unexpected repair {key}: {data.get(key)!r} != {value!r}")
print("REPAIR_RESPONSE_CHECK=PASS")
' <<<"$REPAIR_RESPONSE"

TASKS_AFTER="$(cloud_task_count)"
RUN_AFTER="$(curl \
  --fail-with-body \
  --silent \
  --show-error \
  --max-time 120 \
  --header 'X-Internal-Token: change-this-local-token' \
  "${API_URL}/internal/runs/${RUN_ID}")"

python3 -c '
import json
import sys

run = json.load(sys.stdin).get("run", {})
metadata = run.get("metadata") or {}
expected_run = {
    "status": "queued",
    "mapping_count": 2332,
    "url_group_count": 1149,
    "queued_task_count": 1149,
    "completed_task_count": 0,
}
for key, value in expected_run.items():
    if run.get(key) != value:
        raise SystemExit(f"Unexpected repaired run {key}: {run.get(key)!r} != {value!r}")
expected_metadata = {
    "enqueue_complete": True,
    "task_id_scheme": "legacy-first-full-group-suffix-v2",
    "task_id_collisions_disambiguated": 8,
    "collision_repair_requested": True,
    "refresh_interval_minutes": 720,
}
for key, value in expected_metadata.items():
    if metadata.get(key) != value:
        raise SystemExit(
            f"Unexpected repaired metadata {key}: {metadata.get(key)!r} != {value!r}"
        )
print("POSTGRES_REPAIR_RECORD=PASS")
' <<<"$RUN_AFTER"

FINAL_QUEUE="$(queue_state)"
FINAL_SCHEDULER_12H="$(scheduler_state sa3arly-12h-refresh)"
FINAL_SCHEDULER_HOURLY="$(scheduler_state sa3arly-hourly-refresh)"
WORKER_AFTER="$(active_revision sa3arly-worker)"

[[ "$TASKS_AFTER" == "$EXPECTED_TASKS_AFTER" ]]
[[ "$FINAL_QUEUE" == "PAUSED" ]]
[[ "$FINAL_SCHEDULER_12H" == "ABSENT" ]]
[[ "$FINAL_SCHEDULER_HOURLY" == "ABSENT" ]]
[[ "$WORKER_AFTER" == "$CANDIDATE" ]]

trap - ERR

echo
echo "=== FINAL SAFE REPAIRED STATE ==="
echo "WORKER_ACTIVE=${WORKER_AFTER}"
echo "RUN_ID=${RUN_ID}"
echo "POSTGRES_REGISTERED_TASK_COUNT=${EXPECTED_TASKS_AFTER}"
echo "CLOUD_TASK_COUNT=${TASKS_AFTER}"
echo "QUEUE=${FINAL_QUEUE}"
echo "SCHEDULER_12H=${FINAL_SCHEDULER_12H}"
echo "SCHEDULER_HOURLY=${FINAL_SCHEDULER_HOURLY}"
echo "TASK_NAME_COLLISION_REPAIR=SUCCESS_NO_TASK_EXECUTED"
