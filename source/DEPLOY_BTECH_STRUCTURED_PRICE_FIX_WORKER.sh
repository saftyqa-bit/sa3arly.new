#!/usr/bin/env bash
set +e
set +u
set +E
set +o pipefail
trap - ERR

(
  set -Eeuo pipefail

  PROJECT_ID="sa3arly-prod-972741"
  REGION="europe-west1"
  QUEUE="sa3arly-scrape"
  RUN_ID="ca67a2bc-e7c1-4249-8242-7d374eee69ed"
  FAILED_CANARY_TASK_ID="2026073121-EG-013-156efcb570161481"
  API_EXPECTED="sa3arly-api-00005-fox"
  WORKER_EXPECTED="sa3arly-worker-skufix-083935"
  EXPECTED_CLOUD_TASKS="1144"
  INTERNAL_TOKEN="change-this-local-token"
  TAG="btechpricefix"
  REV_SUFFIX="pricefix-$(date -u +%H%M%S)"
  RELEASE_ID="pricefix-$(date -u +%Y%m%d-%H%M%S)"

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

  keep_safe_on_error() {
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
    echo "NO_TASK_EXECUTED=YES"
    echo "NO_SCHEDULER_CREATED=YES"
    exit "$rc"
  }

  trap keep_safe_on_error ERR
  gcloud config set project "$PROJECT_ID" >/dev/null

  WORKER_OLD="$(active_revision sa3arly-worker)"
  API_ACTIVE="$(active_revision sa3arly-api)"
  QUEUE_BEFORE="$(queue_state)"
  SCHEDULER_12H_BEFORE="$(scheduler_state sa3arly-12h-refresh)"
  SCHEDULER_HOURLY_BEFORE="$(scheduler_state sa3arly-hourly-refresh)"
  TASKS_BEFORE="$(cloud_task_count)"

  echo "=== BTECH STRUCTURED PRICE FIX PRECHECK ==="
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
  [[ "$TASKS_BEFORE" == "$EXPECTED_CLOUD_TASKS" ]]

  API_URL="$(gcloud run services describe sa3arly-api \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format='value(status.url)')"

  RUN_BEFORE="$(curl \
    --fail-with-body \
    --silent \
    --show-error \
    --max-time 120 \
    --header "X-Internal-Token: ${INTERNAL_TOKEN}" \
    "${API_URL}/internal/runs/${RUN_ID}")"

  python3 -c '
import json
import sys

data = json.load(sys.stdin)
run = data.get("run", {})
expected = {
    "run_id": "ca67a2bc-e7c1-4249-8242-7d374eee69ed",
    "status": "running",
    "mapping_count": 2332,
    "url_group_count": 1149,
    "queued_task_count": 1149,
    "completed_task_count": 5,
    "successful_task_count": 1,
    "failed_task_count": 4,
    "cash_updates": 1,
    "installment_updates": 0,
}
for key, value in expected.items():
    if run.get(key) != value:
        raise SystemExit(f"Unexpected pre-fix {key}: {run.get(key)!r} != {value!r}")

failed_task_id = sys.argv[1]
failed_task = next(
    (
        item for item in data.get("tasks", [])
        if item.get("external_task_id") == failed_task_id
    ),
    None,
)
if failed_task is None:
    raise SystemExit("The failed B.TECH canary row was not returned")
if failed_task.get("status") != "failed":
    raise SystemExit(f"Unexpected canary task status: {failed_task.get('status')!r}")
if failed_task.get("error_code") != "product_match_failed":
    raise SystemExit(f"Unexpected canary task error: {failed_task.get('error_code')!r}")

print("POSTGRES_FAILED_CANARY_PRECHECK=PASS")
' "$FAILED_CANARY_TASK_ID" <<<"$RUN_BEFORE"

  echo
  echo "=== PACKAGE VERIFICATION ==="
  sha256sum -c MANIFEST_SHA256.txt >/dev/null
  python3 -m compileall -q app scripts tests
  grep -F 'version = "0.4.5"' pyproject.toml >/dev/null
  grep -F 'No usable price in HTTP document; retrying with browser' \
    app/scraping/engine.py >/dev/null
  grep -F 'html_visible_direct' app/scraping/document.py >/dev/null
  grep -F '_same_offer_evidence' app/scraping/matching.py >/dev/null
  grep -F 'if target.store_sku and candidate.sku:' \
    app/scraping/matching.py >/dev/null
  grep -F '_structured_page_price_exists' \
    app/scraping/document.py >/dev/null
  echo "PACKAGE_VERIFICATION=PASS"

  CURRENT_IMAGE="$(gcloud run revisions describe "$WORKER_OLD" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format='value(spec.containers[0].image)')"
  IMAGE_REPOSITORY="${CURRENT_IMAGE%@*}"
  IMAGE_REPOSITORY="${IMAGE_REPOSITORY%:*}"
  WORKER_IMAGE="${IMAGE_REPOSITORY}:${RELEASE_ID}"

  echo
  echo "=== BUILDING BTECH-STRUCTURED-PRICE-FIXED WORKER IMAGE ==="
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
    "${TAG_URL}/internal/tasks/scrape" || true)"

  PROBE_HTTP="${PROBE_RAW##*$'\n'}"
  echo "SAFE_PROBE_HTTP=${PROBE_HTTP}"
  [[ "$PROBE_HTTP" == "422" ]]
  [[ "$(queue_state)" == "PAUSED" ]]
  [[ "$(cloud_task_count)" == "$EXPECTED_CLOUD_TASKS" ]]

  echo
  echo "=== SWITCHING TO BTECH-STRUCTURED-PRICE-FIXED WORKER ==="

  gcloud run services update-traffic sa3arly-worker \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --clear-tags \
    --to-revisions="${CANDIDATE}=100" \
    --quiet

  FINAL_WORKER="$(active_revision sa3arly-worker)"
  FINAL_QUEUE="$(queue_state)"
  FINAL_SCHEDULER_12H="$(scheduler_state sa3arly-12h-refresh)"
  FINAL_SCHEDULER_HOURLY="$(scheduler_state sa3arly-hourly-refresh)"
  FINAL_TASKS="$(cloud_task_count)"

  [[ "$FINAL_WORKER" == "$CANDIDATE" ]]
  [[ "$FINAL_QUEUE" == "PAUSED" ]]
  [[ "$FINAL_SCHEDULER_12H" == "ABSENT" ]]
  [[ "$FINAL_SCHEDULER_HOURLY" == "ABSENT" ]]
  [[ "$FINAL_TASKS" == "$EXPECTED_CLOUD_TASKS" ]]

  RUN_AFTER="$(curl \
    --fail-with-body \
    --silent \
    --show-error \
    --max-time 120 \
    --header "X-Internal-Token: ${INTERNAL_TOKEN}" \
    "${API_URL}/internal/runs/${RUN_ID}")"

  python3 -c '
import json
import sys
run = json.load(sys.stdin).get("run", {})
expected = {
    "completed_task_count": 5,
    "successful_task_count": 1,
    "failed_task_count": 4,
    "cash_updates": 1,
    "installment_updates": 0,
}
for key, value in expected.items():
    if run.get(key) != value:
        raise SystemExit(f"Worker deploy changed {key}: {run.get(key)!r} != {value!r}")
print("POSTGRES_UNCHANGED_AFTER_DEPLOY=PASS")
' <<<"$RUN_AFTER"

  trap - ERR

  echo
  echo "=== FINAL SAFE DEPLOYED STATE ==="
  echo "API_ACTIVE=$(active_revision sa3arly-api)"
  echo "WORKER_PREVIOUS=${WORKER_OLD}"
  echo "WORKER_ACTIVE=${FINAL_WORKER}"
  echo "RUN_ID=${RUN_ID}"
  echo "COMPLETED_TASK_COUNT=5"
  echo "SUCCESSFUL_TASK_COUNT=1"
  echo "FAILED_TASK_COUNT=4"
  echo "CLOUD_TASK_COUNT=${FINAL_TASKS}"
  echo "QUEUE=${FINAL_QUEUE}"
  echo "SCHEDULER_12H=${FINAL_SCHEDULER_12H}"
  echo "SCHEDULER_HOURLY=${FINAL_SCHEDULER_HOURLY}"
  echo "BTECH_STRUCTURED_PRICE_FIX_DEPLOYED=SUCCESS_NO_TASK_EXECUTED"
)

STEP_RC=$?

echo
echo "BTECH_STRUCTURED_PRICE_FIX_DEPLOY_SCRIPT_EXIT=${STEP_RC}"
