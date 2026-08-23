#!/usr/bin/env bash
set -Eeuo pipefail

GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-engmohamedelmorsy-arch/sa3arly}"
VISIBILITY="${VISIBILITY:-private}"

command -v git >/dev/null || { echo 'git is required' >&2; exit 69; }
command -v gh >/dev/null || {
  echo 'GitHub CLI (gh) is required for repository creation.' >&2
  echo 'Create the private repository in the GitHub UI, then rerun after installing/authenticating gh.' >&2
  exit 69
}
gh auth status >/dev/null

if [[ ! -d .git ]]; then
  git init -b main
fi

git config user.name "Sa3arly Automation"
git config user.email "automation@sa3arly.com"
git add --all
if ! git diff --cached --quiet; then
  git commit -m "chore: initialize Sa3arly autonomous delivery"
fi

if gh repo view "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "https://github.com/${GITHUB_REPOSITORY}.git"
  else
    git remote add origin "https://github.com/${GITHUB_REPOSITORY}.git"
  fi
  git push --set-upstream origin main
else
  gh repo create "$GITHUB_REPOSITORY" \
    "--${VISIBILITY}" \
    --description 'Sa3arly Egypt price comparison platform' \
    --source=. --remote=origin --push
fi

echo "GITHUB_REPOSITORY_READY=https://github.com/${GITHUB_REPOSITORY}"
