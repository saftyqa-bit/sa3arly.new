#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
REGION="${REGION:-europe-west1}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-engmohamedelmorsy-arch/sa3arly}"
POOL_ID="${POOL_ID:-github-actions}"
PROVIDER_ID="${PROVIDER_ID:-sa3arly}"
DEPLOYER_SA_NAME="${DEPLOYER_SA_NAME:-sa3arly-github-deployer}"
DEPLOYER_SA="${DEPLOYER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DB_SECRET="${DB_SECRET:-sa3arly-database-url}"
INTERNAL_TOKEN_SECRET="${INTERNAL_TOKEN_SECRET:-sa3arly-internal-token}"
BOOTSTRAP_SA="${BOOTSTRAP_SA:-sa3arly-bootstrap@${PROJECT_ID}.iam.gserviceaccount.com}"
WEB_RUNTIME_SA="${WEB_RUNTIME_SA:-sa3arly-web@${PROJECT_ID}.iam.gserviceaccount.com}"
CLOUD_BUILD_BUCKET="${CLOUD_BUILD_BUCKET:-${PROJECT_ID}_cloudbuild}"
ARTIFACT_REPOSITORY="${ARTIFACT_REPOSITORY:-sa3arly}"

command -v gcloud >/dev/null || { echo 'gcloud is required' >&2; exit 69; }
gcloud config set project "$PROJECT_ID" >/dev/null

for api in \
  cloudresourcemanager.googleapis.com storage.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudbuild.googleapis.com run.googleapis.com artifactregistry.googleapis.com \
  sqladmin.googleapis.com secretmanager.googleapis.com \
  cloudtasks.googleapis.com cloudscheduler.googleapis.com; do
  gcloud services enable "$api" --project="$PROJECT_ID" >/dev/null
done

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

if ! gcloud iam service-accounts describe "$DEPLOYER_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$DEPLOYER_SA_NAME" \
    --project="$PROJECT_ID" \
    --display-name='Sa3arly GitHub production deployer'
fi

for role in \
  roles/serviceusage.serviceUsageConsumer \
  roles/cloudbuild.builds.editor \
  roles/run.admin \
  roles/run.invoker \
  roles/cloudsql.admin \
  roles/cloudtasks.admin \
  roles/cloudscheduler.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role="$role" --condition=None --quiet >/dev/null
done

# GitHub builds the web image on its own runner and may push only to the
# Sa3arly Artifact Registry repository. This avoids impersonating the broad
# default Cloud Build service account.
if gcloud artifacts repositories describe "$ARTIFACT_REPOSITORY" \
  --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories add-iam-policy-binding "$ARTIFACT_REPOSITORY" \
    --project="$PROJECT_ID" --location="$REGION" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role='roles/artifactregistry.writer' --quiet >/dev/null
else
  echo "Artifact Registry repository ${REGION}/${ARTIFACT_REPOSITORY} is missing." >&2
  exit 2
fi

# Keep Cloud Build staging access scoped to its own bucket for backend jobs
# that may still use Cloud Build. The web preview no longer depends on it.
if gcloud storage buckets describe "gs://${CLOUD_BUILD_BUCKET}" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud storage buckets add-iam-policy-binding "gs://${CLOUD_BUILD_BUCKET}" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role='roles/storage.objectAdmin' --quiet >/dev/null
  gcloud storage buckets add-iam-policy-binding "gs://${CLOUD_BUILD_BUCKET}" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role='roles/storage.bucketViewer' --quiet >/dev/null
else
  echo "Cloud Build source bucket gs://${CLOUD_BUILD_BUCKET} is missing." >&2
  exit 2
fi

if ! gcloud iam service-accounts describe "$WEB_RUNTIME_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${WEB_RUNTIME_SA%%@*}" \
    --project="$PROJECT_ID" --display-name='Sa3arly public web runtime'
fi

if gcloud run services describe sa3arly-api --project="$PROJECT_ID" --region="$REGION" >/dev/null 2>&1; then
  gcloud run services add-iam-policy-binding sa3arly-api \
    --project="$PROJECT_ID" --region="$REGION" \
    --member="serviceAccount:${WEB_RUNTIME_SA}" \
    --role='roles/run.invoker' --quiet >/dev/null
fi

for runtime_sa in \
  "sa3arly-api@${PROJECT_ID}.iam.gserviceaccount.com" \
  "sa3arly-worker@${PROJECT_ID}.iam.gserviceaccount.com" \
  "$BOOTSTRAP_SA" \
  "$WEB_RUNTIME_SA"; do
  if gcloud iam service-accounts describe "$runtime_sa" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts add-iam-policy-binding "$runtime_sa" \
      --project="$PROJECT_ID" \
      --member="serviceAccount:${DEPLOYER_SA}" \
      --role='roles/iam.serviceAccountUser' --quiet >/dev/null
  fi
done

LEGACY_BUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
if gcloud iam service-accounts describe "$LEGACY_BUILD_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts add-iam-policy-binding "$LEGACY_BUILD_SA" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role='roles/iam.serviceAccountUser' --quiet >/dev/null
fi

if gcloud secrets describe "$DB_SECRET" --project="$PROJECT_ID" >/dev/null 2>&1 \
  && gcloud iam service-accounts describe "$BOOTSTRAP_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets add-iam-policy-binding "$DB_SECRET" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:${BOOTSTRAP_SA}" \
    --role='roles/secretmanager.secretAccessor' --quiet >/dev/null
fi

if gcloud secrets describe "$INTERNAL_TOKEN_SECRET" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets add-iam-policy-binding "$INTERNAL_TOKEN_SECRET" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:${WEB_RUNTIME_SA}" \
    --role='roles/secretmanager.secretAccessor' --quiet >/dev/null
fi

if ! gcloud iam workload-identity-pools describe "$POOL_ID" \
  --project="$PROJECT_ID" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$POOL_ID" \
    --project="$PROJECT_ID" --location=global \
    --display-name='GitHub Actions'
fi

ATTRIBUTE_MAPPING='google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.actor=assertion.actor'
ATTRIBUTE_CONDITION="assertion.repository=='${GITHUB_REPOSITORY}' && assertion.ref=='refs/heads/main'"
if gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
  --project="$PROJECT_ID" --location=global \
  --workload-identity-pool="$POOL_ID" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER_ID" \
    --project="$PROJECT_ID" --location=global \
    --workload-identity-pool="$POOL_ID" \
    --issuer-uri='https://token.actions.githubusercontent.com' \
    --attribute-mapping="$ATTRIBUTE_MAPPING" \
    --attribute-condition="$ATTRIBUTE_CONDITION"
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --project="$PROJECT_ID" --location=global \
    --workload-identity-pool="$POOL_ID" \
    --display-name='Sa3arly GitHub repository' \
    --issuer-uri='https://token.actions.githubusercontent.com' \
    --attribute-mapping="$ATTRIBUTE_MAPPING" \
    --attribute-condition="$ATTRIBUTE_CONDITION"
fi

POOL_NAME="$(gcloud iam workload-identity-pools describe "$POOL_ID" \
  --project="$PROJECT_ID" --location=global --format='value(name)')"
PROVIDER_NAME="$(gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
  --project="$PROJECT_ID" --location=global \
  --workload-identity-pool="$POOL_ID" --format='value(name)')"
PRINCIPAL_SET="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository/${GITHUB_REPOSITORY}"

gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" \
  --project="$PROJECT_ID" \
  --member="$PRINCIPAL_SET" \
  --role='roles/iam.workloadIdentityUser' --quiet >/dev/null

cat <<EOF
GITHUB_WIF_BOOTSTRAP=SUCCESS
GITHUB_REPOSITORY=${GITHUB_REPOSITORY}
GCP_WORKLOAD_IDENTITY_PROVIDER=${PROVIDER_NAME}
GCP_DEPLOY_SERVICE_ACCOUNT=${DEPLOYER_SA}
ARTIFACT_REGISTRY_REPOSITORY=${REGION}/${ARTIFACT_REPOSITORY}
ARTIFACT_REGISTRY_WRITER=CONFIGURED
CLOUD_BUILD_BUCKET=gs://${CLOUD_BUILD_BUCKET}
CLOUD_BUILD_BUCKET_OBJECT_ACCESS=CONFIGURED
CLOUD_BUILD_BUCKET_VIEWER=CONFIGURED
AUTONOMOUS_DEPLOY_ENABLED=false

Add these as GitHub repository Variables (not secrets):
  GCP_WORKLOAD_IDENTITY_PROVIDER=${PROVIDER_NAME}
  GCP_DEPLOY_SERVICE_ACCOUNT=${DEPLOYER_SA}
  AUTONOMOUS_DEPLOY_ENABLED=false
EOF

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER \
    --repo "$GITHUB_REPOSITORY" --body "$PROVIDER_NAME"
  gh variable set GCP_DEPLOY_SERVICE_ACCOUNT \
    --repo "$GITHUB_REPOSITORY" --body "$DEPLOYER_SA"
  gh variable set AUTONOMOUS_DEPLOY_ENABLED \
    --repo "$GITHUB_REPOSITORY" --body 'false'
  if [[ -n "${SA3ARLY_ADMIN_EMAILS:-}" ]]; then
    gh variable set SA3ARLY_ADMIN_EMAILS \
      --repo "$GITHUB_REPOSITORY" --body "$SA3ARLY_ADMIN_EMAILS"
  fi
  echo 'GITHUB_VARIABLES=CONFIGURED'
else
  echo 'GITHUB_VARIABLES=MANUAL_CONFIGURATION_REQUIRED'
fi
