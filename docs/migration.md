# Migrating to Cabrita

The distribution, import package, executable, and application directories are now
`cabrita`. The former `scc` and `scc-carla` executables and separate workspace
provider distributions are removed. Uninstall those distributions explicitly if
previously installed. Cabrita does not discover, adopt, rename, or delete their
state, virtual machines, caches, disks, or bastion media.

The environment prefix is `CABRITA_`. Copy configuration intentionally and review
resource names before execution. Managed virtual machine names include `cabrita-` and the declared cluster name.
Application data uses Cabrita’s own platform directories.

SCC CARLA 2026 remains available through the Helvetios HPC profile, including its
competition network, account, and hardware settings. Customize the bastion SSH
alias and credentials for your environment. Resources have no repository symlink dependencies; wheel resources are read from the installed package.

## Installation prerequisites

Python 3.14 or newer and uv are required. PyPI’s `cabrita` name is currently
occupied by an unrelated package; use the repository or a locally built wheel
until publication is resolved. The intended published base command is
`uv tool install cabrita`; provider commands are `uv tool install 'cabrita[libvirt]'` or
`uv tool install 'cabrita[helvetios]'` to select optional dependencies.

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
`cabrita init`, `validate`, and `plan` with `--cluster`; the old `cluster` commands
and `provider add` are removed. `provider list` reports available integrations.
Golden images use `image capture`, `image inspect`, and `image list`.

The duplicated repository-level Ansible and template trees, legacy state
database helpers, and unregistered command implementations have been removed.
Custom playbooks and templates should be declared in the manifest.
