#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
REPOSITORY="${REPOSITORY:-sa3arly}"
API_SERVICE="${API_SERVICE:-sa3arly-api}"
WEB_SERVICE="${WEB_SERVICE:-sa3arly-web}"
WEB_SA_NAME="${WEB_SA_NAME:-sa3arly-web}"
WEB_SA="${WEB_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
INTERNAL_TOKEN_SECRET="${INTERNAL_TOKEN_SECRET:-sa3arly-internal-token}"
SA3ARLY_INTERNAL_TOKEN_ENABLED="${SA3ARLY_INTERNAL_TOKEN_ENABLED:-false}"
SA3ARLY_ADMIN_EMAILS="${SA3ARLY_ADMIN_EMAILS:-}"
RELEASE_ID="${RELEASE_ID:-web-$(date -u +%Y%m%d-%H%M%S)}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/web:${RELEASE_ID}"

command -v gcloud >/dev/null || { echo 'gcloud is required' >&2; exit 69; }
command -v docker >/dev/null || { echo 'docker is required' >&2; exit 69; }
gcloud config set project "$PROJECT_ID" >/dev/null

gcloud iam service-accounts describe "$WEB_SA" --project="$PROJECT_ID" >/dev/null 2>&1 || {
  echo "${WEB_SA} is missing. Run scripts/bootstrap_github_wif.sh first." >&2
  exit 2
}

API_URL="$(gcloud run services describe "$API_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')"
[[ -n "$API_URL" ]] || { echo 'Could not resolve the Sa3arly API URL.' >&2; exit 2; }

echo '[sa3arly-web] configuring Docker authentication for Artifact Registry'
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

echo '[sa3arly-web] building the Next.js standalone image on the GitHub runner'
docker build \
  --file=sites-frontend-v13/Dockerfile \
  --tag="$IMAGE" \
  sites-frontend-v13

echo '[sa3arly-web] pushing the image to the Sa3arly Artifact Registry repository'
docker push "$IMAGE"

deploy_args=(
  run deploy "$WEB_SERVICE"
  --project="$PROJECT_ID"
  --region="$REGION"
  --platform=managed
  --image="$IMAGE"
  --service-account="$WEB_SA"
  --allow-unauthenticated
  --port=8080
  --cpu=1
  --memory=1Gi
  --min=0
  --max=4
  --concurrency=40
  --timeout=60s
  --set-env-vars="^|^NODE_ENV=production|NEXT_TELEMETRY_DISABLED=1|SA3ARLY_API_BASE_URL=${API_URL}|SA3ARLY_ADMIN_EMAILS=${SA3ARLY_ADMIN_EMAILS}"
  --quiet
)

if [[ "$SA3ARLY_INTERNAL_TOKEN_ENABLED" == "true" ]]; then
  deploy_args+=(--set-secrets="SA3ARLY_INTERNAL_TOKEN=${INTERNAL_TOKEN_SECRET}:latest")
else
  echo "[sa3arly-web] Internal admin token is disabled; /admin will remain unavailable."
fi

gcloud "${deploy_args[@]}"
WEB_URL="$(gcloud run services describe "$WEB_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')"

curl --fail-with-body --silent --show-error --max-time 60 "${WEB_URL}/robots.txt" >/dev/null
curl --fail-with-body --silent --show-error --max-time 60 "${WEB_URL}/" >/dev/null

echo 'SA3ARLY_WEB_DEPLOYMENT=SUCCESS'
echo "WEB_IMAGE=${IMAGE}"
echo "WEB_URL=${WEB_URL}"
echo 'DOMAIN_TRAFFIC_CHANGED=NO'
