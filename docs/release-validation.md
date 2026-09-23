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

## Managed host workflow (2026-09-23)

The host workflow adds explicit setup previews/apply, managed libvirt storage,
cluster-owned NAT networks, structured preflight checks, guest peer firewall
reconciliation, and scoped host firewall rules. There is no legacy migration.

Validation evidence:

- The unit suite passes 100 tests, including allocation conflicts, unowned-resource
  protection, interrupted volume uploads, shared artifact retention, firewall
  receipts, setup previews, executable discovery, and firmware selection.
- Fedora 44 with SELinux enforcing passed the three-node BIOS workload. The
  corrected UEFI run passed SSH, guest DNS/HTTPS, NFS, three-rank MPI, numerical
  HPL, stopped-disk export, cache removal, restart, and repeated `up`. That run's
  final cleanup encountered a fresh libvirt/polkit connection timeout; its exact
  test resources were subsequently removed and an empty VM list verified.
  The test now reuses its existing authorized connection for cleanup.
- Ubuntu 26.04.1 with AppArmor enabled passed both BIOS and UEFI managed-host
  tests (2 tests, 271.80 seconds). The UEFI run initially exposed libvirt selecting
  a monolithic AMD-SEV ROM rejected by `virt-aa-helper`; explicit split CODE/VARS
  selection fixes it without changing AppArmor policy.
- Test VMs, networks and pools were removed on both hosts. The existing Ubuntu
  showcase remains running and unchanged.
- Ruff, formatting, type checking, packaged Ansible syntax, and wheel/sdist
  builds were checked during implementation.

Local logs are under `test-results/managed-host`, `test-results/managed-host-fixed`
and `test-results/ubuntu-host-validation`. Test command:

```bash
uv run pytest tests/e2e/test_managed_host.py --run-e2e \
  --cloud-image /path/to/Rocky-cloud.qcow2 --e2e-log-dir test-results/managed-host
```

The real UFW adapter test uses a disposable Ubuntu 26.04 rootless container with
`NET_ADMIN`, not the host firewall. It validates rule creation, repeatability,
recovery HTTP scoping, ownership receipts, preservation of an unrelated rule,
cleanup, and detection of inactive UFW's empty residual chains. It models only
libvirt's already-destroyed-network query; UFW and
its kernel rules are real.

```bash
uv build
uv run pytest tests/e2e/test_ufw_container.py --run-e2e \
  --e2e-log-dir test-results/ufw
```

The VM hosts already had native prerequisites installed. Complete package/service
setup on pristine installations was not exercised. The UFW container does not
substitute for a full libvirt VM traffic test on a UFW-managed host. The full
installer/golden-restore matrix has not been rerun for this change; the new tests
exercise cloud-init and stopped-disk export. Physical Helvetios limitations above
continue to apply.
