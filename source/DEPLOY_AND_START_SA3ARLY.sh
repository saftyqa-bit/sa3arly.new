#!/usr/bin/env bash
set -Eeuo pipefail

: "${COST_CONTROLS_CONFIRMED:?Set COST_CONTROLS_CONFIRMED=1 after configuring the Cloud Run spend cap}"
if [[ "$COST_CONTROLS_CONFIRMED" != "1" ]]; then
  echo "COST_CONTROLS_CONFIRMED must equal 1." >&2
  exit 2
fi

# The deployment wrapper deliberately provisions everything in a paused state.
bash DEPLOY_SA3ARLY_CLOUD_SHELL.sh

# Re-check the cost-sensitive runtime limits before activating collection.
PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
for service in sa3arly-api sa3arly-worker; do
  max_instances="$(
    gcloud run services describe "$service" \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --format=json |
      python3 -c 'import json, sys
value = json.load(sys.stdin)
template = value.get("spec", {}).get("template", {})
annotations = template.get("metadata", {}).get("annotations", {})
scaling = template.get("scaling", {})
print(
    scaling.get("maxInstanceCount")
    or annotations.get("autoscaling.knative.dev/maxScale")
    or ""
)'
  )"
  if [[ "$max_instances" != "1" ]]; then
    echo "${service} max instances is ${max_instances:-unset}; refusing to activate." >&2
    exit 2
  fi
done

COST_CONTROLS_CONFIRMED=1 bash START_SA3ARLY_COLLECTION.sh
