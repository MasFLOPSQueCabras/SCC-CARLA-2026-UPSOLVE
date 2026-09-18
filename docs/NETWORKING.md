# High-Performance InfiniBand Networking & MPI+UCX Guide

This document provides a comprehensive technical guide to the InfiniBand networking architecture used in the SCC@CARLA compute cluster, detailing the hardware specifications, host-level configuration, the UCX (Unified Communication X) communication framework, and instructions for running high-performance MPI applications over 100 Gbps InfiniBand.

---

## Table of Contents
1. [InfiniBand Architecture & Core Concepts](#1-infiniband-architecture--core-concepts)
2. [Cluster Hardware & NIC Specifications](#2-cluster-hardware--nic-specifications)
3. [Host Setup & Configuration](#3-host-setup--configuration)
4. [Diagnostics & Performance Verification](#4-diagnostics--performance-verification)
5. [MPI & UCX Architecture](#5-mpi--ucx-architecture)
6. [Compiling & Running MPI Applications over InfiniBand](#6-compiling--running-mpi-applications-over-infiniband)
7. [Troubleshooting & Best Practices](#7-troubleshooting--best-practices)

---

## 1. InfiniBand Architecture & Core Concepts

InfiniBand (IB) is a computer network communications standard used in high-performance computing (HPC) and enterprise data centers. Unlike standard Ethernet/TCP/IP networking, InfiniBand was engineered specifically for high throughput, extremely low latency, and minimal CPU utilization.

### Key Characteristics

* **Kernel Bypass (Zero-Copy)**:
  Standard TCP/IP networking requires user applications to copy data into kernel memory space, calculate TCP checksums, process the IP stack via CPU interrupts, and then write to the network adapter. InfiniBand allows user-space applications to read and write directly to network adapter memory registers without involving the operating system kernel or CPU context switches.

* **Remote Direct Memory Access (RDMA)**:
  RDMA allows one compute node to read from or write to the virtual memory of another compute node across the network fabric without interrupting the target node's CPU, cache, or operating system.

* **Hardware Offloading**:
  Transport protocol reliability (packet sequencing, acknowledgments, retransmissions, flow control) is implemented directly in the silicon of the Host Channel Adapter (HCA), rather than in software on the host CPU.

* **Credit-Based Flow Control**:
  InfiniBand uses a link-level credit mechanism ensuring that packets are never dropped due to buffer exhaustion. This eliminates TCP-style packet loss and expensive retransmission timeouts.

### Subnet Components & Addressing

* **Subnet Manager (SM)**: The centralized software entity (running either on an InfiniBand managed switch or on a host via `opensm`) that discovers the network topology, configures routing tables, assigns Local Identifiers (LIDs), and enforces partitioning.
* **GUID (Globally Unique Identifier)**: A permanent, factory-burned 64-bit hardware address (analogous to an Ethernet MAC address).
* **LID (Local Identifier)**: A 16-bit address assigned dynamically by the Subnet Manager to each port within a subnet, used for directing packets through InfiniBand switches.
* **Partition Key (PKey)**: A 16-bit identifier used to segregate traffic into isolated logical domains (analogous to VLANs in Ethernet).
* **IPoIB (IP over InfiniBand)**: An encapsulation protocol that allows standard IP-based applications (like SSH, NFS, HTTP) to communicate over InfiniBand link-layer fabrics using standard Linux network interfaces (`ibs5f0`).

---

## 2. Cluster Hardware & NIC Specifications

Each compute node in the SCC@CARLA cluster is equipped with a high-speed Mellanox ConnectX-5 Host Channel Adapter (HCA).

### Hardware Profile

| Attribute | Value |
| :--- | :--- |
| **Vendor** | Mellanox Technologies (NVIDIA Networking) |
| **Model Family** | MT27800 Family [ConnectX-5] |
| **PCI Device ID** | `0x15b3:0x1017` (Subsystem: `HPE2920111032`) |
| **PCI Slot / Bus** | `0000:5c:00.0` (PCIe Gen3 x16 / Gen4 capable) |
| **Firmware Version** | `12.28.4706` |
| **Port Count** | 2 Physical Ports |
| **Port 1 (Active)** | **100 Gbps EDR** (`Enhanced Data Rate`, 4 lanes @ 25.78125 Gbps) |
| **Port 2 (Standby/Down)**| 100 Gbps capable |
| **Link Layer** | Native InfiniBand |
| **Active MTU** | **4096 bytes** (4K InfiniBand MTU) |
| **RDMA Device Name** | `mlx5_0` (Port: `mlx5_0:1`) |
| **IPoIB Interface** | `ibs5f0` |
| **Assigned IPoIB Subnet** | `10.10.72.0/24` (MTU 2044 or 4092) |

> [!NOTE]
> The servers also feature Intel Ethernet controllers running the `irdma` driver (`irdma0`, `irdma1`). These provide iWARP/RoCE over management Ethernet, whereas `mlx5_0` is the dedicated 100 Gbps InfiniBand interconnect.

### Key Silicon Capabilities of ConnectX-5

1. **Direct Verbs (DevX / `mlx5_dv`)**:
   Provides an ultra-low-latency interface allowing user space to bypass the generic `libibverbs` layer and talk directly to Mellanox-specific microcode and hardware queues.
2. **Dynamically Connected Transport (DCT)**:
   Solves the $O(N^2)$ memory scaling problem of traditional Reliable Connection (RC) queue pairs, allowing thousands of MPI ranks to communicate with full reliability using a fixed memory footprint.
3. **Hardware Tag Matching**:
   Offloads MPI message header inspection and unexpected message matching directly to the NIC hardware, freeing CPU cycles for floating-point calculations.
4. **Adaptive Routing**:
   Dynamic load balancing of packets across multiple paths in the InfiniBand fabric to eliminate network congestion hotspots.

---

## 3. Host Setup & Configuration

To enable InfiniBand and achieve full line-rate performance on Rocky Linux 10, the following system components must be configured.

### 3.1 Kernel Drivers & Modules

The Linux kernel requires the OpenFabrics Alliance (OFA) RDMA subsystem modules. Ensure the following drivers are loaded:

```bash
# Load core RDMA and Mellanox drivers
modprobe mlx5_core
modprobe mlx5_ib
modprobe ib_core
modprobe ib_ipoib
modprobe ib_uverbs

# Verify module loading
lsmod | grep -E "mlx5|ib_"
```

### 3.2 Unlocking Memory Limits (`memlock`)

RDMA requires that memory buffers used for network communication be **pinned** (prevented from being paged out to swap by the Linux virtual memory manager). Without setting `memlock` to `unlimited`, MPI jobs and RDMA operations will abort with `ENOMEM` or registration errors.

Create `/etc/security/limits.d/99-rdma.conf`:

```ini
# /etc/security/limits.d/99-rdma.conf
*          soft    memlock         unlimited
*          hard    memlock         unlimited
*          soft    stack           32768
*          hard    stack           32768
```

Verify active limits as the cluster user:
```bash
ulimit -l
# Expected output: unlimited
```

### 3.3 IPoIB Network Interface Configuration

The IPoIB network interface (`ibs5f0`) is managed via NetworkManager (`nmcli`). It provides high-speed TCP/IP connectivity across nodes (used for NFS shared storage, inter-node SSH, and out-of-band communication).

#### Configuration Commands

```bash
# Set static IP on node1 (replace with .2 for node2, .3 for node3)
nmcli con add type infiniband con-name ibs5f0 ifname ibs5f0 ip4 10.10.72.1/24 autoconnect yes

# Apply manual addressing and ensure automatic connection on boot
nmcli con mod ibs5f0 ipv4.addresses 10.10.72.1/24 ipv4.method manual autoconnect yes

# Bring up the interface
nmcli con up ibs5f0
```

#### Firewall Rules
Assign the InfiniBand interface to firewalld's `trusted` zone so inter-node MPI and NFS traffic are permitted without packet filtering latency:

```bash
firewall-cmd --zone=trusted --add-interface=ibs5f0 --permanent
firewall-cmd --reload
```

---

## 4. Diagnostics & Performance Verification

The following diagnostic commands verify link status, fabric integrity, and performance:

### 4.1 Check Port Physical & Logical State (`ibstat`)

```bash
ibstat mlx5_0
```

Expected output:
```text
CA 'mlx5_0'
	CA type: MT4115
	Number of ports: 2
	Firmware version: 12.28.4706
	Hardware version: 0
	Node GUID: 0x040973ffffcbb8f8
	System image GUID: 0x040973ffffcbb8f8
	Port 1:
		State: Active
		Physical state: LinkUp
		Rate: 100
		Base lid: 39
		LMC: 0
		SM lid: 1
		Capability mask: 0x2651e848
		Port GUID: 0x040973ffffcbb8f8
		Link layer: InfiniBand
```
* **State: Active** confirms that the Subnet Manager has assigned a LID and the port is operational.
* **Rate: 100** confirms 100 Gbps line speed.

### 4.2 Query Verbose Device Information (`ibv_devinfo`)

```bash
ibv_devinfo -d mlx5_0 -i 1
```
Confirms active MTU (`4096`), transport type (`InfiniBand`), and vendor ID (`0x02c9` / `0x15b3`).

### 4.3 Measuring Raw RDMA Performance (`perftest`)

The `perftest` suite measures latency and bandwidth using raw InfiniBand verbs:

#### Bandwidth Benchmark (`ib_write_bw`)
```bash
# On Node 1 (Server):
ib_write_bw -d mlx5_0 -i 1 -F

# On Node 2 (Client):
ib_write_bw -d mlx5_0 -i 1 -F 10.10.72.1
```
* **Expected Result**: ~11,800 to 12,100 MB/s (~96-98 Gbps sustained).

#### Latency Benchmark (`ib_write_lat`)
```bash
# On Node 1 (Server):
ib_write_lat -d mlx5_0 -i 1

# On Node 2 (Client):
ib_write_lat -d mlx5_0 -i 1 10.10.72.1
```
* **Expected Result**: ~0.7 to 0.9 microseconds ($\mu s$).

---

## 5. MPI & UCX Architecture

### 5.1 What is UCX (Unified Communication X)?

UCX is an open-source, production-grade communication framework developed by Mellanox/NVIDIA, national labs, and universities. It serves as the primary communication engine under modern OpenMPI (v4 and v5) and MPICH implementations.

```
+-------------------------------------------------------------+
|                      User Application                       |
|           (HPL, Quantum ESPRESSO, OpenFOAM, etc.)           |
+-------------------------------------------------------------+
                              |
+-------------------------------------------------------------+
|                     MPI Interface Layer                     |
|           (MPI_Send, MPI_Recv, MPI_Bcast, MPI_Allreduce)    |
+-------------------------------------------------------------+
                              |
+-------------------------------------------------------------+
|                 OpenMPI PML (pml_ucx.so)                    |
+-------------------------------------------------------------+
                              |
+-------------------------------------------------------------+
|                 UCP (UCX Protocols Layer)                   |
|   • Dynamic Protocol Selection (Eager, Short, Rendezvous)   |
|   • Software Tag Matching & Data Packing                    |
+-------------------------------------------------------------+
                              |
+-------------------------------------------------------------+
|                 UCT (UCX Transports Layer)                  |
|  +--------------------+  +-------------------------------+  |
|  |     rc_mlx5        |  |            sm / sysv          |  |
|  | Mellanox DevX RDMA |  | Intra-node Shared Memory      |  |
|  | (Direct Hardware)  |  | (15+ GB/s, 80 ns latency)     |  |
|  +--------------------+  +-------------------------------+  |
+-------------------------------------------------------------+
                              |
+-------------------------------------------------------------+
|              ConnectX-5 HCA / PCIe Subsystem                |
+-------------------------------------------------------------+
```

### 5.2 UCX Layers

1. **UCP (Protocols)**: High-level API for point-to-point communication, tag matching, and non-blocking collectives. It dynamically decides whether to use:
   * **Short Protocol**: Inlines tiny data directly into network descriptors (< 128 bytes).
   * **Eager Protocol**: Sends data immediately to pre-allocated buffers on receiver (low latency for small messages).
   * **Rendezvous Protocol**: Performs zero-copy RDMA Read/Write directly between user buffers for large data arrays (highest throughput, zero CPU involvement).
2. **UCT (Transports)**: Low-level hardware drivers. In this cluster:
   * `rc_mlx5`: Mellanox Direct Verbs reliable connected transport.
   * `rc_verbs`: Standard OpenFabrics reliable connected transport.
   * `sm` / `sysv`: High-speed shared memory for ranks executing on the same socket/server.
   * `self`: Local process loopback.
3. **UCS (Services)**: Underlying infrastructure providing memory pools, callbacks, async event loops, and data structures.

### 5.3 Verifying Active UCX Transports

To verify which hardware transports are compiled and detected by UCX:

```bash
# Load Spack environment
source /shared/spack/share/spack/setup-env.sh
spack load ucx

# Inspect available devices and bandwidth
ucx_info -d
```

Output confirming hardware acceleration over `mlx5_0:1`:
```text
#      Transport: rc_verbs
#         Device: mlx5_0:1
#           Type: network
#      capabilities:
#            bandwidth: 11794.23/ppn + 0.00 MB/sec
#
#      Transport: rc_mlx5
#         Device: mlx5_0:1
#           Type: network
#      capabilities:
#            bandwidth: 11794.23/ppn + 0.00 MB/sec
```

---

## 6. Compiling & Running MPI Applications over InfiniBand

### 6.1 Compiling an MPI Program

Use `mpicc` (provided by OpenMPI compiled against UCX):

```bash
# Load OpenMPI environment
source /shared/spack/share/spack/setup-env.sh
spack load openmpi

# Compile C code with optimization flags for Skylake-SP
mpicc -O3 -march=skylake-avx512 my_mpi_code.c -o my_mpi_code
```

### 6.2 Hostfile Definition

Create a hostfile (e.g. `hosts.txt`) specifying the InfiniBand interfaces and core slots:

```text
# /shared/hpl/hosts.txt
10.10.72.1:36
10.10.72.2:36
```
*(Each node provides 36 physical CPU cores across 2 sockets).*

### 6.3 Standard Execution Command

Run MPI programs directly without SLURM by launching `mpirun`:

```bash
#!/bin/bash
set -euo pipefail

# 1. Source Spack environment
source /shared/spack/share/spack/setup-env.sh
spack load openmpi hpl

# 2. Configure Environment for ConnectX-5 & Skylake NUMA
export OMP_NUM_THREADS=1
export UCX_NET_DEVICES=mlx5_0:1
export UCX_TLS=rc,sm,self

# 3. Launch with optimal process binding
mpirun -np 72 \
    --hostfile /shared/hpl/hosts.txt \
    --bind-to core \
    --map-by core \
    -x PATH \
    -x LD_LIBRARY_PATH \
    -x OMP_NUM_THREADS \
    -x UCX_NET_DEVICES \
    -x UCX_TLS \
    --mca pml ucx \
    /shared/hpl/bin/xhpl
```

### 6.4 Critical `mpirun` Flags Explained

* `--bind-to core`: Binds each MPI rank to a distinct physical CPU core, preventing the OS scheduler from migrating threads between NUMA nodes and invalidating L1/L2 caches.
* `--map-by core`: Sequentially assigns ranks across cores to optimize hardware locality.
* `-x UCX_NET_DEVICES=mlx5_0:1`: Instructs UCX to strictly use the active 100G Mellanox InfiniBand port, preventing it from binding to Ethernet or down ports.
* `-x UCX_TLS=rc,sm,self`: Forces UCX to use **RC** (InfiniBand Reliable Connection) for inter-node communication and **SM** (Shared Memory) for intra-node communication.
* `--mca pml ucx`: Explicitly selects the UCX Point-to-Point Management Layer in OpenMPI.

---

## 7. Troubleshooting & Best Practices

### Common Errors & Solutions

| Error | Root Cause | Fix |
| :--- | :--- | :--- |
| `UCX WARN transport 'rc' is not available` | UCX was compiled without `+rc +ud +dc` variants in Spack. | Rebuild UCX with `spack install ucx +rc +ud +dc +verbs +mlx5_dv`. |
| `network device 'mlx5_0:1' is not available` | Interface down, wrong device name, or missing permissions on `/dev/infiniband/uverbs*`. | Check `ibstat mlx5_0`, verify `ls -la /dev/infiniband/`, ensure driver is loaded. |
| `Cannot allocate memory` / `ibv_reg_mr() failed` | Locked memory limit (`memlock`) is insufficient. | Set `* soft memlock unlimited` and `* hard memlock unlimited` in `/etc/security/limits.d/99-rdma.conf`. |
| `error while loading shared libraries: lib*.so` | Dynamic linker cannot locate Spack libraries on client nodes. | Ensure `/home/shared -> /shared` symlink exists so RPATHs match on all nodes, and pass `-x LD_LIBRARY_PATH`. |
| `No route to host` on `10.10.72.X` | IPoIB interface not activated or firewalld blocking traffic. | Run `nmcli con up ibs5f0` and ensure `ibs5f0` is in firewalld's `trusted` zone. |
