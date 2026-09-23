#!/usr/bin/env bash
# Capture the actual self-built HPL and vendor tool/library provenance.
set -euo pipefail
variant=${1:?Pass the build variant}
dest=${2:?Pass a new evidence directory}
case "$variant" in gcc-mkl-openmpi|icx-mkl-openmpi|icx-mkl-intelmpi|icx-mkl-intelmpi-fast) ;; *) exit 2 ;; esac
root=/shared/hpl/intel-builds/$variant
test ! -e "$dest"
cp -a /shared/hpl/build-evidence "$dest"
cp "$root/build.log" "$dest/selected-hpl-build.log"
cp /shared/hpl/oneapi-evidence/packages.txt "$dest/intel-packages.txt"
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
cp "$script_dir/hpl-build-oneapi.sh" "$script_dir/helvetios-oneapi-setup.sh" "$dest/"
sha256sum "$root/install/bin/xhpl" > "$dest/hpl.sha256"
ldd "$root/install/bin/xhpl" > "$dest/hpl-libraries.txt"
/opt/intel/oneapi/compiler/2026.1/bin/icx --version > "$dest/intel-compiler.txt"
sha256sum /shared/hpl/hpl-2.3.tar.gz > "$dest/hpl-source.sha256"
python3 - "$root/source" "$dest/modified_source.zip" <<'PY'
import pathlib,sys,zipfile
source=pathlib.Path(sys.argv[1])
with zipfile.ZipFile(sys.argv[2], 'w', zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
    for path in sorted(source.rglob('*')):
        if path.is_file(): archive.write(path, pathlib.Path('hpl-2.3') / path.relative_to(source))
PY
cat > "$dest/source-changes.md" <<'TEXT'
The Netlib HPL 2.3 configure script's libs10 BLAS probe was changed to explicitly
link oneMKL. The numerical algorithm is unchanged. The archive contains the
actual configured build tree. hpl-build-oneapi.sh records the reproducible patch,
verified source checksum, compiler flags and link options.
TEXT
flags=$(sed -n 's/^CFLAGS=//p' "$root/build.log" | head -n 1)
cat > "$dest/build-description.md" <<TEXT
HPL 2.3 was compiled from checksum-verified Netlib source using variant
\`$variant\`. oneMKL 2026.1 supplies BLAS. The variant selects GCC 14.3.1 or
Intel icx 2026.1.1, and source-built OpenMPI 5.0.5 or Intel MPI 2021.18.1.
\`selected-hpl-build.log\` records the actual compiler, flags, build output,
linkage and executable checksum. The selected compiler flags are \`$flags\`.
HPL is built using configure and GNU make.
\`intel-packages.txt\` records exact signed vendor library/compiler RPM versions.
No vendor HPL executable is used. The user confirmed that compiler/libraries
are permitted. The only HPL source modification is the configure BLAS probe;
see \`src/README.md\` and the actual source ZIP.

The Spack records describe the underlying source-built OpenMPI/UCX baseline
and its dependencies; their original HPL/OpenBLAS entries describe the comparison
baseline, not the selected oneMKL-linked executable. After provisioning that
baseline with Cabrita, run on the head node:

\`\`\`bash
bash scripts/build-evidence/helvetios-oneapi-setup.sh
bash scripts/build-evidence/hpl-build-oneapi.sh $variant
\`\`\`

These build commands require a fresh variant build directory. All logs listed
above are under \`scripts/build-evidence/\`.
TEXT
