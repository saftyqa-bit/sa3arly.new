#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${SITES_ENV_READY:-}" != "1" ]]; then
  exec "${script_dir}/sites-env.sh" -- "$0" "$@"
fi

command -v timeout >/dev/null || { echo "GNU timeout is required." >&2; exit 69; }
next_bin="${SITES_PROJECT_ROOT}/node_modules/.bin/next"
[[ -x "${next_bin}" ]] || {
  echo "Next.js is unavailable. Run npm run install:ci first." >&2
  exit 69
}

rm -rf "${SITES_PROJECT_ROOT}/.next"
install -m 0644 \
  "${SITES_PROJECT_ROOT}/app/catalog-data.json" \
  "${SITES_PROJECT_ROOT}/public/catalog-data.json"
echo "[sa3arly-web] building the standard Next.js standalone application"
timeout \
  --signal=TERM \
  --kill-after="${SITES_BUILD_KILL_AFTER:-20s}" \
  "${SITES_BUILD_TIMEOUT:-12m}" \
  "${next_bin}" build

standalone="${SITES_PROJECT_ROOT}/.next/standalone"
mkdir -p "${standalone}/.next"
rm -rf "${standalone}/public" "${standalone}/.next/static"
cp -a "${SITES_PROJECT_ROOT}/public" "${standalone}/public"
cp -a "${SITES_PROJECT_ROOT}/.next/static" "${standalone}/.next/static"

"${script_dir}/validate-artifact.sh"
