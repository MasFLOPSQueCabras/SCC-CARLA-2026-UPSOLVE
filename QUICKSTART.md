# Quickstart Guide: SCC@CARLA 2026

Get your HPC cluster initialized, planned, provisioned, tuned, and configured in minutes.

> [!TIP]
> Operating on the physical competition cluster? See the dedicated **[Bare-Metal Helvetios Competition Runbook & Troubleshooting Manual](docs/QUICKSTART_CABRITA_CARLA2026.md)**.

---

## ⚡ CLI Defaults Reference

When executing commands without explicit flags, `cabrita` applies the following predictable defaults:

| Operation / Flag | Default Behavior | Overriding Flag |
|---|---|---|
| **Execution & Teardown Plan** (`cabrita up`, `down`) | **Renders plan diff & prompts for explicit confirmation** | `--yes` / `-y` (auto-approve), `--dry-run` (preview diff only) |
| **Cluster Manifest** (`cabrita up`, `validate`) | Searches `./values.yaml` in current working directory &rarr; repo `values.yaml` &rarr; `configs/clusters/vm-standard.yaml` | `-c`, `--cluster <path>` |
| **Target Nodes** (`cabrita up`, `down`, `status`, `power`) | **All 3 nodes: `[1, 2, 3]`** | `-n`, `--node <id>` (e.g. `-n 1`, `-n 1 -n 2`) |
| **SSH Target** (`cabrita ssh`) | **Node 1** (`node1` at `10.2.72.1` / `192.168.122.101`) | `<target>` (e.g. `cabrita ssh 2`, `cabrita ssh bastion`) |
| **Provider** (`cabrita init`) | **`vm`** (Local KVM/QEMU via Libvirt) | `-P`, `--provider <vm\|helvetios>` |
| **Cluster Profile** (`cabrita init`) | **`standard`** (`vm-standard.yaml` / SeaBIOS / VirtIO) | `-p`, `--profile <standard\|hw-optimized>` |
| **BIOS Tuning** (`cabrita up`, `bios apply`) | **`hpc`** (Max performance, NUMA on, Hyper-Threading off) | `--bios-profile <hpc\|balanced\|debug>` |
| **Teardown Mode** (`cabrita down`) | **Graceful shutdown** (preserves CoW disks and NVMe state) | `--purge` (destroys VM disks / wipes state) |

---

## 🛠️ Prerequisites (30 Seconds)

Ensure `uv` and Python 3.14+ are available:

```bash
# Clone the repository
git clone git@github.com:MasFLOPSQueCabras/SCC-CARLA-2026-UPSOLVE.git
cd SCC-CARLA-2026-UPSOLVE

# Install and sync workspace dependencies
uv sync --all-packages

# Verify CLI is ready
uv run cabrita --help
```

> [!TIP]
> You can use either `uv run cabrita <cmd>` or `uv run cabrita <cmd>` interchangeably.

---

## 💻 Local Virtual Cluster Quickstart (5 Minutes)

Use this workflow to test and develop the full cluster stack on any Linux workstation or CI runner without physical hardware.

### 1. Initialize Local Workspace
Scaffolds a customized `values.yaml` and stages provider templates into `./templates/`:
```bash
# Defaults to: --provider vm --profile standard
uv run cabrita init
```

### 2. Preview Plan & Spin Up VMs
`cabrita up` automatically renders a Terraform-like declarative diff table showing what resources will be created (`+ CREATE`), updated (`~ UPDATE`), or performed (`* TASK`), and requests explicit confirmation:
```bash
# Preview plan diff and prompt for confirmation:
uv run cabrita up

# Or preview diff without making any changes:
uv run cabrita up --dry-run

# Or run non-interactively (ideal for scripts & CI):
uv run cabrita up --yes
```

### 3. Check Cluster Health & Telemetry
Probes live hypervisor state, SSH accessibility, and Turso state database in parallel:
```bash
uv run cabrita status
```

### 4. Apply Post-Provisioning Configuration
Executes the full Ansible automation suite (NFS, Spack, HPC kernel tuning, HPL):
```bash
uv run cabrita configure
```

### 5. Connect to Nodes
```bash
# Defaults to Node 1
uv run cabrita ssh

# Or connect to a specific node:
uv run cabrita ssh 2
```

### 6. Clean Teardown with Plan Diff
`cabrita down` computes the teardown impact, shows which nodes will stop and which disk overlays will be discarded, and prompts for confirmation:
```bash
# Preview teardown plan and prompt:
uv run cabrita down

# Preview teardown diff only:
uv run cabrita down --dry-run

# Auto-approve teardown and delete CoW disk images:
uv run cabrita down --purge --yes
```

---

## ⚡ Zero-Install Teardowns (Golden Image Pipeline)

In testing and competitions, reinstalling from the minimal bootable ISO via Anaconda takes **10–15 minutes per node**. To eliminate this bottleneck, `cabrita` implements a **Golden Image & Streaming Pipeline**:

### 1. How It Works
- **First Run**: Install the OS once from the minimal bootable ISO (or automated via Libvirt).
- **Subsequent Teardowns**:
  - **Local Libvirt**: When you run `cabrita down --purge`, only the ephemeral child CoW overlay (`nodeX.qcow2`) is discarded. On `cabrita up`, a fresh CoW overlay is created on top of `golden-rocky-base.qcow2` in **< 2 seconds**. Zero package re-installations!
  - **Bare-Metal Streaming**: An optimized raw compressed block image (`golden-rocky-base.raw.zst`, ~1.1 GB) can be streamed directly to block devices (`curl | zstd -d | dd of=/dev/target`) via HTTP in **30–45 seconds**, completely bypassing sequential RPM package installation.

### 2. Image Management Commands
```bash
# List cached ISOs, base cloud images, and golden images
uv run cabrita image list

# Inspect detailed image virtual size, allocation, and format
uv run cabrita image inspect ~/.cache/cabrita/images/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2

# Export QCOW2 golden image to compressed raw stream for Bastion HTTP server
uv run cabrita image export --source ~/.cache/cabrita/golden/golden-rocky-base.qcow2
```

---

## 📋 CLI Daily Cheat Sheet

| Command | Description | Example Invocation | Explicit Targeting |
|---|---|---|---|
| `cabrita status` | Inspect real-time cluster state & power | `uv run cabrita status` | `uv run cabrita status --no-probe` |
| `cabrita up` | Plan & provision cluster nodes | `uv run cabrita up` *(interactive)* | `uv run cabrita up --yes -n 1 -n 2` |
| `cabrita up --dry-run` | Preview execution plan diff only | `uv run cabrita up --dry-run` | `uv run cabrita up --dry-run -c configs/clusters/vm-hw-optimized.yaml` |
| `cabrita down` | Plan & decommission cluster nodes | `uv run cabrita down` *(interactive)* | `uv run cabrita down --purge --yes -n 3` |
| `cabrita down --dry-run`| Preview teardown plan diff only | `uv run cabrita down --dry-run` | `uv run cabrita down --dry-run --reset-db` |
| `cabrita init` | Author new cluster workspace | `uv run cabrita init` | `uv run cabrita init -p hw-optimized -d ./my-cluster` |
| `cabrita image` | Manage golden images & streaming | `uv run cabrita image list` | `uv run cabrita image export -s image.qcow2` |
| `cabrita power` | Power control via BMC / Libvirt | `uv run cabrita power on` | `uv run cabrita power reboot -n 2` |
| `cabrita bios` | Inspect or apply Redfish BIOS profile | `uv run cabrita bios status` | `uv run cabrita bios apply hpc -n 1` |
| `cabrita configure` | Execute Ansible configuration | `uv run cabrita configure` | `uv run cabrita configure --tags infiniband,nfs` |
| `cabrita ssh` | Interactive terminal to node/bastion | `uv run cabrita ssh` *(Node 1)* | `uv run cabrita ssh bastion` |
| `cabrita lock` | Manage operational lease locks | `uv run cabrita lock list` | `uv run cabrita lock release` |
| `cabrita cluster` | Inspect or validate cluster manifests | `uv run cabrita cluster list` | `uv run cabrita cluster validate values.yaml` |
