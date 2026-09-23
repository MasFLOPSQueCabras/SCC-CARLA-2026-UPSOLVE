#!/usr/bin/env bash
# Run on the head node after a successful source build.
set -euo pipefail
out=${1:?Usage: hpl-build-evidence.sh NEW_OUTPUT_DIRECTORY}
mkdir -- "$out"
spack=/shared/spack/bin/spack
cp /shared/environment/spack.yaml /shared/environment/spack.lock "$out/"
"$spack" -e /shared/environment find --json > "$out/installed.json"
"$spack" -e /shared/environment config blame config > "$out/spack-config.txt"
"$spack" config blame compilers > "$out/compiler.txt"
git -C /shared/spack rev-parse HEAD > "$out/spack-commit.txt"
gcc --version > "$out/gcc-version.txt"
/shared/environment/view/bin/mpirun --version > "$out/mpi-version.txt"
/shared/environment/view/bin/ompi_info --all > "$out/ompi-info.txt"
/shared/environment/view/bin/ucx_info -v > "$out/ucx-version.txt"
ldd /shared/environment/view/bin/xhpl > "$out/hpl-libraries.txt"
sha256sum /shared/environment/view/bin/xhpl > "$out/hpl.sha256"
# The pinned HPL recipe rewrites its configure BLAS probe: include that source.
source_dir=$("$spack" -e /shared/environment location -s hpl)
python3 - "$source_dir" "$out/modified_source.zip" <<'PY'
import pathlib, sys, zipfile
source = pathlib.Path(sys.argv[1])
if not (source / 'configure').is_file():
    raise SystemExit('Missing retained HPL source; build with --keep-stage')
with zipfile.ZipFile(sys.argv[2], 'x', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source.rglob('*')):
        if path.is_file():
            archive.write(path, pathlib.Path('hpl-2.3') / path.relative_to(source))
PY
cat > "$out/source-changes.md" <<'SOURCE'
# HPL build-source changes

The pinned Spack HPL 2.3 recipe changes the `libs10` assignment in `configure`
to the selected BLAS library's linker flags. This makes its BLAS detection use
the source-built OpenBLAS installation. No HPL numerical algorithm was changed.
The ZIP contains the retained HPL build-source tree, including generated build
files. Exact recipes and build commands are recorded alongside this archive.
SOURCE
# Spack keeps exact build environments, commands, logs, and source manifests here.
find /shared/software -path '*/.spack/*' -type f \
    \( -name '*build*.txt' -o -name '*build*.gz' -o -name 'spec.json' -o -name '*source*' \) \
    -print0 | tar --null -T - -czf "$out/spack-build-records.tar.gz"
for node in node1 node2 node3; do
    ssh "$node" 'hostname; lscpu; free -b; ip -brief address; ibstat; ulimit -l; tuned-adm active' \
        > "$out/$node.txt" 2>&1
done
