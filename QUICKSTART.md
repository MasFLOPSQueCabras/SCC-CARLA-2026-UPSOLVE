# Quickstart Guide: SCC@CARLA 2026

Get your HPC cluster initialized, planned, provisioned, tuned, and configured in minutes.

---

## ⚡ CLI Defaults Reference

When executing commands without explicit flags, `scc-carla` applies the following predictable defaults:

| Operation / Flag | Default Behavior | Overriding Flag |
|---|---|---|
| **Cluster Manifest** (`scc plan`, `validate`) | Searches `./values.yaml` in current working directory &rarr; repo `values.yaml` &rarr; `configs/clusters/vm-standard.yaml` | `-c`, `--cluster <path>` |
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

## 💻 Workflow 1: Local Virtual Cluster (5 Minutes)

Use this workflow to test and develop the full cluster stack on any Linux workstation or CI runner without physical hardware.

### 1. Initialize Local Workspace
Scaffolds a customized `values.yaml` and stages provider templates into `./templates/`:
```bash
# Defaults to: --provider vm --profile standard
uv run scc init
```

### 2. Preview Execution Plan (Diff)
Preview the declarative changes against your local environment (like `terraform plan`):
```bash
# Defaults to: ./values.yaml
uv run scc plan
```
*Outputs table indicating resources to `+ CREATE`, `~ UPDATE`, `* TASK`, or `= NOOP`.*

### 3. Spin Up VMs in Parallel
Provisions all virtual machines, generates Cloud-Init CIDATA FAT drives, creates CoW overlays, and starts domains concurrently:
```bash
# Defaults to: all nodes [1, 2, 3]
uv run scc up
```

### 4. Check Cluster Health & Telemetry
Probes live hypervisor state, SSH accessibility, and Turso state database in parallel:
```bash
uv run scc status
```

### 5. Apply Post-Provisioning Configuration
Executes the full Ansible automation suite (NFS, Spack, HPC kernel tuning, HPL):
```bash
uv run scc configure
```

### 6. Connect to Nodes
```bash
# Defaults to Node 1
uv run scc ssh

# Or connect to a specific node:
uv run scc ssh 2
```

### 7. Clean Teardown
```bash
# Gracefully power off all nodes (preserves disk overlays)
uv run scc down

# Or completely destroy and delete CoW disk images
uv run scc down --purge
```

---

## 🏆 Workflow 2: Bare-Metal Helvetios (Competition Cluster)

Use this workflow on the physical competition cluster (dual Intel Xeon Gold 6140, HPE iLO Redfish, 100G InfiniBand).

### 1. Configure Bastion & Credentials
Ensure `.env` contains your team's BMC credentials and Bastion IP:
```bash
cp .env.example .env
# Edit .env:
# SCC_BMC_USER=admin
# SCC_BMC_PASSWORD=your_password
# SCC_TEAM_ID=72
```

Ensure your `~/.ssh/config` includes the Bastion host alias:
```ssh-config
Host scc-bastion
    HostName 10.7.12.101
    User scct-2672
    IdentityFile ~/.ssh/id_ed25519
```

### 2. Scaffold Helvetios Cluster Definition
```bash
uv run scc init --provider helvetios
```

### 3. Review Execution Plan
```bash
uv run scc plan --cluster configs/clusters/helvetios-hpc.yaml
```

### 4. Apply Redfish Workload BIOS Tuning
Applies the competition HPC BIOS profile (Disables C-states/Hyper-Threading, enables NUMA clustering and Turbo Boost):
```bash
# Defaults to: all nodes [1, 2, 3], profile: hpc
uv run scc bios apply hpc
```

### 5. Launch Automated Bare-Metal Bootstrap
Generates unattended OEMDRV driver disks, starts the ephemeral HTTP Range server on the Bastion, boots via Redfish Virtual Media, and waits for SSH:
```bash
# Defaults to: all nodes [1, 2, 3]
uv run scc up
```

### 6. Configure HPC Stack & InfiniBand
Deploys cluster-wide NFS over 100G InfiniBand, builds Spack environment, and compiles HPL with UCX/OpenMPI:
```bash
uv run scc configure
```

### 7. Run InfiniBand & HPL Residual Verification
```bash
# Run IB verification playbook
uv run scc configure --playbook ansible/playbooks/verify_ib.yaml

# Log into Node 1 and execute HPL benchmark
uv run scc ssh 1
/shared/hpl/run_hpl.sh
```

---

## 📋 CLI Daily Cheat Sheet

| Command | Description | Example Default Invocation | Explicit Targeting |
|---|---|---|---|
| `scc status` | Inspect real-time cluster state & power | `uv run scc status` | `uv run scc status --no-probe` |
| `scc plan` | Terraform-like dry-run diff | `uv run scc plan` | `uv run scc plan -c configs/clusters/vm-hw-optimized.yaml` |
| `scc init` | Author new cluster workspace | `uv run scc init` | `uv run scc init -P helvetios -d ./my-cluster` |
| `scc up` | Provision OS and start nodes | `uv run scc up` | `uv run scc up -n 1 -n 2` |
| `scc down` | Power off or decommission nodes | `uv run scc down` | `uv run scc down -n 3 --purge` |
| `scc power` | Power control via BMC / Libvirt | `uv run scc power on` | `uv run scc power reboot -n 2` |
| `scc bios` | Inspect or apply Redfish BIOS profile | `uv run scc bios status` | `uv run scc bios apply hpc -n 1` |
| `scc configure` | Execute Ansible configuration | `uv run scc configure` | `uv run scc configure --tags infiniband,nfs` |
| `scc ssh` | Interactive terminal to node/bastion | `uv run scc ssh` *(Node 1)* | `uv run scc ssh bastion` |
| `scc lock` | Manage operational lease locks | `uv run scc lock list` | `uv run scc lock release` |
| `scc cluster` | Inspect or validate cluster manifests | `uv run scc cluster list` | `uv run scc cluster validate values.yaml` |
