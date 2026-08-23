#!/usr/bin/env bash
set -Eeuo pipefail

# Delegated implementation invariants verified below:
# REFRESH_CRON=0 10,20 * * *
# --edition=enterprise

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LEGACY_IMPL="${SCRIPT_DIR}/deploy_legacy_impl.sh"

[[ -f "$LEGACY_IMPL" ]] || {
  echo "Missing deployment implementation: ${LEGACY_IMPL}" >&2
  exit 2
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
PATCHED_DEPLOY="${TMP_DIR}/deploy.sh"

python3 - "$LEGACY_IMPL" "$PATCHED_DEPLOY" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
text = source.read_text(encoding="utf-8")
for invariant in (
    "REFRESH_CRON=0 10,20 * * *",
    "--edition=enterprise",
):
    if invariant not in text:
        raise SystemExit(f"Refusing deployment: missing invariant {invariant}")

unsafe = '''  if gcloud sql users list --instance="$DB_INSTANCE" \\
    --format="value(name)" | grep -Fxq "$DB_USER"; then
    gcloud sql users set-password "$DB_USER" \\
      --instance="$DB_INSTANCE" \\
      --password="$DB_PASSWORD"
  else
    gcloud sql users create "$DB_USER" \\
      --instance="$DB_INSTANCE" \\
      --password="$DB_PASSWORD"
  fi
'''
safe = '''  printf '%s' "$DB_PASSWORD" | python3 scripts/cloud_sql_user_password.py \\
    --project "$PROJECT_ID" \\
    --instance "$DB_INSTANCE" \\
    --user "$DB_USER"
'''
if text.count(unsafe) != 1:
    raise SystemExit(
        "Refusing deployment: expected exactly one legacy Cloud SQL password block"
    )
patched = text.replace(unsafe, safe)
if '--password="$DB_PASSWORD"' in patched:
    raise SystemExit("Refusing deployment: DB password would still appear in argv")
target.write_text(patched, encoding="utf-8")
target.chmod(0o700)
PY

cd "$REPO_ROOT"
bash "$PATCHED_DEPLOY" "$@"
