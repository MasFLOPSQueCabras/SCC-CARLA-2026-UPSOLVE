# Cabrita

Cabrita provisions user-authored clusters on local libvirt VMs and Helvetios
bare-metal nodes. A `cluster.yaml` declares nodes, artifacts, bootstrap inputs,
and configuration. Planning and execution use the same resolved manifest.

It supports cloud-init, embedded Kickstart ISOs, auxiliary OEMDRV media,
golden-image capture and recovery, custom preparation programs, and custom
Ansible playbooks. Shared HPC profiles provide NFS, MPI, and numerical HPL
validation. Repeated `up` preserves installed disks; replacement requires
`--reinstall`.

## Install

Python 3.14 or newer is required. **Publication is blocked:** PyPI’s `cabrita`
name currently belongs to an unrelated project. Do not install that package
expecting this CLI. The intended commands, once name ownership is resolved, are:

```bash
uv tool install cabrita
# Select the provider dependencies you need:
uv tool install 'cabrita[libvirt]'
# or: uv tool install 'cabrita[helvetios]'
```

From a checkout, use `uv sync --extra libvirt` and prefix commands with `uv run`.
Native QEMU/libvirt, SSH, and media tools are installed separately; see
[installation prerequisites](docs/migration.md#installation-prerequisites).

## One workflow

With an existing cloud image and SSH key:

```bash
cabrita init ./demo --provider libvirt --artifact /path/to/cloud.qcow2
# Review demo/cluster.yaml and its explicit nodes, network, and SSH keys.
cabrita validate --cluster demo/cluster.yaml
cabrita doctor --cluster demo/cluster.yaml
cabrita plan --cluster demo/cluster.yaml --json
cabrita up --cluster demo/cluster.yaml --yes
cabrita verify --cluster demo/cluster.yaml --json
cabrita down --cluster demo/cluster.yaml --yes
```

`down` preserves disks. `destroy` removes managed virtual resources while keeping
reusable artifacts. Helvetios destruction powers off and detaches media without
wiping installed disks. Cabrita never adopts legacy SCC resources automatically.

Start with the [local quickstart](QUICKSTART.md). Read about
[authoring](docs/authoring.md), [bootstrap methods](docs/bootstrap-methods.md),
[lifecycle](docs/lifecycle.md), [golden recovery](docs/golden-recovery.md),
[HPC configuration](docs/hpc-configuration.md), [competition recovery](docs/competition.md),
[migration](docs/migration.md), [architecture](docs/architecture.md), and
[troubleshooting](docs/troubleshooting.md).

[Release validation](docs/release-validation.md) distinguishes real VM evidence
from outstanding hardware checks. [Future work](docs/roadmap.md) covers planned
providers and workflows.
