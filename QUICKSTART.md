# Quickstart Guide: SCC@CARLA 2026

Get your HPC cluster initialized, planned, provisioned, tuned, and configured in minutes.

> [!TIP]
> Operating on the physical competition cluster? See the dedicated **[Bare-Metal Helvetios Competition Runbook & Troubleshooting Manual](docs/QUICKSTART_SCC_CARLA2026.md)**.

---

## ⚡ CLI Defaults Reference

When executing commands without explicit flags, `cabrita` applies the following predictable defaults:

| Operation / Flag | Default Behavior | Overriding Flag |
|---|---|---|
| **Execution & Teardown Plan** (`cabrita up`, `down`) | **Renders plan diff & prompts for explicit confirmation** | `--yes` / `-y` (auto-approve), `--dry-run` (preview diff only) |
| **Cluster Manifest** (`cabrita up`, `validate`) | `./cluster.yaml`; artifact and template paths resolve relative to the manifest | `-c`, `--cluster <path>` |
| **Target Nodes** (`cabrita up`, `down`, `status`, `power`) | All nodes declared in the manifest | Repeat `-n`, `--node <id>` to select declared IDs |
| **SSH Target** (`cabrita ssh`) | Select a declared node | `--node <id>` |
| **Provider** (`cabrita init`) | `libvirt` (local KVM/QEMU) | `--provider <libvirt\|helvetios>` |
| **Shutdown** (`cabrita down`) | Stops nodes and preserves disks | Use `cabrita destroy` to remove managed virtual resources |

---

## 🛠️ Prerequisites (30 Seconds)

Ensure `uv` and Python 3.14+ are available:

```bash
# Clone the repository
git clone https://github.com/MasFLOPSQueCabras/cabrita.git
cd cabrita

# Install and sync workspace dependencies
uv sync --all-extras

# Verify CLI is ready
uv run cabrita --help
```

---

## 💻 Local Virtual Cluster Quickstart (5 Minutes)

Use this workflow to test and develop the full cluster stack on any Linux workstation or CI runner without physical hardware.

### 1. Initialize Local Workspace
Scaffolds a customized `cluster.yaml` and stages provider templates into `./templates/`:
```bash
# Defaults to: --provider libvirt
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
uv run cabrita destroy --yes
```

---

## ⚡ Zero-Install Teardowns (Golden Image Pipeline)

Capture a configured, shut-down libvirt node into an independent disk and a
checksum-verified recovery payload. A restore ISO fetches that payload over HTTP,
applies each node's identity, then boots from disk without reinstalling packages.

```bash
cabrita down --cluster cluster.yaml --node 1 --yes
cabrita image capture --cluster cluster.yaml --node 1 --output ./golden
cabrita image inspect ./golden/golden.json
```

See [golden recovery](docs/golden-recovery.md) for manifest inputs, prerequisites,
and the separate libvirt and Helvetios verification status.

---

## 📋 CLI Daily Cheat Sheet

| Command | Description | Example Invocation | Explicit Targeting |
|---|---|---|---|
| `cabrita status` | Inspect real-time cluster state & power | `uv run cabrita status` | `uv run cabrita status --no-probe` |
| `cabrita up` | Plan & provision cluster nodes | `uv run cabrita up` *(interactive)* | `uv run cabrita up --yes -n 1 -n 2` |
| `cabrita up --dry-run` | Preview execution plan diff only | `uv run cabrita up --dry-run` | `uv run cabrita up --dry-run -c configs/clusters/vm-hw-optimized.yaml` |
| `cabrita down` | Plan & decommission cluster nodes | `uv run cabrita down` *(interactive)* | `uv run cabrita destroy --yes -n 3` |
| `cabrita down --dry-run`| Preview teardown plan diff only | `uv run cabrita down --dry-run` | `uv run cabrita down --dry-run --reset-db` |
| `cabrita init` | Author new cluster workspace | `uv run cabrita init` | `uv run cabrita init -p hw-optimized -d ./my-cluster` |
| `cabrita image` | Manage golden images & streaming | `uv run cabrita image list` | `cabrita image capture --node 1 --output ./golden` |
| `cabrita power` | Power control via BMC / Libvirt | `uv run cabrita power on` | `uv run cabrita power reboot -n 2` |
| `cabrita bios` | Inspect or apply Redfish BIOS profile | `uv run cabrita bios status` | `uv run cabrita bios apply hpc -n 1` |
| `cabrita configure` | Execute Ansible configuration | `uv run cabrita configure` | `uv run cabrita configure --tags infiniband,nfs` |
| `cabrita ssh` | Interactive terminal to node/bastion | `uv run cabrita ssh` *(Node 1)* | `uv run cabrita ssh bastion` |
| `cabrita lock` | Manage operational lease locks | `uv run cabrita lock list` | `uv run cabrita lock release` |
| `cabrita cluster` | Inspect or validate cluster manifests | `uv run cabrita cluster list` | `uv run cabrita cluster validate cluster.yaml` |
