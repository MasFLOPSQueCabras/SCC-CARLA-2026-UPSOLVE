#!/usr/bin/env bash
# Source from scripts executed at the Cabrita checkout root. Never echo credentials.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi
export CABRITA_BMC_USER=${CABRITA_BMC_USER:-${SCC_BMC_USER:-}}
export CABRITA_BMC_PASSWORD=${CABRITA_BMC_PASSWORD:-${SCC_BMC_PASSWORD:-}}
export XDG_STATE_HOME=${XDG_STATE_HOME:-$repo/test-results/helvetios-submission/state}
export XDG_CACHE_HOME=${XDG_CACHE_HOME:-$repo/test-results/helvetios-submission/cache}
