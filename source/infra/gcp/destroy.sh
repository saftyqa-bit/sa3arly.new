#!/usr/bin/env bash
set -Eeuo pipefail
: "${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-europe-west1}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"
API_SERVICE="${API_SERVICE:-sa3arly-api}"
WORKER_SERVICE="${WORKER_SERVICE:-sa3arly-worker}"
BOOTSTRAP_JOB="${BOOTSTRAP_JOB:-sa3arly-bootstrap}"
QUEUE="${QUEUE:-sa3arly-scrape}"
SCHEDULER_JOB="${SCHEDULER_JOB:-sa3arly-12h-refresh}"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud scheduler jobs delete "$SCHEDULER_JOB" --location="$REGION" --quiet || true
gcloud tasks queues delete "$QUEUE" --location="$TASKS_LOCATION" --quiet || true
gcloud run jobs delete "$BOOTSTRAP_JOB" --region="$REGION" --quiet || true
gcloud run services delete "$WORKER_SERVICE" --region="$REGION" --quiet || true
gcloud run services delete "$API_SERVICE" --region="$REGION" --quiet || true
echo "Cloud SQL and the database secret were intentionally kept to prevent data loss."
