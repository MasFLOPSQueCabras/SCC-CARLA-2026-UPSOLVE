#!/usr/bin/env bash
# Run on the configured head node. Libraries/tools are permitted; build HPL separately.
set -euo pipefail
mkdir -p /shared/intel /shared/hpl/oneapi-evidence
for peer in 10.148.72.1 10.148.72.2 10.148.72.3; do
    ssh "$peer" 'set -e; if test -e /opt/intel || test -L /opt/intel; then test "$(readlink -f /opt/intel)" = "$(readlink -f /shared/intel)"; else sudo ln -s /shared/intel /opt/intel; fi'
done
dnf_args=(-y --repofrompath=oneapi,https://yum.repos.intel.com/oneapi
    --setopt=oneapi.gpgcheck=1 --setopt=oneapi.repo_gpgcheck=1
    --setopt=oneapi.gpgkey=https://yum.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB
    --setopt=install_weak_deps=False --setopt=keepcache=True)
if [[ ${1:-all} != compiler ]]; then
    sudo dnf "${dnf_args[@]}" install intel-oneapi-mkl-devel-2026.1.0-236 \
        intel-oneapi-mpi-devel-2021.18.1-9
fi
if [[ ${1:-all} != libraries ]]; then
    sudo dnf "${dnf_args[@]}" install intel-oneapi-dpcpp-cpp-2026.1-2026.1.1-325
fi
rpm -qa --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' 'intel-oneapi*' \
    | sort > /shared/hpl/oneapi-evidence/packages.txt
