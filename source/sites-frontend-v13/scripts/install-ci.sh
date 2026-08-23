#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${SITES_ENV_READY:-}" != "1" ]]; then
  exec "${script_dir}/sites-env.sh" -- "$0" "$@"
fi

command -v npm >/dev/null || { echo "npm is required." >&2; exit 69; }
command -v timeout >/dev/null || { echo "GNU timeout is required." >&2; exit 69; }

printf '%s\n' "[sa3arly-web] installing the package-lock with npm ci"
export NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-https://registry.npmjs.org}"
export NPM_CONFIG_AUDIT=false
export NPM_CONFIG_FUND=false
export NPM_CONFIG_FETCH_RETRIES="${NPM_CONFIG_FETCH_RETRIES:-2}"
export NPM_CONFIG_FETCH_TIMEOUT="${NPM_CONFIG_FETCH_TIMEOUT:-120000}"

printf 'npm registry: %s\n' "$NPM_CONFIG_REGISTRY"
timeout \
  --signal=TERM \
  --kill-after="${SITES_INSTALL_KILL_AFTER:-20s}" \
  "${SITES_INSTALL_TIMEOUT:-12m}" \
  npm ci --no-audit --no-fund

for executable in next tsc; do
  [[ -x "${SITES_PROJECT_ROOT}/node_modules/.bin/${executable}" ]] || {
    echo "npm ci completed but ${executable} is unavailable." >&2
    exit 69
  }
done

echo "[sa3arly-web] dependency installation passed"
