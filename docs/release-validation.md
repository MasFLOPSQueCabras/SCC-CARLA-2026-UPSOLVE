# Release validation

## Publication status

The distribution, Python package, and command were renamed to `cabritactl`
because [PyPI's cabrita project](https://pypi.org/project/cabrita/) belongs to an
unrelated Docker Compose dashboard. On 2026-09-22, the PyPI JSON endpoint for
`cabritactl` returned HTTP 404. This is an availability check, not a reservation.
No release has been published by this implementation work.

Build and install this project's wheel directly:

```bash
uv build
uv tool install ./dist/cabritactl-*.whl
cabritactl --version
```

## Required checks

Every phase commit runs the entire repository gate:

```bash
uv run ruff format .
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

The default pytest suite is infrastructure-independent. Removed legacy helper
coverage is retained through current manifest targeting, artifact caching, and
lifecycle failure/retry tests. GitHub CI runs these checks, packaged Ansible
syntax validation, authoring plans, and a distribution build.

The real libvirt matrix requires KVM, libvirt authorization, native media/image
tools, the default network, a verified Rocky cloud image, and the Rocky installer:

```bash
uv run pytest -m e2e --run-e2e --provider libvirt \
  --installer-iso /path/to/Rocky-minimal.iso \
  --cloud-image /path/to/Rocky-cloud.qcow2
```

Selectors `--provider` and `--bootstrap` are repeatable. Missing prerequisites
fail explicitly. Test logs remain under `test-results/e2e`. Golden restore tests
need guest access to the selected HTTP ports on the host firewall.

## Evidence

Phases 5 and 6 passed real BIOS/UEFI cloud-init, embedded Kickstart, OEMDRV,
media-detachment/disk-boot, and golden capture/restore tests. Phase 7 passed the
two-node SSH/NFS/MPI/mini-HPL test, repeated configuration, and recovery without
rebuilding the benchmark binary. Its commit `27c5171` passed
[GitHub CI](https://github.com/MasFLOPSQueCabras/cabrita/actions/runs/35688170041).

The isolated base wheel passed help/version without optional provider imports,
initialization, validation, and packaged-resource checks outside the checkout.
Both provider extras installed from the wheel and imported successfully in a
fresh Python 3.14 rootless container with libvirt development headers and a C
build toolchain. That environment also passed the same base-wheel checks.
The final cleanup gate passes formatting, Ruff, ty, and 71 unit tests.
The final cleanup VM matrix passed all 10 tests in 19 minutes 17 seconds:
cloud-init, embedded Kickstart, and OEMDRV on BIOS/UEFI; golden recovery on both
firmware modes; two-node HPC recovery; and lifecycle acceptance. Logs are retained
under `test-results/release-libvirt`, with isolated installation evidence under
`test-results/release-wheel`. The provider-backed initialization, validation,
JSON plan, and up/down dry-run workflow also passed.

Physical Helvetios virtual-media boot, restore, BIOS, InfiniBand,
full-size HPL, and competition submission requirements remain unverified.
The Spack competition environment is pinned but has not been built on hardware
in this validation run. Libvirt results cannot substitute for that evidence.

## cabritactl rename verification

The distribution rename passed the full quality gate (71 unit tests), real
BIOS/UEFI cloud-init deployment, and libvirt lifecycle acceptance (3 tests).
Fresh wheel installs outside the checkout passed help, version, initialization,
manifest validation, and packaged-resource checks. Both provider extras installed
and imported in a clean Python 3.14 container. The wheel exposes only the
`cabritactl` executable and Python package; there is no `cabrita` alias.
Rename-specific deployment logs are under `test-results/cabritactl-rename`.
