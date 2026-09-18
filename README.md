# SCC@CARLA 2026 Cluster Management CLI (`scc-carla`)

CLI tool for automated provisioning, Redfish BIOS tuning, SSH access, and distributed lifecycle management for the SCC@CARLA bare-metal cluster.

---

## Quick Start

Ensure dependencies are installed using `uv`:

```bash
uv sync
uv run scc-carla --help
```

---

## Usage Instructions

### 1. Cluster Status

Inspect cluster hardware power, SSH reachability, and lifecycle states:

```bash
# Full live hardware & network probe across all nodes
uv run scc-carla status

# Fast database-only view without probing hardware
uv run scc-carla status --no-probe
```

---

### 2. Node Provisioning (`up`)

Automates BIOS profile configuration, ephemeral HTTP serving, iLO virtual media boot, and unattended Rocky Linux 10.2 minimal installation:

```bash
# Provision a single node (default HPC BIOS profile)
uv run scc-carla up -n 1

# Provision with no polling timeout (waits until install completes)
uv run scc-carla up -n 1 --no-timeout

# Provision with a custom BIOS profile (hpc, baseline, low_latency)
uv run scc-carla up -n 1 -b low_latency

# Provision all cluster nodes (1, 2, and 3)
uv run scc-carla up -a

# Override an active or dead operational lock
uv run scc-carla up -n 1 --force
```

---

### 3. Decommissioning & Teardown (`down`)

Ejects virtual media, powers off nodes, sweeps ephemeral HTTP processes, and updates cluster state:

```bash
# Decommission a single node
uv run scc-carla down -n 1

# Decommission all cluster nodes
uv run scc-carla down -a

# Sweep lingering bastion background servers without touching nodes
uv run scc-carla down

# Reset cluster database states back to UNPROVISIONED
uv run scc-carla down -a --reset-db
```

---

### 4. Power Management (`power`)

Direct bare-metal power operations via BMC Redfish without re-provisioning:

```bash
# Inspect current hardware power state (all nodes)
uv run scc-carla power status

# Inspect a specific node
uv run scc-carla power status -n 1

# Power on node(s) and wait until confirmed ON
uv run scc-carla power on -n 1 --wait
uv run scc-carla power on -a -w

# Power off node(s) gracefully and wait until confirmed OFF
uv run scc-carla power off -n 1 --wait

# Force immediate hardware power off
uv run scc-carla power off -n 1 --force -w

# Reboot / restart node(s) and wait until confirmed ON
uv run scc-carla power restart -n 1 --wait
uv run scc-carla power restart -n 1 --force -w

# Live power draw telemetry (Current Watts, 20-min avg, Min, Peak, and Cluster Total)
uv run scc-carla power metrics
uv run scc-carla power metrics -n 1

# Stream continuous live power metrics updates
uv run scc-carla power metrics --watch
uv run scc-carla power metrics -w -i 1.0
```

---

### 5. SSH Access (`ssh`)

Direct transparent jump through the bastion host to cluster nodes:

```bash
# Open interactive shell on node 1 (default)
uv run scc-carla ssh 1

# Connect to node 2 or node 3
uv run scc-carla ssh 2

# Open interactive shell on the bastion host
uv run scc-carla ssh bastion

# Execute remote command directly
uv run scc-carla ssh 1 "uname -a"
```

---

### 6. BIOS Management (`bios`)

Inspect, backup, and stage BIOS workload profiles via Redfish:

```bash
# Inspect current BIOS configuration on a node
uv run scc-carla bios show -n 1

# Backup BIOS settings to a JSON file
uv run scc-carla bios backup -n 1 -o bios_backup.json

# Stage a BIOS profile (takes effect on next reboot)
uv run scc-carla bios apply -n 1 -p hpc
uv run scc-carla bios apply -a -p low_latency
```

---

### 7. Operational Locks (`lock`)

Distributed locking prevents teammates from running conflicting operations on shared hardware:

```bash
# List all active and expired locks
uv run scc-carla lock list

# Release/break a specific lock (e.g. node-1, cluster, or all)
uv run scc-carla lock release node-1
uv run scc-carla lock release all
```

---

## Configuration

Configuration is loaded from environment variables (prefixed with `SCC_`) or `.env`:

| Setting | Default | Description |
|---|---|---|
| `SCC_TEAM_ID` | `72` | Team number |
| `SCC_BASTION_SSH_HOST` | `scc-bastion` | SSH host alias for bastion |
| `SCC_BASTION_HTTP_IP` | `10.7.12.101` | Bastion internal IP for iLO HTTP |
| `SCC_BASTION_HTTP_PORT` | `8072` | Ephemeral HTTP server port |
| `SCC_BASTION_STATE_DB_PATH` | `~/.config/scc_carla/scc_state.db` | Shared Turso DB path on bastion |
| `SCC_NODE_USERNAME` | `scct-2672` | Node OS administrative username |

---

## Team Members

- Isaac David
- Miguel Aguilar
- Pablo Perez
