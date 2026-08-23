#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
PRICE_SCHEDULER_JOB="${PRICE_SCHEDULER_JOB:-sa3arly-hourly-refresh}"
CATALOG_SCHEDULER_JOB="${CATALOG_SCHEDULER_JOB:-sa3arly-catalog-discovery}"
CATALOG_QUEUE="${CATALOG_QUEUE:-sa3arly-catalog-discovery}"

test -s catalog-canary-run.json || {
  echo "catalog-canary-run.json is missing; run DEPLOY_CATALOG_DISCOVERY.sh first." >&2
  exit 2
}
RUN_ID="$(python3 -c 'import json; print(json.load(open("catalog-canary-run.json"))["run_id"])')"
PRICE_URI="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(httpTarget.uri)')"
WORKER_URL="${PRICE_URI%/internal/scheduler/refresh}"
PENDING="$(gcloud tasks list --queue="$CATALOG_QUEUE" \
  --location="$TASKS_LOCATION" --project="$PROJECT_ID" \
  --format='value(name)' | wc -l | tr -d ' ')"
if [[ "$PENDING" != "0" ]]; then
  echo "CATALOG_CANARY_PENDING_TASKS=${PENDING}"
  echo "Run this verifier again after the queue drains; no changes were made." >&2
  exit 3
fi

IDENTITY_TOKEN="$(gcloud auth print-identity-token --audiences="$WORKER_URL")"
RUN_JSON="$(curl --fail-with-body --silent --show-error --max-time 120 \
  -H "Authorization: Bearer ${IDENTITY_TOKEN}" \
  "${WORKER_URL}/internal/catalog/runs/${RUN_ID}")"
python3 - "$RUN_JSON" <<'PY'
import json,sys
data=json.loads(sys.argv[1])
run=data["run"]
tasks=data["tasks"]
checks={
    "five_tasks_registered": len(tasks)==5,
    "all_tasks_terminal": all(x.get("status") in {"success","failed"} for x in tasks),
    "at_least_one_public_source": any(x.get("status")=="success" for x in tasks),
    "no_internal_errors": not any(x.get("error_code")=="internal_error" for x in tasks),
    "run_complete": run.get("status") in {"success","failed"},
}
print(json.dumps({"checks":checks,"run":run}, ensure_ascii=False, indent=2))
if not all(checks.values()):
    failed=[
        {
            "store_id":x.get("store_id"),
            "source_url":x.get("source_url"),
            "error_code":x.get("error_code"),
            "error_message":x.get("error_message"),
            "metrics":x.get("metrics"),
        }
        for x in tasks if x.get("status") != "success"
    ]
    print(json.dumps({"failed_tasks":failed}, ensure_ascii=False, indent=2))
    raise SystemExit("CATALOG_CANARY_VERIFICATION=FAIL")
PY

PRICE_SCHEDULE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(schedule)')"
PRICE_STATE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(state)')"
CATALOG_STATE="$(gcloud scheduler jobs describe "$CATALOG_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(state)')"
if [[ "$PRICE_SCHEDULE" != "0 10,20 * * *" || "$PRICE_STATE" != "ENABLED" ]]; then
  echo "PRICE_SCHEDULER_REGRESSION=FAIL" >&2
  exit 2
fi
if [[ "$CATALOG_STATE" != "PAUSED" ]]; then
  echo "Catalog scheduler must remain paused until explicit activation." >&2
  exit 2
fi
printf '%s\n' "$RUN_ID" > .catalog-discovery-verified
echo "CATALOG_CANARY_VERIFICATION=PASS"
echo "PRICE_SCHEDULER=ENABLED_10AM_8PM"
echo "NEXT_STEP=Run ./ACTIVATE_CATALOG_DISCOVERY.sh"
