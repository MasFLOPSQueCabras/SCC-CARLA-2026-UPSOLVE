# Quickstart Guide: SCC@CARLA 2026

Get your HPC cluster initialized, planned, provisioned, tuned, and configured in minutes.

> [!TIP]
> Operating on the physical competition cluster? See the dedicated **[Bare-Metal Helvetios Competition Runbook & Troubleshooting Manual](docs/QUICKSTART_SCC_CARLA2026.md)**.

---

## ⚡ CLI Defaults Reference

When executing commands without explicit flags, `scc-carla` applies the following predictable defaults:

| Operation / Flag | Default Behavior | Overriding Flag |
|---|---|---|
| **Execution & Teardown Plan** (`scc up`, `down`) | **Renders plan diff & prompts for explicit confirmation** | `--yes` / `-y` (auto-approve), `--dry-run` (preview diff only) |
| **Cluster Manifest** (`scc up`, `validate`) | Searches `./values.yaml` in current working directory &rarr; repo `values.yaml` &rarr; `configs/clusters/vm-standard.yaml` | `-c`, `--cluster <path>` |
| **Target Nodes** (`scc up`, `down`, `status`, `power`) | **All 3 nodes: `[1, 2, 3]`** | `-n`, `--node <id>` (e.g. `-n 1`, `-n 1 -n 2`) |
| **SSH Target** (`scc ssh`) | **Node 1** (`node1` at `10.2.72.1` / `192.168.122.101`) | `<target>` (e.g. `scc ssh 2`, `scc ssh bastion`) |
| **Provider** (`scc init`) | **`vm`** (Local KVM/QEMU via Libvirt) | `-P`, `--provider <vm\|helvetios>` |
| **Cluster Profile** (`scc init`) | **`standard`** (`vm-standard.yaml` / SeaBIOS / VirtIO) | `-p`, `--profile <standard\|hw-optimized>` |
| **BIOS Tuning** (`scc up`, `bios apply`) | **`hpc`** (Max performance, NUMA on, Hyper-Threading off) | `--bios-profile <hpc\|balanced\|debug>` |
| **Teardown Mode** (`scc down`) | **Graceful shutdown** (preserves CoW disks and NVMe state) | `--purge` (destroys VM disks / wipes state) |

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
uv run scc --help
```

> [!TIP]
> You can use either `uv run scc <cmd>` or `uv run scc-carla <cmd>` interchangeably.

---

## 💻 Local Virtual Cluster Quickstart (5 Minutes)

Use this workflow to test and develop the full cluster stack on any Linux workstation or CI runner without physical hardware.

### 1. Initialize Local Workspace
Scaffolds a customized `values.yaml` and stages provider templates into `./templates/`:
```bash
# Defaults to: --provider vm --profile standard
uv run scc init
```

### 2. Preview Plan & Spin Up VMs
`scc up` automatically renders a Terraform-like declarative diff table showing what resources will be created (`+ CREATE`), updated (`~ UPDATE`), or performed (`* TASK`), and requests explicit confirmation:
```bash
# Preview plan diff and prompt for confirmation:
uv run scc up

# Or preview diff without making any changes:
uv run scc up --dry-run

# Or run non-interactively (ideal for scripts & CI):
uv run scc up --yes
```

### 3. Check Cluster Health & Telemetry
Probes live hypervisor state, SSH accessibility, and Turso state database in parallel:
```bash
uv run scc status
```

### 4. Apply Post-Provisioning Configuration
Executes the full Ansible automation suite (NFS, Spack, HPC kernel tuning, HPL):
```bash
uv run scc configure
```

### 5. Connect to Nodes
```bash
# Defaults to Node 1
uv run scc ssh

# Or connect to a specific node:
uv run scc ssh 2
```

### 6. Clean Teardown with Plan Diff
`scc down` computes the teardown impact, shows which nodes will stop and which disk overlays will be discarded, and prompts for confirmation:
```bash
# Preview teardown plan and prompt:
uv run scc down

# Preview teardown diff only:
uv run scc down --dry-run

# Auto-approve teardown and delete CoW disk images:
uv run scc down --purge --yes
```

---

## ⚡ Zero-Install Teardowns (Golden Image Pipeline)

In testing and competitions, reinstalling from the minimal bootable ISO via Anaconda takes **10–15 minutes per node**. To eliminate this bottleneck, `scc-carla` implements a **Golden Image & Streaming Pipeline**:

### 1. How It Works
- **First Run**: Install the OS once from the minimal bootable ISO (or automated via Libvirt).
- **Subsequent Teardowns**:
  - **Local Libvirt**: When you run `scc down --purge`, only the ephemeral child CoW overlay (`nodeX.qcow2`) is discarded. On `scc up`, a fresh CoW overlay is created on top of `golden-rocky-base.qcow2` in **< 2 seconds**. Zero package re-installations!
  - **Bare-Metal Streaming**: An optimized raw compressed block image (`golden-rocky-base.raw.zst`, ~1.1 GB) can be streamed directly to block devices (`curl | zstd -d | dd of=/dev/target`) via HTTP in **30–45 seconds**, completely bypassing sequential RPM package installation.

### 2. Image Management Commands
```bash
# List cached ISOs, base cloud images, and golden images
uv run scc image list

# Inspect detailed image virtual size, allocation, and format
uv run scc image inspect ~/.cache/scc_carla/images/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2

# Export QCOW2 golden image to compressed raw stream for Bastion HTTP server
uv run scc image export --source ~/.cache/scc_carla/golden/golden-rocky-base.qcow2
```

---

## 📋 CLI Daily Cheat Sheet

| Command | Description | Example Invocation | Explicit Targeting |
|---|---|---|---|
| `scc status` | Inspect real-time cluster state & power | `uv run scc status` | `uv run scc status --no-probe` |
| `scc up` | Plan & provision cluster nodes | `uv run scc up` *(interactive)* | `uv run scc up --yes -n 1 -n 2` |
| `scc up --dry-run` | Preview execution plan diff only | `uv run scc up --dry-run` | `uv run scc up --dry-run -c configs/clusters/vm-hw-optimized.yaml` |
| `scc down` | Plan & decommission cluster nodes | `uv run scc down` *(interactive)* | `uv run scc down --purge --yes -n 3` |
| `scc down --dry-run`| Preview teardown plan diff only | `uv run scc down --dry-run` | `uv run scc down --dry-run --reset-db` |
| `scc init` | Author new cluster workspace | `uv run scc init` | `uv run scc init -p hw-optimized -d ./my-cluster` |
| `scc image` | Manage golden images & streaming | `uv run scc image list` | `uv run scc image export -s image.qcow2` |
| `scc power` | Power control via BMC / Libvirt | `uv run scc power on` | `uv run scc power reboot -n 2` |
| `scc bios` | Inspect or apply Redfish BIOS profile | `uv run scc bios status` | `uv run scc bios apply hpc -n 1` |
| `scc configure` | Execute Ansible configuration | `uv run scc configure` | `uv run scc configure --tags infiniband,nfs` |
| `scc ssh` | Interactive terminal to node/bastion | `uv run scc ssh` *(Node 1)* | `uv run scc ssh bastion` |
| `scc lock` | Manage operational lease locks | `uv run scc lock list` | `uv run scc lock release` |
| `scc cluster` | Inspect or validate cluster manifests | `uv run scc cluster list` | `uv run scc cluster validate values.yaml` |
