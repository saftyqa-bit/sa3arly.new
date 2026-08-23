#!/usr/bin/env bash
set -Eeuo pipefail

export PROJECT_ID="${PROJECT_ID:-sa3arly-prod-972741}"
export BOT_CONTACT_URL="${BOT_CONTACT_URL:-https://sa3arly.com/bot}"
export ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-https://sa3arly.com}"
export REGION="${REGION:-europe-west1}"
if [[ -z "${DB_PASSWORD:-}" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    DB_PASSWORD="$(openssl rand -hex 32)"
  else
    DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
fi
export DB_PASSWORD

# Deployment always leaves collection paused. START_SA3ARLY_COLLECTION.sh is
# the only activation path after the verification report has been reviewed.
exec bash infra/gcp/deploy.sh
