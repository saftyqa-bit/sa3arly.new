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
  API_EXPECTED="sa3arly-api-00005-fox"
  WORKER_EXPECTED="sa3arly-worker-pricefix-092040"
  EXPECTED_CLOUD_TASKS=1139
  EXPECTED_COMPLETED=10
  EXPECTED_SUCCESSFUL=6
  EXPECTED_FAILED=4
  EXPECTED_CASH_UPDATES=6
  INTERNAL_TOKEN="change-this-local-token"
  TAG="runpagination"
  REV_SUFFIX="runpage-$(date -u +%H%M%S)"
  RELEASE_ID="runpagination-$(date -u +%Y%m%d-%H%M%S)"

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

  fetch_run_page() {
    local base_url="$1"
    local offset="$2"
    local output_file="$3"
    curl \
      --fail-with-body \
      --silent \
      --show-error \
      --max-time 120 \
      --header "X-Internal-Token: ${INTERNAL_TOKEN}" \
      --output "$output_file" \
      "${base_url}/internal/runs/${RUN_ID}?limit=500&offset=${offset}"
  }

  assert_run_counters() {
    local input_file="$1"
    python3 - "$input_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    run = json.load(handle).get("run", {})

expected = {
    "run_id": "ca67a2bc-e7c1-4249-8242-7d374eee69ed",
    "status": "running",
    "mapping_count": 2332,
    "url_group_count": 1149,
    "queued_task_count": 1149,
    "completed_task_count": 10,
    "successful_task_count": 6,
    "failed_task_count": 4,
    "cash_updates": 6,
    "installment_updates": 0,
}
for key, value in expected.items():
    if run.get(key) != value:
        raise SystemExit(f"Unexpected {key}: {run.get(key)!r} != {value!r}")
PY
  }

  API_OLD=""
  CANDIDATE=""
  SWITCHED="NO"

  keep_safe_on_error() {
    local rc=$?
    trap - ERR
    set +e

    gcloud tasks queues pause "$QUEUE" \
      --project="$PROJECT_ID" \
      --location="$REGION" >/dev/null 2>&1

    if [[ -n "$API_OLD" ]]
    then
      gcloud run services update-traffic sa3arly-api \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --clear-tags \
        --to-revisions="${API_OLD}=100" \
        --quiet >/dev/null 2>&1
    fi

    echo
    echo "=== SAFE ABORT ==="
    echo "API_ROLLBACK=${API_OLD:-NOT_NEEDED}"
    echo "CANDIDATE=${CANDIDATE:-NOT_CREATED}"
    echo "TRAFFIC_HAD_SWITCHED=${SWITCHED}"
    echo "QUEUE_REPAUSED=YES"
    echo "NO_TASK_EXECUTED=YES"
    echo "NO_SCHEDULER_CREATED=YES"
    exit "$rc"
  }

  trap keep_safe_on_error ERR
  gcloud config set project "$PROJECT_ID" >/dev/null

  API_OLD="$(active_revision sa3arly-api)"
  WORKER_BEFORE="$(active_revision sa3arly-worker)"
  QUEUE_BEFORE="$(queue_state)"
  SCHEDULER_12H_BEFORE="$(scheduler_state sa3arly-12h-refresh)"
  SCHEDULER_HOURLY_BEFORE="$(scheduler_state sa3arly-hourly-refresh)"
  TASKS_BEFORE="$(cloud_task_count)"

  echo "=== RUN PAGINATION API-ONLY PRECHECK ==="
  echo "API_ACTIVE=${API_OLD}"
  echo "WORKER_ACTIVE=${WORKER_BEFORE}"
  echo "QUEUE=${QUEUE_BEFORE}"
  echo "SCHEDULER_12H=${SCHEDULER_12H_BEFORE}"
  echo "SCHEDULER_HOURLY=${SCHEDULER_HOURLY_BEFORE}"
  echo "CLOUD_TASK_COUNT=${TASKS_BEFORE}"

  [[ "$API_OLD" == "$API_EXPECTED" ]]
  [[ "$WORKER_BEFORE" == "$WORKER_EXPECTED" ]]
  [[ "$QUEUE_BEFORE" == "PAUSED" ]]
  [[ "$SCHEDULER_12H_BEFORE" == "ABSENT" ]]
  [[ "$SCHEDULER_HOURLY_BEFORE" == "ABSENT" ]]
  [[ "$TASKS_BEFORE" -eq "$EXPECTED_CLOUD_TASKS" ]]

  API_URL="$(gcloud run services describe sa3arly-api \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format='value(status.url)')"

  WORK_DIR="$(mktemp -d)"
  curl \
    --fail-with-body \
    --silent \
    --show-error \
    --max-time 120 \
    --header "X-Internal-Token: ${INTERNAL_TOKEN}" \
    --output "$WORK_DIR/run-before.json" \
    "${API_URL}/internal/runs/${RUN_ID}"
  assert_run_counters "$WORK_DIR/run-before.json"

  python3 - "$WORK_DIR/run-before.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
tasks = data.get("tasks", [])
if len(tasks) != 500:
    raise SystemExit(f"Expected legacy endpoint to return 500 tasks; found {len(tasks)}")
print("LEGACY_FIRST_PAGE_ROWS=500")
PY

  echo
  echo "=== PACKAGE VERIFICATION ==="
  sha256sum -c MANIFEST_SHA256.txt >/dev/null
  python3 -m compileall -q app scripts tests
  grep -F 'version = "0.4.6"' pyproject.toml >/dev/null
  grep -F 'task_limit: int = 500' app/repository.py >/dev/null
  grep -F 'LIMIT %s OFFSET %s' app/repository.py >/dev/null
  grep -F 'ORDER BY scheduled_for, store_id, external_task_id' \
    app/repository.py >/dev/null
  grep -F 'limit: Annotated[int, Query(ge=1, le=500)] = 500' \
    app/routes_internal.py >/dev/null
  echo "PACKAGE_VERIFICATION=PASS"

  CURRENT_IMAGE="$(gcloud run revisions describe "$API_OLD" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format='value(spec.containers[0].image)')"
  IMAGE_REPOSITORY="${CURRENT_IMAGE%@*}"
  IMAGE_REPOSITORY="${IMAGE_REPOSITORY%:*}"
  API_IMAGE="${IMAGE_REPOSITORY}:${RELEASE_ID}"

  echo
  echo "=== BUILDING RUN-PAGINATED API IMAGE ==="
  echo "API_IMAGE=${API_IMAGE}"

  gcloud builds submit \
    --project="$PROJECT_ID" \
    --config=infra/gcp/cloudbuild-api.yaml \
    --substitutions="_IMAGE=${API_IMAGE}" \
    --quiet \
    .

  echo
  echo "=== CREATING ZERO-TRAFFIC API CANDIDATE ==="

  gcloud run services update sa3arly-api \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --image="$API_IMAGE" \
    --revision-suffix="$REV_SUFFIX" \
    --no-traffic \
    --tag="$TAG" \
    --quiet

  CANDIDATE="$(gcloud run services describe sa3arly-api \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format='value(status.latestCreatedRevisionName)')"

  [[ -n "$CANDIDATE" ]]
  [[ "$CANDIDATE" != "$API_OLD" ]]

  SERVICE_JSON="$(gcloud run services describe sa3arly-api \
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

  gcloud run revisions describe "$API_OLD" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format=json >"$WORK_DIR/old-revision.json"
  gcloud run revisions describe "$CANDIDATE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format=json >"$WORK_DIR/candidate-revision.json"

  python3 - \
    "$WORK_DIR/old-revision.json" \
    "$WORK_DIR/candidate-revision.json" <<'PY'
import copy
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    old = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    candidate = json.load(handle)

old_spec = copy.deepcopy(old.get("spec", {}))
candidate_spec = copy.deepcopy(candidate.get("spec", {}))
old_image = old_spec["containers"][0].pop("image", None)
candidate_image = candidate_spec["containers"][0].pop("image", None)
if old_spec != candidate_spec:
    raise SystemExit("Candidate API runtime configuration differs from active API")
if not old_image or not candidate_image:
    raise SystemExit("Missing API image in revision specification")

env = {
    item.get("name"): item.get("value", "")
    for item in candidate["spec"]["containers"][0].get("env", [])
}
expected = {
    "PERSISTENCE_BACKEND": "postgres",
    "SERVICE_MODE": "api",
}
for key, value in expected.items():
    if env.get(key) != value:
        raise SystemExit(f"Unexpected {key}: {env.get(key)!r} != {value!r}")

print("CANDIDATE_CONFIG=IDENTICAL_EXCEPT_IMAGE")
print("CANDIDATE_IMAGE=" + candidate_image)
PY

  echo
  echo "=== ZERO-TRAFFIC PAGINATION PROBES ==="

  curl \
    --fail-with-body \
    --silent \
    --show-error \
    --max-time 120 \
    --output "$WORK_DIR/health.json" \
    "${TAG_URL}/healthz"

  python3 - "$WORK_DIR/health.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    health = json.load(handle)
if health != {"ok": True, "service_mode": "api", "version": "0.4.6"}:
    raise SystemExit(f"Unexpected candidate health response: {health}")
print("CANDIDATE_HEALTH=PASS_VERSION_0.4.6")
PY

  fetch_run_page "$TAG_URL" 0 "$WORK_DIR/page-0.json"
  fetch_run_page "$TAG_URL" 500 "$WORK_DIR/page-500.json"
  fetch_run_page "$TAG_URL" 1000 "$WORK_DIR/page-1000.json"

  python3 - \
    "$WORK_DIR/page-0.json" \
    "$WORK_DIR/page-500.json" \
    "$WORK_DIR/page-1000.json" <<'PY'
import collections
import json
import sys

pages = []
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as handle:
        pages.append(json.load(handle))

expected_pages = [
    {
        "limit": 500,
        "offset": 0,
        "returned_task_rows": 500,
        "total_task_rows": 1149,
        "has_more": True,
    },
    {
        "limit": 500,
        "offset": 500,
        "returned_task_rows": 500,
        "total_task_rows": 1149,
        "has_more": True,
    },
    {
        "limit": 500,
        "offset": 1000,
        "returned_task_rows": 149,
        "total_task_rows": 1149,
        "has_more": False,
    },
]
for page, expected in zip(pages, expected_pages, strict=True):
    if page.get("pagination") != expected:
        raise SystemExit(
            f"Unexpected pagination metadata: {page.get('pagination')} != {expected}"
        )

tasks = [item for page in pages for item in page.get("tasks", [])]
task_ids = [item.get("external_task_id") for item in tasks]
if len(tasks) != 1149:
    raise SystemExit(f"Expected 1149 task rows; found {len(tasks)}")
if not all(task_ids) or len(set(task_ids)) != 1149:
    raise SystemExit("Paginated task IDs are missing or duplicated")

status_counts = collections.Counter(item.get("status") for item in tasks)
expected_status_counts = {"queued": 1139, "failed": 4, "success": 6}
if dict(status_counts) != expected_status_counts:
    raise SystemExit(
        f"Unexpected all-page status counts: {dict(status_counts)} != {expected_status_counts}"
    )

blocked_urls = {
    "https://btech.com/en/p/04816292-7658-4159-845f-0af6740d57cd",
    "https://btech.com/en/p/02080b6c-d6f1-4208-8ab9-ed4bfcc91df0",
    "https://btech.com/en/p/04a596d4-2846-42e9-a693-32fc5f32d631",
    "https://btech.com/en/p/0c42d0b7-7b78-4b57-b43c-8328769d1649",
}
eligible = [
    item
    for item in tasks
    if (
        item.get("status") == "queued"
        and item.get("external_task_id")
        and item.get("store_id") == "EG-013"
        and "btech.com" in str(item.get("source_url") or "").lower()
        and "/p/" in str(item.get("source_url") or "").lower()
        and str(item.get("source_url") or "").rstrip("/") not in blocked_urls
    )
]
if len(eligible) < 25:
    raise SystemExit(
        f"Expected at least 25 queued direct B.TECH tasks across all pages; found {len(eligible)}"
    )

print("PAGINATION_PAGE_ROWS=500,500,149")
print("PAGINATION_UNIQUE_TASK_ROWS=1149")
print("ALL_PAGE_STATUS_COUNTS=" + json.dumps(dict(status_counts), sort_keys=True))
print(f"ELIGIBLE_QUEUED_DIRECT_BTECH_TASKS={len(eligible)}")
PY

  for page in "$WORK_DIR/page-0.json" "$WORK_DIR/page-500.json" "$WORK_DIR/page-1000.json"
  do
    assert_run_counters "$page"
  done

  [[ "$(active_revision sa3arly-api)" == "$API_OLD" ]]
  [[ "$(active_revision sa3arly-worker)" == "$WORKER_EXPECTED" ]]
  [[ "$(queue_state)" == "PAUSED" ]]
  [[ "$(scheduler_state sa3arly-12h-refresh)" == "ABSENT" ]]
  [[ "$(scheduler_state sa3arly-hourly-refresh)" == "ABSENT" ]]
  [[ "$(cloud_task_count)" -eq "$EXPECTED_CLOUD_TASKS" ]]

  echo
  echo "=== SWITCHING TO RUN-PAGINATED API ==="

  gcloud run services update-traffic sa3arly-api \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --clear-tags \
    --to-revisions="${CANDIDATE}=100" \
    --quiet
  SWITCHED="YES"

  FINAL_API="$(active_revision sa3arly-api)"
  FINAL_WORKER="$(active_revision sa3arly-worker)"
  FINAL_QUEUE="$(queue_state)"
  FINAL_SCHEDULER_12H="$(scheduler_state sa3arly-12h-refresh)"
  FINAL_SCHEDULER_HOURLY="$(scheduler_state sa3arly-hourly-refresh)"
  FINAL_TASKS="$(cloud_task_count)"

  [[ "$FINAL_API" == "$CANDIDATE" ]]
  [[ "$FINAL_WORKER" == "$WORKER_EXPECTED" ]]
  [[ "$FINAL_QUEUE" == "PAUSED" ]]
  [[ "$FINAL_SCHEDULER_12H" == "ABSENT" ]]
  [[ "$FINAL_SCHEDULER_HOURLY" == "ABSENT" ]]
  [[ "$FINAL_TASKS" -eq "$EXPECTED_CLOUD_TASKS" ]]

  fetch_run_page "$API_URL" 1000 "$WORK_DIR/final-page-1000.json"
  assert_run_counters "$WORK_DIR/final-page-1000.json"
  python3 - "$WORK_DIR/final-page-1000.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
expected = {
    "limit": 500,
    "offset": 1000,
    "returned_task_rows": 149,
    "total_task_rows": 1149,
    "has_more": False,
}
if data.get("pagination") != expected:
    raise SystemExit(f"Unexpected final pagination: {data.get('pagination')}")
print("ACTIVE_API_FINAL_PAGE=PASS")
PY

  trap - ERR

  echo
  echo "=== FINAL SAFE API STATE ==="
  echo "RUN_ID=${RUN_ID}"
  echo "API_PREVIOUS=${API_OLD}"
  echo "API_ACTIVE=${FINAL_API}"
  echo "WORKER_ACTIVE=${FINAL_WORKER}"
  echo "COMPLETED_TASK_COUNT=${EXPECTED_COMPLETED}"
  echo "SUCCESSFUL_TASK_COUNT=${EXPECTED_SUCCESSFUL}"
  echo "FAILED_TASK_COUNT=${EXPECTED_FAILED}"
  echo "CASH_UPDATES=${EXPECTED_CASH_UPDATES}"
  echo "CLOUD_TASK_COUNT=${FINAL_TASKS}"
  echo "QUEUE=${FINAL_QUEUE}"
  echo "SCHEDULER_12H=${FINAL_SCHEDULER_12H}"
  echo "SCHEDULER_HOURLY=${FINAL_SCHEDULER_HOURLY}"
  echo "RUN_PAGINATION_API_DEPLOYED=SUCCESS_NO_TASK_EXECUTED"
)

STEP_RC=$?

echo
echo "RUN_PAGINATION_API_DEPLOY_SCRIPT_EXIT=${STEP_RC}"
