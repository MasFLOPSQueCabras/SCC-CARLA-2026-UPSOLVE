# SCC@CARLA 2026 Cluster Management CLI (`scc-carla`)

Automated provisioning, Redfish BIOS workload tuning, declarative cluster authoring, dual-provider execution (KVM/Libvirt & bare-metal Helvetios), and distributed lifecycle management for the SCC@CARLA HPC cluster.

---

## Architecture & UV Workspace

The project is structured as a modular `uv` workspace comprising three foundation packages and a lean root CLI coordinator:

```text
SCC_CARLA/
├── packages/
│   ├── scc-core/                 # Manifests, Pydantic models, Jinja2 engine, hooks, ParallelRunner, locks
│   ├── scc-provider-libvirt/     # QEMU/KVM provider, CoW overlays, CIDATA FAT generator, domain templating
│   └── scc-provider-helvetios/   # HPE iLO Redfish REST client, SSH SOCKS5 tunnel, OEMDRV, Range HTTP server
├── configs/clusters/             # Pre-packaged declarative cluster profiles (YAML)
│   ├── vm-standard.yaml          # Generic portable Libvirt profile (SeaBIOS, host-model, virbr0)
│   ├── vm-hw-optimized.yaml      # Hardware-tuned Libvirt profile (UEFI, host-passthrough, io_uring, virtio-scsi)
│   └── helvetios-hpc.yaml        # Bare-metal HPC profile (dual Xeon Gold 6140, iLO Redfish, ConnectX-5 IB)
├── ansible/                      # Post-provisioning configuration, dynamic inventory, and verification
│   ├── inventory/
│   │   └── dynamic_inventory.py  # Dynamic inventory resolving from values.yaml or cluster configs
│   ├── playbooks/                # site.yaml (node_independent & cluster_coordination), verify_ib.yaml
│   └── roles/                    # common, infiniband, hpc_tune, nfs_server, nfs_client, spack, hpl
├── src/scc_carla/                # Modular Typer CLI application and command routers
├── docs/                         # In-depth architectural, hardware, networking, and benchmark guides
└── .github/workflows/ci.yml      # CI workflow for linting, type checks, manifest validation, and dynamic inventory
```

---

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
uv sync --all-packages
uv run scc --help
```

> [!TIP]
> Both `uv run scc` and `uv run scc-carla` can be used interchangeably to invoke the CLI.

> [!IMPORTANT]
> **Looking for a 5-minute setup?** Check out the [Quickstart Guide](QUICKSTART.md) for copy-paste workflows, local VM sandboxing, and competition cluster bootstrapping.

---

## CLI Defaults & Target Resolution

When commands are run without explicit parameters, `scc-carla` applies the following deterministic defaults:

- **Cluster Manifest Resolution (`scc plan`, `scc cluster validate`)**: Searches in order:
  1. `./values.yaml` in the current working directory.
  2. `values.yaml` at the repository root.
  3. `configs/clusters/vm-standard.yaml` (portable generic fallback).
- **Target Nodes (`scc up`, `scc down`, `scc status`, `scc power`, `scc configure`, `scc bios`)**: Default to **all 3 nodes: `[1, 2, 3]`**. Target specific nodes using `-n <id>` (e.g. `-n 1` or `-n 1 -n 2`).
- **SSH Target (`scc ssh`)**: Defaults to **Node 1** (`node1` at `10.2.72.1` / `192.168.122.101`). Use `scc ssh 2` or `scc ssh bastion`.
- **Scaffolding (`scc init`)**: Defaults to `--provider vm` and `--profile standard`.
- **BIOS Profile (`scc up`, `scc bios apply`)**: Defaults to `--bios-profile hpc` (Maximum Performance, NUMA on, Hyper-Threading off).
- **Teardown Mode (`scc down`)**: Defaults to graceful shutdown (`--graceful`, 60s timeout) preserving disk images unless `--purge` is passed.

---

## Operational Guide

### 1. Cluster Status (`status`)

Inspect real-time cluster hardware power, SSH reachability, Turso database lifecycle states, and active operational locks. Node probes run concurrently via `ParallelRunner`:

```bash
# Full live hardware & network probe across all nodes in parallel
uv run scc status

# Fast database-only view without probing hardware
uv run scc status --no-probe
```

---

### 2. Declarative Cluster Authoring (`init` & `cluster`)

Scaffold workspaces, inspect pre-packaged configurations, and validate custom overrides:

```bash
# Scaffold a local VM workspace with values.yaml and templates (standard portable profile)
uv run scc init --provider vm --profile standard

# Scaffold a hardware-optimized VM workspace matching host CPU/virtio-scsi/io_uring
uv run scc init --provider vm --profile hw-optimized

# Scaffold a Helvetios bare-metal HPC workspace
uv run scc init --provider helvetios

# List all available pre-packaged cluster configs and active workspaces
uv run scc cluster list

# Inspect hardware and network definitions of a specific cluster profile
uv run scc cluster show configs/clusters/vm-hw-optimized.yaml

# Validate manifest schema, IP formatting, and unique allocations
uv run scc cluster validate values.yaml
```

---

### 3. Integrated Execution Plan & Drift Diff (`up --dry-run` & `down --dry-run`)

`scc-carla` integrates planning directly into `scc up` and `scc down`. Like `terraform apply`, running `scc up` or `scc down` automatically computes and renders a declarative preview comparing declared configuration (`values.yaml` or `--cluster <path>`) against live observed state across hypervisor/BMC, database, and operational locks, prompting for confirmation before making changes:

```bash
# Preview startup execution plan and prompt for confirmation:
uv run scc up

# Inspect startup diff without making changes (dry-run):
uv run scc up --dry-run
uv run scc up --dry-run --cluster configs/clusters/helvetios-hpc.yaml

# Auto-approve startup without interactive prompt:
uv run scc up --yes

# Preview teardown execution plan (shows nodes to stop and disks to drop):
uv run scc down --dry-run
```

**Example Plan Output:**
```text
╭───────────────────────── SCC Cluster Execution Plan ─────────────────────────╮
│ Cluster Manifest: vm-hw-optimized                                            │
│ (/home/orpheezt/personal/SCC_CARLA/configs/clusters/vm-hw-optimized.yaml)    │
│ Provider: libvirt | Nodes: 3 | Description: Hardware-optimized QEMU/KVM      │
│ cluster with host-passthrough, UEFI, virtio-scsi, io_uring, and vhost        │
╰──────────────────────────────────────────────────────────────────────────────╯
                                Resource State & Drift Comparison       
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Resource             ┃ Type          ┃ Declared State               ┃ Observed Live State    ┃ Action      ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ Cluster Locks        │ Lock          │ Exclusive cluster lease      │ Free (No active locks) │ * TASK      │
│ Network Subnet       │ Network       │ Gateway: 192.168.122.1, DNS: │ Bridge/Subnet 192.168. │ = NOOP      │
│ VM Domain 'node1'    │ Libvirt       │ 8 vCPU, 16384MB RAM, EFI,    │ Non-existent           │ + CREATE    │
│ Disk Overlay 'node1' │ CoW Storage   │ 60 GB, bus: scsi, io_uring   │ Not created            │ + CREATE    │
│ VM Domain 'node2'    │ Libvirt       │ 4 vCPU, 8192MB RAM, EFI,     │ Non-existent           │ + CREATE    │
│ Disk Overlay 'node2' │ CoW Storage   │ 40 GB, bus: scsi, io_uring   │ Not created            │ + CREATE    │
│ VM Domain 'node3'    │ Libvirt       │ 4 vCPU, 8192MB RAM, EFI,     │ Non-existent           │ + CREATE    │
│ Disk Overlay 'node3' │ CoW Storage   │ 40 GB, bus: scsi, io_uring   │ Not created            │ + CREATE    │
│ Ansible Playbook     │ Configuration │ Roles: common, ib, hpc_tune  │ Pending OS & SSH       │ * TASK      │
└──────────────────────┴───────────────┴──────────────────────────────┴────────────────────────┴─────────────┘

Plan: 6 to create, 0 to update, 0 to destroy, 2 operational task(s), 1 unchanged.
```

---

### 4. Whole-Lifecycle Cluster Startup (`up`)

Orchestrates entire deployment end-to-end: atomic lease locking, virtual media / domain creation, parallel OS installation, SSH readiness polling, and idempotent Ansible configuration:

```bash
# Provision a single node (default HPC BIOS profile)
uv run scc up -n 1

# Provision with no polling timeout (waits indefinitely until install completes)
uv run scc up -n 1 --no-timeout

# Provision using an explicit cluster configuration
uv run scc up -n 1 --cluster configs/clusters/vm-hw-optimized.yaml

# Provision all cluster nodes (1, 2, and 3) in parallel
uv run scc up -a

# Override an active or dead operational lock
uv run scc up -n 1 --force
```

---

### 5. OS Deployment Only (`deploy`)

Provisions operating system images (via Libvirt QEMU/KVM domain creation or HPE iLO virtual media unattended Kickstart) and polls until SSH is available without executing Ansible:

```bash
# Deploy OS on node 1
uv run scc deploy -n 1

# Deploy OS across all 3 nodes concurrently
uv run scc deploy -a
```

---

### 6. Post-Provisioning Configuration & Ansible (`configure`)

Idempotent cluster configuration and verification powered by Ansible:

> [!NOTE]
> The Ansible dynamic inventory ([`ansible/inventory/dynamic_inventory.py`](ansible/inventory/dynamic_inventory.py)) automatically extracts cluster topology, node IPs, roles (`headnode`, `computenode`), usernames, and network parameters directly from `values.yaml` (when present in your working directory) or from `configs/clusters/<cluster>.yaml` (such as `helvetios-hpc.yaml` or `vm-hw-optimized.yaml`). You can also specify an explicit cluster manifest or override file using `--cluster <path>` or the `SCC_CLUSTER_MANIFEST` environment variable.

```bash
# Configure all cluster nodes (hosts, base packages, InfiniBand, RDMA limits)
uv run scc configure

# Configure using a specific cluster profile
uv run scc configure --cluster configs/clusters/vm-hw-optimized.yaml

# Dry-run check mode (preview changes without applying)
uv run scc configure --check

# Target specific nodes
uv run scc configure --limit node1,node2

# Run InfiniBand fabric verification playbook
uv run scc configure -p verify_ib.yaml
```

---

### 7. Power Management & Telemetry (`power`)

Direct bare-metal power operations via BMC Redfish (or Libvirt KVM) without re-provisioning. Multi-node operations run concurrently via `ParallelRunner`:

```bash
# Inspect current hardware power state (all nodes)
uv run scc power status

# Inspect a specific node
uv run scc power status -n 1

# Power on node(s) in parallel and wait until confirmed ON
uv run scc power on -n 1 --wait
uv run scc power on -a -w

# Power off node(s) gracefully and wait until confirmed OFF
uv run scc power off -n 1 --wait
uv run scc power off -a -w

# Force immediate hardware power off
uv run scc power off -n 1 --force -w

# Reboot / restart node(s) in parallel
uv run scc power restart -a -w

# Live power draw telemetry (Current Watts, 20-min avg, Min, Peak, and Cluster Total)
uv run scc power metrics
uv run scc power metrics -n 1

# Stream continuous live power metrics updates
uv run scc power metrics --watch
uv run scc power metrics -w -i 1.0
```

---

### 8. Native SSH Access (`ssh`)

Direct transparent jump through the bastion host to physical or virtual cluster nodes:

```bash
# Open interactive shell on node 1 (default)
uv run scc ssh 1

# Connect to node 2 or node 3
uv run scc ssh 2

# Open interactive shell on the bastion host
uv run scc ssh bastion

# Execute remote command directly
uv run scc ssh 1 "uname -a"
```

---

### 9. BIOS Management (`bios`)

Inspect, backup, and stage workload-optimized BIOS profiles via Redfish:

```bash
# Inspect current BIOS configuration on a node
uv run scc bios show -n 1

# Backup BIOS settings to a JSON file
uv run scc bios backup -n 1 -o bios_backup.json

# Stage a BIOS profile (takes effect on next reboot)
uv run scc bios apply -n 1 -p hpc
uv run scc bios apply -a -p low_latency
```

---

### 10. Operational Lease Locks (`lock`)

Distributed, zero-sudo SSH lease locks prevent team members from issuing conflicting operations on shared physical hardware:

```bash
# List all active and expired locks
uv run scc lock list

# Release / break a specific lock (e.g. node-1, cluster, or all)
uv run scc lock release node-1
uv run scc lock release all
```

---

### 11. Decommissioning & Teardown (`down`)

Ejects virtual media, destroys Libvirt domains, sweeps ephemeral HTTP background processes, and updates cluster state:

```bash
# Decommission a single node
uv run scc down -n 1

# Decommission all cluster nodes concurrently
uv run scc down -a

# Sweep lingering bastion background servers without touching nodes
uv run scc down

# Reset cluster database states back to UNPROVISIONED
uv run scc down -a --reset-db
```

---

### 12. Golden Image & Streaming Pipeline (`image`)

Eliminates repeated 15-minute unattended OS installations by caching golden base images and streaming raw disk blocks:

```bash
# List all cached ISOs, base cloud images, and golden images
uv run scc image list

# Inspect detailed image allocation, virtual size, and format
uv run scc image inspect ~/.cache/scc_carla/images/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2

# Export and compress a QCOW2 golden image to .raw.zst for Bastion HTTP streaming
uv run scc image export --source ~/.cache/scc_carla/golden/golden-rocky-base.qcow2
```

---

## Configuration & Environment Variables

Configuration is loaded from environment variables (prefixed with `SCC_`) or a local `.env` file:

| Setting | Default | Description |
|---|---|---|
| `SCC_TEAM_ID` | `72` | Competition team identifier |
| `SCC_PROVIDER` | `libvirt` | Default node provider (`libvirt`, `helvetios`, `bmc`, `chameleon`) |
| `SCC_CLUSTER` | `vm-standard` | Default named cluster profile in `configs/clusters/` |
| `SCC_CLUSTER_MANIFEST` | *(auto)* | Explicit path to active cluster manifest or `values.yaml` |
| `SCC_BASTION_SSH_HOST` | `scc-bastion` | SSH host alias for bastion gateway |
| `SCC_BASTION_HTTP_IP` | `10.7.12.101` | Bastion internal IP for iLO HTTP serving |
| `SCC_BASTION_HTTP_PORT` | `8072` | Ephemeral HTTP server port on bastion |
| `SCC_BASTION_STATE_DB_PATH` | `~/.config/scc_carla/scc_state.db` | Shared Turso (`pyturso`) DB path on bastion |
| `SCC_NODE_USERNAME` | `scct-2672` | Node OS administrative username |

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
uv run scc cluster validate configs/clusters/vm-standard.yaml
uv run scc cluster validate configs/clusters/vm-hw-optimized.yaml
uv run scc cluster validate configs/clusters/helvetios-hpc.yaml

# Validate Ansible dynamic inventory
uv run ansible-inventory -i ansible/inventory/dynamic_inventory.py --list

# Validate Ansible playbooks syntax
ANSIBLE_CONFIG=ansible/ansible.cfg uv run ansible-playbook -i ansible/inventory/dynamic_inventory.py --syntax-check ansible/playbooks/site.yaml ansible/playbooks/verify_ib.yaml

# Verify execution plan dry-run
uv run scc plan --cluster configs/clusters/vm-standard.yaml
```

---

## Documentation

Comprehensive architecture, hardware, and performance guides:

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
