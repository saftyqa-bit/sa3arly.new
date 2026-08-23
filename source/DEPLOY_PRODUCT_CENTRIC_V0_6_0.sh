#!/usr/bin/env bash
set -Eeuo pipefail
echo "DEPLOY_PRODUCT_CENTRIC_V0_6_0.sh is retained as a compatibility wrapper; executing v0.6.1." >&2
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/DEPLOY_PRODUCT_CENTRIC_V0_6_1.sh" "$@"
