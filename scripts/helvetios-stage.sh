#!/usr/bin/env bash
# Fresh gates reinstall all three OS disks; --resume continues an interrupted gate.
set -euo pipefail
source "$(dirname -- "$0")/cabrita-env.sh"
stage=${1:?Usage: helvetios-stage.sh iso|basic|hpl}
case "$stage" in iso|basic|hpl) ;; *) echo 'Unknown stage' >&2; exit 2;; esac
up_args=(--reinstall)
case "${2:-}" in
    '') ;;
    --resume) up_args=() ;;
    *) echo 'Second argument must be --resume' >&2; exit 2 ;;
esac
manifest="$repo/competition/helvetios/$stage.yaml"
if grep -q REQUIRES_LIVE_ "$manifest"; then
    echo "Populate live hardware values in $manifest before installing" >&2
    exit 1
fi
: "${CABRITA_BMC_USER:?Set CABRITA_BMC_USER in .env}"
: "${CABRITA_BMC_PASSWORD:?Set CABRITA_BMC_PASSWORD in .env}"
# This file is created only after matching live hardware to the manifests.
sha256sum --check competition/helvetios/verified-manifests.sha256
run_dir=$(mktemp -d "$repo/test-results/helvetios-submission/${stage}.XXXXXX")
exec > >(tee "$run_dir/stage.log") 2>&1
cp "$manifest" "$run_dir/cluster.yaml"
git rev-parse HEAD > "$run_dir/commit.txt"
uv run --locked cabritactl validate --cluster "$manifest"
uv run --locked cabritactl doctor --cluster "$manifest" --json
uv run --locked cabritactl plan --cluster "$manifest" --json
uv run --locked cabritactl up --cluster "$manifest" "${up_args[@]}" --yes
configuration_log="$XDG_STATE_HOME/cabrita/clusters/helvetios-submission/work/configure.log"
if [[ "$stage" != iso && -f "$configuration_log" ]]; then
    cp "$configuration_log" "$run_dir/configure-initial.log"
fi
uv run --locked cabritactl verify --cluster "$manifest" --json
for node in 1 2 3; do
    ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 -i "$HOME/.ssh/carla_scc_ed25519" -J scc-bastion \
        "scct-2672@10.2.72.$node" \
        'set -eu; hostname; cat /etc/os-release; lsblk -o NAME,SIZE,MODEL,SERIAL,MOUNTPOINTS; ip -brief address; sudo -n true; getent hosts download.rockylinux.org; curl -IfsS --max-time 30 https://download.rockylinux.org; sudo dnf -q repolist' \
        | tee "$run_dir/node${node}.log"
done
if [[ "$stage" != iso ]]; then
    uv run --locked cabritactl configure --cluster "$manifest" --yes
    cp "$configuration_log" "$run_dir/configure-repeat.log"
    uv run --locked cabritactl verify --cluster "$manifest" --json
fi
printf 'Stage evidence: %s\n' "$run_dir"
