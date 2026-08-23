#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${SITES_ENV_READY:-}" != "1" ]]; then
  exec "${script_dir}/sites-env.sh" -- "$0" "$@"
fi

root="${SITES_PROJECT_ROOT}/.next"
standalone="${root}/standalone"
required=(
  "${root}/BUILD_ID"
  "${standalone}/server.js"
  "${standalone}/package.json"
  "${standalone}/.next/static"
  "${standalone}/public"
)
for path in "${required[@]}"; do
  [[ -e "${path}" ]] || { echo "Missing Next.js artifact: ${path}" >&2; exit 66; }
done

node --check "${standalone}/server.js"
node --input-type=module - "${standalone}/package.json" <<'NODE'
import { readFile } from "node:fs/promises";
const manifest = JSON.parse(await readFile(process.argv[2], "utf8"));
if (!manifest.name) throw new Error("standalone package.json has no package name");
NODE

echo "[sa3arly-web] validated Next.js standalone server, static files, and public assets"
