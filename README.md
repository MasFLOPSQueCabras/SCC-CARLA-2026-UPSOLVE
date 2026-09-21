# Cabrita

Cabrita manages user-authored clusters on local libvirt and Helvetios hardware.
It packages the CLI, providers, templates, profiles, and Ansible resources in one
Python distribution. SCC CARLA 2026 remains a competition configuration profile.

Install with Python 3.14 or newer:

```bash
uv tool install cabrita
uv tool install 'cabrita[libvirt]'
uv tool install 'cabrita[helvetios]'
cabrita --help
cabrita --version
```

For development, use `uv sync --extra helvetios` or `uv sync --all-extras`.
Native prerequisites and migration details are in [docs/migration.md](docs/migration.md).
The package is not yet published by this implementation; to test it locally,
run `uv build` and install the generated wheel with `uv tool install <wheel>`.

The source lives in `src/cabrita`: shared operations in `core`, optional backends
in `providers`, and bundled configuration under `ansible` and provider resource directories.

## Pre-Packaged Cluster Profiles

The CLI supports declarative cluster authoring for both local virtualized environments and physical bare-metal hardware:

| Profile | Provider | CPU Emulation | Firmware | Disk Bus & I/O | Network Interface | Use Case |
|---|---|---|---|---|---|---|
| **`vm-standard.yaml`** | `libvirt` | `host-model` | SeaBIOS (`bios`) | VirtIO Block (`virtio`), threads | `virbr0` (NAT `default`) | Portable local development on any developer workstation or CI runner. |
| **`vm-hw-optimized.yaml`** | `libvirt` | `host-passthrough` | UEFI OVMF (`efi`) | `virtio-scsi`, `io_uring`, writeback | `cabrita_bridge` (vhost multiqueue) | High-performance VM matching physical host NUMA and storage latency. |
| **`helvetios-hpc.yaml`** | `helvetios` | Physical Host | HPE UEFI | Direct NVMe / SATA (`/dev/sda`) | `eno1` (Mgmt) + `ibs5f0` (100G IB) | Production competition bare-metal cluster on HPE ProLiant DL385. |

---

## Quick Start

Ensure dependencies and workspace packages are installed using `uv`:

```bash
uv sync --all-extras
uv run cabrita --help
```

> [!TIP]
> The only executable is `cabrita`; legacy executable aliases were removed.

> [!IMPORTANT]
> **Looking for a 5-minute setup?** Check out the [Quickstart Guide](QUICKSTART.md) for copy-paste workflows, local VM sandboxing, and competition cluster bootstrapping.

---

## CLI Defaults & Target Resolution

When commands are run without explicit parameters, `cabrita` applies the following deterministic defaults:

- **Cluster manifest**: Lifecycle commands use `./cluster.yaml`. Select another manifest with `--cluster <path>`; artifact and template paths resolve relative to that manifest.
- **Target nodes**: Commands target the nodes declared in the manifest. Select a subset with repeatable `--node` options, using IDs from your manifest.
- **SSH**: Select a declared node explicitly, for example `cabrita ssh --cluster cluster.yaml --node 1 -- hostname` if your manifest declares node 1.
- **Scaffolding**: `cabrita init` defaults to `--provider libvirt`. Use `--provider helvetios` for physical nodes.
- **Shutdown**: `cabrita down` stops nodes and preserves disks. Use `cabrita destroy` to remove managed virtual resources.

---

## Operational Guide

### 1. Cluster Status (`status`)

Inspect real-time cluster hardware power, SSH reachability, Turso database lifecycle states, and active operational locks. Node probes run concurrently via `ParallelRunner`:

```bash
# Full live hardware & network probe across all nodes in parallel
uv run cabrita status

# Fast database-only view without probing hardware
uv run cabrita status --no-probe
```

---

### 2. Declarative Cluster Authoring (`init` & `cluster`)

Scaffold workspaces, inspect pre-packaged configurations, and validate custom overrides:

```bash
# Scaffold a local VM workspace with cluster.yaml and templates (standard portable profile)
uv run cabrita init --provider libvirt

# Customize CPU, memory, firmware, and disks in the generated cluster.yaml

# Scaffold a Helvetios bare-metal HPC workspace
uv run cabrita init --provider helvetios

# List all available pre-packaged cluster configs and active workspaces
uv run cabrita cluster list

# Inspect hardware and network definitions of a specific cluster profile
uv run cabrita cluster show configs/clusters/vm-hw-optimized.yaml

# Validate manifest schema, IP formatting, and unique allocations
uv run cabrita cluster validate cluster.yaml
```

---

### 3. Integrated Execution Plan & Drift Diff (`up --dry-run` & `down --dry-run`)

`cabrita` integrates planning directly into `cabrita up` and `cabrita down`. Like `terraform apply`, running `cabrita up` or `cabrita down` automatically computes and renders a declarative preview comparing declared configuration (`cluster.yaml` or `--cluster <path>`) against live observed state across hypervisor/BMC, database, and operational locks, prompting for confirmation before making changes:

```bash
# Preview startup execution plan and prompt for confirmation:
uv run cabrita up

# Inspect startup diff without making changes (dry-run):
uv run cabrita up --dry-run
uv run cabrita up --dry-run --cluster configs/clusters/helvetios-hpc.yaml

# Auto-approve startup without interactive prompt:
uv run cabrita up --yes

# Preview teardown execution plan (shows nodes to stop and disks to drop):
uv run cabrita down --dry-run
```

**Example plan output** (node IDs and hostnames come from `cluster.yaml`):

```text
1 node1: deploy
2 node2: deploy
3 node3: deploy
```

---

### 4. Whole-Lifecycle Cluster Startup (`up`)

Orchestrates entire deployment end-to-end: atomic lease locking, virtual media / domain creation, parallel OS installation, SSH readiness polling, and idempotent Ansible configuration:

```bash
# Provision a single node (default HPC BIOS profile)
uv run cabrita up -n 1

# Provision with no polling timeout (waits indefinitely until install completes)
uv run cabrita up -n 1 --no-timeout

# Provision using an explicit cluster configuration
uv run cabrita up -n 1 --cluster configs/clusters/vm-hw-optimized.yaml

# Provision all cluster nodes (1, 2, and 3) in parallel
uv run cabrita up -a

# Override an active or dead operational lock
uv run cabrita up -n 1 --force
```

---

### 5. OS Deployment Only (`deploy`)

Provisions operating system images (via Libvirt QEMU/KVM domain creation or HPE iLO virtual media unattended Kickstart) and polls until SSH is available without executing Ansible:

```bash
# Deploy OS on node 1
uv run cabrita deploy -n 1

# Deploy OS across all 3 nodes concurrently
uv run cabrita deploy -a
```

---

### 6. Post-Provisioning Configuration & Ansible (`configure`)

Idempotent cluster configuration and verification powered by Ansible:

> [!NOTE]
> The Ansible dynamic inventory ([`ansible/inventory/dynamic_inventory.py`](ansible/inventory/dynamic_inventory.py)) automatically extracts cluster topology, node IPs, roles (`headnode`, `computenode`), usernames, and network parameters directly from `cluster.yaml` (when present in your working directory) or from `configs/clusters/<cluster>.yaml` (such as `helvetios-hpc.yaml` or `vm-hw-optimized.yaml`). You can also specify an explicit cluster manifest or override file using `--cluster <path>` or the `CABRITA_CLUSTER_MANIFEST` environment variable.

```bash
# Configure all cluster nodes (hosts, base packages, InfiniBand, RDMA limits)
uv run cabrita configure

# Configure using a specific cluster profile
uv run cabrita configure --cluster configs/clusters/vm-hw-optimized.yaml

# Dry-run check mode (preview changes without applying)
uv run cabrita configure --check

# Target specific nodes
uv run cabrita configure --limit node1,node2

# Run InfiniBand fabric verification playbook
uv run cabrita configure -p verify_ib.yaml
```

---

### 7. Power Management & Telemetry (`power`)

Direct bare-metal power operations via BMC Redfish (or Libvirt KVM) without re-provisioning. Multi-node operations run concurrently via `ParallelRunner`:

```bash
# Inspect current hardware power state (all nodes)
uv run cabrita power status

# Inspect a specific node
uv run cabrita power status -n 1

# Power on node(s) in parallel and wait until confirmed ON
uv run cabrita power on -n 1 --wait
uv run cabrita power on -a -w

# Power off node(s) gracefully and wait until confirmed OFF
uv run cabrita power off -n 1 --wait
uv run cabrita power off -a -w

# Force immediate hardware power off
uv run cabrita power off -n 1 --force -w

# Reboot / restart node(s) in parallel
uv run cabrita power restart -a -w

# Live power draw telemetry (Current Watts, 20-min avg, Min, Peak, and Cluster Total)
uv run cabrita power metrics
uv run cabrita power metrics -n 1

# Stream continuous live power metrics updates
uv run cabrita power metrics --watch
uv run cabrita power metrics -w -i 1.0
```

---

### 8. Native SSH Access (`ssh`)

Direct transparent jump through the bastion host to physical or virtual cluster nodes:

```bash
# Open interactive shell on node 1 (default)
uv run cabrita ssh 1

# Connect to node 2 or node 3
uv run cabrita ssh 2

# Open interactive shell on the bastion host
uv run cabrita ssh bastion

# Execute remote command directly
uv run cabrita ssh 1 "uname -a"
```

---

### 9. BIOS Management (`bios`)

Inspect, backup, and stage workload-optimized BIOS profiles via Redfish:

```bash
# Inspect current BIOS configuration on a node
uv run cabrita bios show -n 1

# Backup BIOS settings to a JSON file
uv run cabrita bios backup -n 1 -o bios_backup.json

# Stage a BIOS profile (takes effect on next reboot)
uv run cabrita bios apply -n 1 -p hpc
uv run cabrita bios apply -a -p low_latency
```

---

### 10. Operational Lease Locks (`lock`)

Distributed, zero-sudo SSH lease locks prevent team members from issuing conflicting operations on shared physical hardware:

```bash
# List all active and expired locks
uv run cabrita lock list

# Release / break a specific lock (e.g. node-1, cluster, or all)
uv run cabrita lock release node-1
uv run cabrita lock release all
```

---

### 11. Decommissioning & Teardown (`down`)

Ejects virtual media, destroys Libvirt domains, sweeps ephemeral HTTP background processes, and updates cluster state:

```bash
# Decommission a single node
uv run cabrita down -n 1

# Decommission all cluster nodes concurrently
uv run cabrita down -a

# Sweep lingering bastion background servers without touching nodes
uv run cabrita down

# Reset cluster database states back to UNPROVISIONED
uv run cabrita down -a --reset-db
```

---

### 12. Golden Image & Streaming Pipeline (`image`)

Eliminates repeated 15-minute unattended OS installations by caching golden base images and streaming raw disk blocks:

```bash
# List all cached ISOs, base cloud images, and golden images
uv run cabrita image list

# Inspect detailed image allocation, virtual size, and format
uv run cabrita image inspect ~/.cache/cabrita/images/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2

# Export and compress a QCOW2 golden image to .raw.zst for Bastion HTTP streaming
uv run cabrita image export --source ~/.cache/cabrita/golden/golden-rocky-base.qcow2
```

---

## Configuration & Environment Variables

Configuration is loaded from environment variables (prefixed with `CABRITA_`) or a local `.env` file:

| Setting | Default | Description |
|---|---|---|
| `CABRITA_TEAM_ID` | `72` | Competition team identifier |
| `CABRITA_PROVIDER` | `libvirt` | Default node provider (`libvirt`, `helvetios`, `bmc`, `chameleon`) |
| `CABRITA_CLUSTER` | `vm-standard` | Default named cluster profile in `configs/clusters/` |
| `CABRITA_CLUSTER_MANIFEST` | *(auto)* | Explicit path to active cluster manifest or `cluster.yaml` |
| `CABRITA_BASTION_SSH_HOST` | `cabrita-bastion` | SSH host alias for bastion gateway |
| `CABRITA_BASTION_HTTP_IP` | `10.7.12.101` | Bastion internal IP for iLO HTTP serving |
| `CABRITA_BASTION_HTTP_PORT` | `8072` | Ephemeral HTTP server port on bastion |
| `CABRITA_BASTION_STATE_DB_PATH` | `~/.config/cabrita/cabrita_state.db` | Shared Turso (`pyturso`) DB path on bastion |
| `CABRITA_NODE_USERNAME` | `scct-2672` | Node OS administrative username |

---

## Quality Gates & CI Pipeline

The project includes an automated GitHub Actions CI pipeline ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) validating every pull request and push:

```bash
# Check code formatting across entire workspace
uv run ruff format --check .

# Lint entire workspace
uv run ruff check .

# Type check all packages
uv run ty check

# Validate all pre-packaged cluster manifests
uv run cabrita cluster validate configs/clusters/vm-standard.yaml
uv run cabrita cluster validate configs/clusters/vm-hw-optimized.yaml
uv run cabrita cluster validate configs/clusters/helvetios-hpc.yaml

# Validate Ansible dynamic inventory
uv run ansible-inventory -i ansible/inventory/dynamic_inventory.py --list

# Validate Ansible playbooks syntax
ANSIBLE_CONFIG=ansible/ansible.cfg uv run ansible-playbook -i ansible/inventory/dynamic_inventory.py --syntax-check ansible/playbooks/site.yaml ansible/playbooks/verify_ib.yaml

# Verify execution plan dry-run
uv run cabrita up --dry-run --cluster configs/clusters/vm-standard.yaml
```

---

## Documentation

Comprehensive architecture, hardware, and performance guides:

- [Bare-Metal Competition Cluster Guide & Troubleshooting](docs/QUICKSTART_SCC_CARLA2026.md) - Dedicated runbook, failure modes, error codes, and troubleshooting manual for Helvetios.
- [Competence Replication Guide (Libvirt to Helvetios)](docs/COMPETENCE_REPLICATION.md) - Matrix of what can be replicated locally with 100% fidelity vs physical HPC.
- [Cluster Hardware Specifications (SPECS)](docs/SPECS.md) - Deep dive into Helvetios dual-socket Xeon Gold 6140, AVX-512 frequencies, and $R_{\text{peak}}$.
- [InfiniBand & MPI+UCX Guide](docs/NETWORKING.md) - 100 Gbps ConnectX-5 architecture, RDMA, IPoIB, and OpenMPI/UCX tuning.
- [HPL Benchmark Guide](docs/HPL.md) & [HPL Tuning](docs/HPL_TUNING.md) - High Performance Linpack derivation ($N, NB, P \times Q$), grid search, and residual verification.
- [MPI Programming & Execution](docs/MPI.md) - Multi-node execution patterns and process binding.

---

## Team Members

- Isaac David
- Miguel Aguilar
- Pablo Perez
