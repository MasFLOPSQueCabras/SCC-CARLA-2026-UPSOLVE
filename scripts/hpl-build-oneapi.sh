#!/usr/bin/env bash
# Build reference HPL ourselves against explicitly selected compiler/MPI/BLAS.
set -euo pipefail
variant=${1:?Usage: hpl-build-oneapi.sh gcc-mkl-openmpi|icx-mkl-openmpi|icx-mkl-intelmpi|icx-mkl-intelmpi-fast|icx-mkl-intelmpi-mixed}
root=/shared/hpl/intel-builds/$variant
mkdir -p "$root"
test ! -e "$root/install/bin/xhpl"
mkl=/opt/intel/oneapi/mkl/2026.1
compiler=/opt/intel/oneapi/compiler/2026.1
intelmpi=/opt/intel/oneapi/mpi/2021.18
export PATH="$compiler/bin:$PATH"
export LD_LIBRARY_PATH="$compiler/lib:${LD_LIBRARY_PATH:-}"
case "$variant" in
    gcc-mkl-openmpi)
        cc=/shared/environment/view/bin/mpicc
        blas="-L$mkl/lib -Wl,-rpath,$mkl/lib -lmkl_gf_lp64 -lmkl_gnu_thread -lmkl_core -lgomp -lpthread -lm -ldl"
        flags='-O3 -march=skylake-avx512'
        ;;
    icx-mkl-openmpi)
        cc=/shared/environment/view/bin/mpicc
        export OMPI_CC="$compiler/bin/icx"
        blas="-L$mkl/lib -L$compiler/lib -Wl,-rpath,$mkl/lib -Wl,-rpath,$compiler/lib -lmkl_intel_lp64 -lmkl_intel_thread -lmkl_core -liomp5 -lpthread -lm -ldl"
        flags='-O3 -xCORE-AVX512 -fp-model=precise'
        ;;
    icx-mkl-intelmpi|icx-mkl-intelmpi-fast|icx-mkl-intelmpi-mixed)
        export I_MPI_ROOT="$intelmpi" I_MPI_CC="$compiler/bin/icx"
        cc=$intelmpi/bin/mpiicx
        blas="-L$mkl/lib -L$compiler/lib -Wl,-rpath,$mkl/lib -Wl,-rpath,$compiler/lib -lmkl_intel_lp64 -lmkl_intel_thread -lmkl_core -liomp5 -lpthread -lm -ldl"
        flags='-O3 -xCORE-AVX512 -fp-model=precise'
        if [[ $variant == icx-mkl-intelmpi-fast || $variant == icx-mkl-intelmpi-mixed ]]; then
            flags='-O3 -xCORE-AVX512 -fp-model=fast=2 -qopt-zmm-usage=high'
        fi
        ;;
    *) echo 'Unknown build variant' >&2; exit 2 ;;
esac
archive=/shared/hpl/hpl-2.3.tar.gz
if [[ ! -f "$archive" ]]; then
    curl -fL --retry 2 https://www.netlib.org/benchmark/hpl/hpl-2.3.tar.gz -o "$archive"
fi
printf '%s  %s\n' 32c5c17d22330e6f2337b681aded51637fb6008d3f0eb7c277b163fadd612830 "$archive" | sha256sum -c -
mkdir "$root/source"
tar -xzf "$archive" -C "$root/source" --strip-components=1
# Match the Spack HPL recipe: direct the configure BLAS probe to the chosen BLAS.
python3 - "$root/source/configure" "$blas" <<'PY'
import pathlib,re,sys
p=pathlib.Path(sys.argv[1])
s,count=re.subn(r'^libs10=.*$', 'libs10='+repr(sys.argv[2]),p.read_text(),flags=re.M)
if count != 1: raise SystemExit('Unexpected HPL configure BLAS probe')
p.write_text(s)
PY
{
    date -u --iso-8601=seconds
    "$cc" --version
    printf 'CC=%s\nCFLAGS=%s\nBLAS=%s\n' "$cc" "$flags" "$blas"
    cd "$root/source"
    ./configure --prefix="$root/install" "CC=$cc" "CFLAGS=$flags" "LDFLAGS=$blas" "LIBS=$blas"
    if [[ $variant == icx-mkl-intelmpi-mixed ]]; then
        precise_flags='-O3 -xCORE-AVX512 -fp-model=precise -qopt-zmm-usage=high'
        printf 'Machine precision and validation CFLAGS=%s\n' "$precise_flags"
        make -C src auxil/HPL_dlamch.o pauxil/HPL_pdlamch.o "CFLAGS=$precise_flags"
        make -C src -j4
        make -C testing -j4 "CFLAGS=$precise_flags"
    else
        make -j4
    fi
    make install
    ldd "$root/install/bin/xhpl"
    sha256sum "$root/install/bin/xhpl"
} > "$root/build.log" 2>&1
printf 'Built %s\n' "$root/install/bin/xhpl"
