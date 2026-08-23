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
  FAILED_TASK_ID="2026073121-EG-013-5fc2d69612efec36"
  API_EXPECTED="sa3arly-api-runpage-100305"
  WORKER_EXPECTED="sa3arly-worker-q11iguard-130322"
  EXPECTED_CLOUD_TASKS="1130"
  EXPECTED_COMPLETED="20"
  EXPECTED_SUCCESSFUL="14"
  EXPECTED_FAILED="6"
  EXPECTED_CASH_UPDATES="14"
  INTERNAL_TOKEN="change-this-local-token"
  TAG="parsedcachev2"
  REV_SUFFIX="cachev2-$(date -u +%H%M%S)"
  RELEASE_ID="cachev2-$(date -u +%Y%m%d-%H%M%S)"
  WORK_DIR="$(mktemp -d)"

  cleanup() {
    rm -rf -- "$WORK_DIR"
  }

  trap cleanup EXIT

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
    local offset="$1"
    local output_file="$2"
    curl \
      --fail-with-body \
      --silent \
      --show-error \
      --connect-timeout 10 \
      --max-time 120 \
      --header "X-Internal-Token: ${INTERNAL_TOKEN}" \
      --output "$output_file" \
      "${API_URL}/internal/runs/${RUN_ID}?limit=500&offset=${offset}"
  }

  fetch_full_run() {
    fetch_run_page 0 "$WORK_DIR/run-page-0.json"
    fetch_run_page 500 "$WORK_DIR/run-page-500.json"
    fetch_run_page 1000 "$WORK_DIR/run-page-1000.json"

    python3 - \
      "$WORK_DIR/run-page-0.json" \
      "$WORK_DIR/run-page-500.json" \
      "$WORK_DIR/run-page-1000.json" <<'PY'
import json
import sys

pages = []
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as handle:
        pages.append(json.load(handle))

expected_pages = [
    (0, 500, True),
    (500, 500, True),
    (1000, 150, False),
]
for page, (offset, returned, has_more) in zip(pages, expected_pages, strict=True):
    expected = {
        "limit": 500,
        "offset": offset,
        "returned_task_rows": returned,
        "total_task_rows": 1150,
        "has_more": has_more,
    }
    if page.get("pagination") != expected:
        raise SystemExit(
            f"Unexpected run pagination metadata: "
            f"{page.get('pagination')!r} != {expected!r}"
        )

run = pages[0].get("run", {})
tasks = [item for page in pages for item in page.get("tasks", [])]
task_ids = [item.get("external_task_id") for item in tasks]
if len(tasks) != 1150 or not all(task_ids) or len(set(task_ids)) != 1150:
    raise SystemExit("Paginated task rows are missing or duplicated")
for page in pages[1:]:
    if page.get("run") != run:
        raise SystemExit("Run counters changed between pagination pages")

print(json.dumps({"run": run, "tasks": tasks}, ensure_ascii=False, separators=(",", ":")))
PY
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

  echo "=== PARSED PAGE-CACHE VERSION GUARD PRECHECK ==="
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

  RUN_BEFORE="$(fetch_full_run)"

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
    "queued_task_count": 1150,
    "completed_task_count": 20,
    "successful_task_count": 14,
    "failed_task_count": 6,
    "cash_updates": 14,
    "installment_updates": 0,
}
for key, value in expected.items():
    if run.get(key) != value:
        raise SystemExit(f"Unexpected pre-deploy {key}: {run.get(key)!r} != {value!r}")

if len(data.get("tasks", [])) != 1150:
    raise SystemExit(f"Expected 1150 task rows; found {len(data.get('tasks', []))}")

failed_task_id = sys.argv[1]
failed_task = next(
    (
        item for item in data.get("tasks", [])
        if item.get("external_task_id") == failed_task_id
    ),
    None,
)
if failed_task is None:
    raise SystemExit("The failed B.TECH Q11i row was not returned")
if failed_task.get("status") != "failed":
    raise SystemExit(f"Unexpected task status: {failed_task.get('status')!r}")
if failed_task.get("error_code") != "product_match_failed":
    raise SystemExit(f"Unexpected task error: {failed_task.get('error_code')!r}")
if failed_task.get("source_url") != "https://btech.com/en/p/anker-soundcore-q11i-wireless-headphones-black-a3005":
    raise SystemExit(f"Unexpected task URL: {failed_task.get('source_url')!r}")

print("POSTGRES_FAILED_Q11I_TASK_PRECHECK=PASS")
' "$FAILED_TASK_ID" <<<"$RUN_BEFORE"

  echo
  echo "=== PACKAGE VERIFICATION ==="
  sha256sum -c MANIFEST_SHA256.txt >/dev/null
  python3 -m compileall -q app scripts tests
  grep -F 'version = "0.4.9"' pyproject.toml >/dev/null
  grep -F 'No usable price in HTTP document; retrying with browser' \
    app/scraping/engine.py >/dev/null
  grep -F 'html_visible_direct' app/scraping/document.py >/dev/null
  grep -F '_same_offer_evidence' app/scraping/matching.py >/dev/null
  grep -F 'if target.store_sku and candidate.sku:' \
    app/scraping/matching.py >/dev/null
  grep -F '_structured_page_price_exists' \
    app/scraping/document.py >/dev/null
  grep -F 'r"(?<!\w)([0-9٠-٩۰-۹]' \
    app/scraping/document.py >/dev/null
  grep -F 'first.price is not None' \
    app/scraping/matching.py >/dev/null
  grep -F 'test_same_page_meta_without_price_does_not_hide_structured_offer' \
    tests/test_ranking_contract.py >/dev/null
  grep -F 'test_dynamic_direct_page_does_not_extract_price_from_alphanumeric_sku' \
    tests/test_document.py >/dev/null
  grep -F 'test_q11i_structured_price_suppresses_sku_derived_visible_false_price' \
    tests/test_document.py >/dev/null
  grep -F 'test_structured_price_suppresses_visible_fallback_when_page_titles_differ' \
    tests/test_document.py >/dev/null
  grep -F 'PARSED_PAGE_CACHE_SCHEMA_VERSION = 2' \
    app/scraping/engine.py >/dev/null
  grep -F '"schema_version": PARSED_PAGE_CACHE_SCHEMA_VERSION' \
    app/scraping/engine.py >/dev/null
  grep -F 'test_http_304_with_pre_q11i_cache_forces_full_reparse' \
    tests/test_dynamic_browser_fallback.py >/dev/null
  echo "PACKAGE_VERIFICATION=PASS"

  CURRENT_IMAGE="$(gcloud run revisions describe "$WORKER_OLD" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format='value(spec.containers[0].image)')"
  IMAGE_REPOSITORY="${CURRENT_IMAGE%@*}"
  IMAGE_REPOSITORY="${IMAGE_REPOSITORY%:*}"
  WORKER_IMAGE="${IMAGE_REPOSITORY}:${RELEASE_ID}"

  echo
  echo "=== BUILDING PARSED-CACHE-VERSION-GUARDED WORKER IMAGE ==="
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
  echo "=== SWITCHING TO PARSED-CACHE-VERSION-GUARDED WORKER ==="

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

  RUN_AFTER="$(fetch_full_run)"

  python3 -c '
import json
import sys
run = json.load(sys.stdin).get("run", {})
expected = {
    "completed_task_count": 20,
    "successful_task_count": 14,
    "failed_task_count": 6,
    "cash_updates": 14,
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
  echo "COMPLETED_TASK_COUNT=${EXPECTED_COMPLETED}"
  echo "SUCCESSFUL_TASK_COUNT=${EXPECTED_SUCCESSFUL}"
  echo "FAILED_TASK_COUNT=${EXPECTED_FAILED}"
  echo "CASH_UPDATES=${EXPECTED_CASH_UPDATES}"
  echo "CLOUD_TASK_COUNT=${FINAL_TASKS}"
  echo "QUEUE=${FINAL_QUEUE}"
  echo "SCHEDULER_12H=${FINAL_SCHEDULER_12H}"
  echo "SCHEDULER_HOURLY=${FINAL_SCHEDULER_HOURLY}"
  echo "PARSED_CACHE_VERSION_GUARD_DEPLOYED=SUCCESS_NO_TASK_EXECUTED"
)

STEP_RC=$?

echo
echo "PARSED_CACHE_VERSION_GUARD_DEPLOY_SCRIPT_EXIT=${STEP_RC}"
exit "$STEP_RC"
