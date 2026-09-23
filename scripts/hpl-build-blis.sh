#!/usr/bin/env bash
# Build BLIS and Netlib HPL from pinned sources for 108 single-thread MPI ranks.
set -euo pipefail
root=/shared/hpl/blis-build
revision=e8566eb3e773fb54d11b33e371d13f22d2941e50
mkdir -p "$root"
if [[ ! -f "$root/blis.tar.gz" ]]; then
    curl -fL --retry 2 "https://github.com/flame/blis/archive/$revision.tar.gz" -o "$root/blis.tar.gz"
fi
test ! -e "$root/install/bin/xhpl"
mkdir "$root/blis-source" "$root/hpl-source"
tar -xzf "$root/blis.tar.gz" -C "$root/blis-source" --strip-components=1
archive=/shared/hpl/hpl-2.3.tar.gz
printf '%s  %s\n' 32c5c17d22330e6f2337b681aded51637fb6008d3f0eb7c277b163fadd612830 "$archive" | sha256sum -c -
tar -xzf "$archive" -C "$root/hpl-source" --strip-components=1
{
    date -u --iso-8601=seconds
    printf 'BLIS revision=%s; single-thread skx; HPL GCC -O3 -march=skylake-avx512\n' "$revision"
    gcc --version
    sha256sum "$root/blis.tar.gz" "$archive"
    cd "$root/blis-source"
    ./configure --prefix="$root/blis-install" skx
    make -j16
    make install
    blas="-L$root/blis-install/lib -Wl,-rpath,$root/blis-install/lib -lblis -lpthread -lm"
    python3 - "$root/hpl-source/configure" "$blas" <<'PY'
import pathlib,re,sys
p=pathlib.Path(sys.argv[1])
s,count=re.subn(r'^libs10=.*$', 'libs10='+sys.argv[2],p.read_text(),flags=re.M)
if count != 1: raise SystemExit('Unexpected HPL configure BLAS probe')
p.write_text(s)
PY
    cd "$root/hpl-source"
    ./configure --prefix="$root/install" CC=/shared/environment/view/bin/mpicc CFLAGS='-O3 -march=skylake-avx512' "LDFLAGS=$blas" "LIBS=$blas"
    make -j16
    make install
    ldd "$root/install/bin/xhpl"
    sha256sum "$root/install/bin/xhpl"
} > "$root/build.log" 2>&1
