#!/usr/bin/env bash
# Run on node1. The settings file is trusted Bash, captured with each result.
set -euo pipefail
if [[ $# != 3 ]]; then
    echo 'Usage: hpl-eval.sh HPL.dat SETTINGS.sh NEW_RESULT_DIRECTORY' >&2
    exit 2
fi
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
input=$(realpath -- "$1")
settings=$(realpath -- "$2")
result=$(realpath -m -- "$3")
mkdir -- "$result"  # Never overwrite an earlier run.
cp -- "$input" "$result/HPL.dat"
cp -- "$settings" "$result/settings.sh"
cp -- "$0" "$result/hpl-eval.sh"
cp -- "$script_dir/hpl-result.py" "$result/hpl-result.py"
source "$result/settings.sh"
: "${HPL_BINARY:?}" "${MPI_LAUNCHER:?}" "${MPI_HOSTFILE:?}"
: "${HPL_RANKS:?}" "${OMP_NUM_THREADS:?}" "${HPL_TIMEOUT_SECONDS:?}"
: "${MPI_MAP_BY:?}" "${MPI_LIBRARY_PATH:?}" "${UCX_NET_DEVICES:?}"
exec 9>"${HPL_LOCK_FILE:-/shared/hpl/.evaluation.lock}"
flock --nonblock 9 || { echo 'Another HPL evaluation is active' >&2; exit 1; }
cp -- "$MPI_HOSTFILE" "$result/hosts"
export OMP_NUM_THREADS OPENBLAS_NUM_THREADS=$OMP_NUM_THREADS
export OMP_PROC_BIND=close OMP_PLACES=cores
export UCX_NET_DEVICES UCX_TLS=rc,sm,self
export PATH="$(dirname -- "$MPI_LAUNCHER"):$PATH"
export LD_LIBRARY_PATH="$MPI_LIBRARY_PATH:${LD_LIBRARY_PATH:-}"
cd "$result"
python3 "$script_dir/hpl-result.py" input HPL.dat "$HPL_RANKS"
{
    date -u --iso-8601=seconds
    hostname
    lscpu
    "$MPI_LAUNCHER" --version
    ldd "$HPL_BINARY"
    sha256sum "$HPL_BINARY" "$MPI_LAUNCHER" HPL.dat settings.sh hosts
    printf 'OMP_NUM_THREADS=%s\nMPI_MAP_BY=%s\nUCX_NET_DEVICES=%s\n' \
        "$OMP_NUM_THREADS" "$MPI_MAP_BY" "$UCX_NET_DEVICES"
} > metadata.txt 2>&1
command=("$MPI_LAUNCHER" -np "$HPL_RANKS" --hostfile "$result/hosts"
    --map-by "$MPI_MAP_BY" --bind-to core --report-bindings
    --mca pml ucx -x PATH -x LD_LIBRARY_PATH -x OMP_NUM_THREADS
    -x OPENBLAS_NUM_THREADS -x OMP_PROC_BIND -x OMP_PLACES -x UCX_NET_DEVICES -x UCX_TLS
    "$HPL_BINARY")
printf '%q ' "${command[@]}" > command.txt
printf '\n' >> command.txt
set +e
timeout --signal=TERM --kill-after=15 "$HPL_TIMEOUT_SECONDS" "${command[@]}" \
    > >(tee HPL.out) 2> >(tee HPL.err >&2)
status=$?
wait
set -e
printf '%s\n' "$status" > exit-status.txt
date -u --iso-8601=seconds > finished.txt
if (( status != 0 )); then exit "$status"; fi
python3 "$script_dir/hpl-result.py" result HPL.out HPL.dat > result.json
printf 'Validated result: %s\n' "$result"
