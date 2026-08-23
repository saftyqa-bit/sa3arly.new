#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
PRICE_SCHEDULER_JOB="${PRICE_SCHEDULER_JOB:-sa3arly-hourly-refresh}"
CATALOG_SCHEDULER_JOB="${CATALOG_SCHEDULER_JOB:-sa3arly-catalog-discovery}"

test -s .catalog-discovery-verified || {
  echo "Run VERIFY_CATALOG_DISCOVERY.sh successfully before activation." >&2
  exit 2
}
PRICE_SCHEDULE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(schedule)')"
PRICE_STATE="$(gcloud scheduler jobs describe "$PRICE_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --format='value(state)')"
if [[ "$PRICE_SCHEDULE" != "0 10,20 * * *" || "$PRICE_STATE" != "ENABLED" ]]; then
  echo "Price scheduler is not in the approved 10:00/20:00 state." >&2
  exit 2
fi
gcloud scheduler jobs resume "$CATALOG_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID"
gcloud scheduler jobs describe "$CATALOG_SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" \
  --format='yaml(state,schedule,timeZone,scheduleTime,httpTarget.uri)'
echo "CATALOG_DISCOVERY=ACTIVE"
echo "CATALOG_BATCH=35_STORES_NIGHTLY"
echo "FULL_FIRST_PASS=ABOUT_6_NIGHTS"
echo "PRICE_REFRESH=UNCHANGED_10AM_8PM"
