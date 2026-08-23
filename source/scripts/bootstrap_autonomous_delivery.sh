#!/usr/bin/env bash
set -Eeuo pipefail

GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-engmohamedelmorsy-arch/sa3arly}"
PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
VISIBILITY="${VISIBILITY:-private}"

for command_name in git gh gcloud; do
  command -v "$command_name" >/dev/null || {
    echo "${command_name} is required." >&2
    exit 69
  }
done

gh auth status >/dev/null
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || {
  echo 'No active gcloud account.' >&2
  exit 77
}

if [[ ! -d .git ]]; then
  git init -b main
fi
git config user.name "Sa3arly Automation"
git config user.email "automation@sa3arly.com"
git add --all
if ! git diff --cached --quiet; then
  git commit -m 'release: initialize Sa3arly autonomous delivery'
fi

if ! gh repo view "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
  gh repo create "$GITHUB_REPOSITORY" \
    "--${VISIBILITY}" \
    --description 'Sa3arly Egypt price comparison platform'
fi

REMOTE_URL="https://github.com/${GITHUB_REPOSITORY}.git"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

# Configure repository-scoped Google identity and GitHub variables before the
# first push, so the first production workflow can authenticate successfully.
PROJECT_ID="$PROJECT_ID" \
GITHUB_REPOSITORY="$GITHUB_REPOSITORY" \
bash scripts/bootstrap_github_wif.sh

git push --set-upstream origin main

cat <<EOF2
SA3ARLY_AUTONOMOUS_BOOTSTRAP=SUCCESS
GITHUB_REPOSITORY=${GITHUB_REPOSITORY}
PROJECT_ID=${PROJECT_ID}
FIRST_GITHUB_ACTION_TRIGGERED=YES

One account-level step remains for ChatGPT editing access:
GitHub > Settings > Applications > Installed GitHub Apps > ChatGPT > Configure,
then add the ${GITHUB_REPOSITORY##*/} repository to the allowed repositories.
EOF2
