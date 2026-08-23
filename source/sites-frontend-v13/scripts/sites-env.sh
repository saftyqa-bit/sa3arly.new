#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"
runtime_root="${project_root}/.sites-runtime"

mkdir -p "${runtime_root}/home" "${runtime_root}/npm-cache" "${runtime_root}/tmp"

export SITES_PROJECT_ROOT="${project_root}"
export SITES_ENV_READY=1
export HOME="${runtime_root}/home"
export npm_config_cache="${runtime_root}/npm-cache"
export XDG_CACHE_HOME="${runtime_root}/cache"
export TMPDIR="${runtime_root}/tmp"

if [[ "${1:-}" == "--" ]]; then shift; fi
cd "${project_root}"
exec "$@"
