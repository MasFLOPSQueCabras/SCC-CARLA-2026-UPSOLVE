# Migrating to cabritactl

The distribution, Python package, and executable are `cabritactl`. The former
`cabrita`, `scc`, and `scc-carla` executable names have no compatibility aliases.
Uninstall older distributions explicitly if previously installed. Python imports
must use `cabritactl`.

Cabrita remains the deployment resource namespace: state/cache directories,
managed VM names, and guest configuration paths continue to use `cabrita`.
Renaming the CLI does not rename or adopt SCC resources.

The environment prefix is `CABRITA_`. Copy configuration intentionally and review
resource names before execution. Managed virtual machine names include `cabrita-` and the declared cluster name.
Application data uses Cabrita’s own platform directories.

SCC CARLA 2026 remains available through the Helvetios HPC profile, including its
competition network, account, and hardware settings. Customize the bastion SSH
alias and credentials for your environment. Resources have no repository symlink dependencies; wheel resources are read from the installed package.

## Installation prerequisites

Python 3.14 or newer and uv are required. The `cabritactl` PyPI name was available
when checked on 2026-09-22. Until this project is published, install a locally
built wheel or use the checkout. Published installation commands will be
`uv tool install cabritactl`, `uv tool install 'cabritactl[libvirt]'`, or
`uv tool install 'cabritactl[helvetios]'`.

Libvirt needs a running libvirt/QEMU installation, permissions for its socket,
`qemu-img`, and a suitable virtualization runner. Building `libvirt-python` needs
a compiler, Python headers, pkg-config, and libvirt development headers
(`libvirt-devel` on Fedora or `libvirt-dev` on Debian/Ubuntu).

Installer media tools include `xorriso`, `mtools`, and `dosfstools`; UEFI requires
OVMF. Golden image tools additionally need libguestfs and zstd. Helvetios requires
OpenSSH, access to the bastion and BMC network, and the selected media tools on
the machine doing preparation. These native dependencies are not installed by
Python package extras.

## Quality checks

Run `uv run ruff format .`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run ty check`, and `uv run pytest` before
committing. Infrastructure tests are opt-in with `--run-e2e`; selectors are
repeatable `--provider` and `--bootstrap` options. Keep deployment logs under
`test-results/e2e` (or set `--e2e-log-dir`). Skipped infrastructure tests are not
acceptance evidence.

## Removed interfaces

There are no compatibility aliases. Providers are named `libvirt` and
`helvetios`; `vm`, `bmc`, and `chi` are not accepted provider names. Use root
`cabritactl init`, `validate`, and `plan` with `--cluster`; the old `cluster` commands
and `provider add` are removed. `provider list` reports available integrations.
Golden images use `image capture`, `image inspect`, and `image list`.

The duplicated repository-level Ansible and template trees, legacy state
database helpers, and unregistered command implementations have been removed.
Custom playbooks and templates should be declared in the manifest.
