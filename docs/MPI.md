# MPI & UCX Architecture, Configuration & Tuning Guide

This document details the configuration, internal architecture, and runtime optimization of the **Message Passing Interface (MPI)** and **Unified Communication X (UCX)** framework deployed on the SCC@CARLA compute cluster.

---

## Table of Contents
1. [OpenMPI Modular Component Architecture (MCA)](#1-openmpi-modular-component-architecture-mca)
2. [Point-to-Point Management: Why PML UCX?](#2-point-to-point-management-why-pml-ucx)
3. [UCX Hardware Transport Engine](#3-ucx-hardware-transport-engine)
4. [Process Binding & Hardware Affinity](#4-process-binding--hardware-affinity)
5. [Cluster Configuration & Compilation Details](#5-cluster-configuration--compilation-details)
6. [Runtime Parameters & Execution Command Reference](#6-runtime-parameters--execution-command-reference)
7. [Diagnostics & Performance Verification](#7-diagnostics--performance-verification)

---

## 1. OpenMPI Modular Component Architecture (MCA)

OpenMPI is designed as an extensible collection of independent components organized into functional frameworks called the **Modular Component Architecture (MCA)**.

```
+-----------------------------------------------------------------------------+
|                          MPI Application Code                               |
+-----------------------------------------------------------------------------+
                                      |
+-----------------------------------------------------------------------------+
|                        OpenMPI MCA Core Framework                           |
|                                                                             |
|  +--------------------+  +--------------------+  +--------------------+     |
|  |     PML Layer      |  |     COLL Layer     |  |     OSC Layer      |     |
|  | (Point-to-Point    |  | (Collectives:      |  | (One-Sided RMA:    |     |
|  |  Management: ucx)  |  |  Bcast, Reduce)    |  |  ucx, pt2pt)       |     |
|  +--------------------+  +--------------------+  +--------------------+     |
|            |                       |                       |                |
|  +--------------------------------------------------------------------+     |
|  |                         UCP / UCT Engine                           |     |
|  |                 (Unified Communication Services)                   |     |
|  +--------------------------------------------------------------------+     |
+-----------------------------------------------------------------------------+
                                      |
                 +--------------------+--------------------+
                 |                                         |
    [Inter-Node Communication]                [Intra-Node Communication]
       Mellanox ConnectX-5                        CPU Shared Memory
       100G InfiniBand RDMA                       sysv / posix / knem
     (rc_mlx5 / rc_verbs: 12 GB/s)              (sm: 15 GB/s, 80 ns latency)
```

### Key MCA Frameworks in OpenMPI 5.x
* **PML (Point-to-Point Management Layer)**: Handles message protocols (eager, rendezvous), fragmentation, and hardware transport selection.
* **COLL (Collective Subsystem)**: Implements optimized group communication algorithms (`MPI_Bcast`, `MPI_Reduce`, `MPI_Alltoall`).
* **OSC (One-Sided Communications)**: Implements RMA memory windows (`MPI_Put`, `MPI_Get`).
* **PRTE (PMIx Reference Run-Time Environment)**: Manages distributed process spawning, daemon launch via SSH, and out-of-band wiring without requiring SLURM.

---

## 2. Point-to-Point Management: Why PML UCX?

In earlier OpenMPI releases (v1.x–v3.x), InfiniBand was handled via the `openib` Byte Transfer Layer (BTL). 

### Limitations of Legacy `openib` BTL
1. **Inefficient Kernel Traps**: Handled connection setup and memory registrations with high CPU overhead.
2. **Poor Scaling**: Required establishing a dedicated Queue Pair (QP) between every pair of processes ($O(N^2)$ memory scaling).
3. **No Direct Hardware Optimization**: Could not leverage Mellanox-specific silicon offloads (such as DevX Direct Verbs).

### Advantages of PML UCX
In our OpenMPI 5.0.5 stack, all point-to-point operations are routed through **PML UCX** (`--mca pml ucx`):
* **Zero Overhead**: Directly invokes the optimized UCX protocol layer.
* **Dynamic Protocol Adaptation**: Automatically toggles between Short, Eager, and Rendezvous protocols based on payload size.
* **Zero-Copy RDMA**: Large messages bypass user buffers and OS kernel space completely, reading/writing across nodes directly between physical memory pages.
* **Tag Matching Offload**: Hardware-level message filtering on the Mellanox ConnectX-5 adapter.

---

## 3. UCX Hardware Transport Engine

UCX is structured into three internal sub-layers:

### 3.1 UCS (Unified Communication Services)
Underlying foundational services providing memory allocation pools, spinlocks, CPU time measurement, and async signal handling.

### 3.2 UCP (Unified Communication Protocols)
Implements high-level protocols for MPI primitives:
* **Short Protocol** ($\le 128 \text{ bytes}$): Payload is packed directly inside the transport header/descriptor, delivering minimum latency.
* **Eager Protocol** ($128 \text{ bytes} \dots 64 \text{ KB}$): Message is immediately transmitted to pre-allocated bounce buffers on the target node.
* **Rendezvous Protocol** ($> 64 \text{ KB}$): Sender transmits a request; receiver pins its destination buffer and issues an RDMA Read or grants an RDMA Write. Data transfers directly at 100 Gbps line rate without CPU buffering.

### 3.3 UCT (Unified Communication Transports)
Low-level drivers that interface directly with hardware:

| UCT Transport | Medium | Latency | Bandwidth | Usage in Cluster |
| :--- | :--- | :--- | :--- | :--- |
| **`rc_mlx5`** | 100G InfiniBand | **~0.75 $\mu s$** | **11,794 MB/s** | **Primary inter-node RDMA** (Mellanox Direct Verbs bypasses `libibverbs`). |
| **`rc_verbs`** | 100G InfiniBand | ~0.85 $\mu s$ | 11,794 MB/s | Secondary inter-node fallback via standard OpenFabrics verbs. |
| **`sm` / `sysv`** | Host RAM | **~0.08 $\mu s$ (80 ns)** | **15,360 MB/s** | **Intra-node communication** between ranks sharing the same server. |
| **`self`** | Host CPU Cache | 0 ns | 19,360 MB/s | Intra-rank loopback communication. |
| **`tcp`** | Ethernet / IPoIB | ~5.2 $\mu s$ | ~2,200 MB/s | Fallback management transport. |

---

## 4. Process Binding & Hardware Affinity

Each node features a dual-socket NUMA architecture with 18 cores per socket (36 physical cores total).

```
                      NUMA Node 0                              NUMA Node 1
               Socket 0 (Cores 0-17)                    Socket 1 (Cores 18-35)
          +-------------------------------+        +-------------------------------+
          | Rank 0  | Rank 1  | ...       |  UPI   | Rank 18 | Rank 19 | ...       |
          | Core 0  | Core 1  | Core 17   |<------->| Core 18 | Core 19 | Core 35   |
          | (L1/L2) | (L1/L2) | (L1/L2)   |        | (L1/L2) | (L1/L2) | (L1/L2)   |
          +-------------------------------+        +-------------------------------+
          |      L3 Cache: 24.75 MB       |        |      L3 Cache: 24.75 MB       |
          +-------------------------------+        +-------------------------------+
          |       96 GB Local RAM         |        |       96 GB Local RAM         |
          +-------------------------------+        +-------------------------------+
                         |
                 PCIe Gen3 x16 Bus
                         |
          +-------------------------------+
          | Mellanox ConnectX-5 (mlx5_0)  |
          +-------------------------------+
```

### Why Process Binding Matters
1. **Cache Locality**: If an OS thread migrates from Core 0 to Core 1, its hot L1 (32 KB) and L2 (1 MB) caches are completely lost.
2. **NUMA Penalties**: If an MPI rank pinned to Socket 1 accesses memory allocated on Socket 0, every memory access must traverse the UPI bus (adding 60–90 ns latency and reducing bandwidth by ~30%).
3. **PCIe Affinity**: Socket 0 is directly attached to the PCIe lanes driving the Mellanox ConnectX-5 HCA.

### The Standard Binding Directives
```bash
mpirun --bind-to core --map-by core ...
```
* `--bind-to core`: Permanently pins each rank's thread to an individual physical core.
* `--map-by core`: Allocates ranks sequentially across physical cores, ensuring equal core distribution across both CPU sockets.

---

## 5. Cluster Configuration & Compilation Details

Our software stack was compiled using **Spack v0.23** with GCC 14.3.1 on Rocky Linux 10 (`skylake_avx512`).

### 5.1 UCX Specification
To enable Mellanox hardware offload and avoid software fallback, UCX was concretized and compiled with:

```text
ucx@1.17.0%gcc@14.3.1 +rc +ud +dc +verbs +mlx5_dv +dm +rdmacm +thread_multiple ^rdma-core@61.0
```
* `+rc`: Reliable Connected transport (required for InfiniBand zero-copy).
* `+ud`: Unreliable Datagram (used during collective bootstrap).
* `+dc`: Dynamic Connection support.
* `+mlx5_dv`: Direct Verbs hardware acceleration.
* `+dm`: Device Memory offload.
* `^rdma-core@61.0`: Native Rocky Linux 10 kernel user-space RDMA library.

### 5.2 OpenMPI Specification
OpenMPI was configured to use the compiled UCX stack:

```text
openmpi@5.0.5%gcc@14.3.1 +atomics fabrics=ucx romio-filesystem=none schedulers=none ^ucx@1.17.0
```

---

## 6. Runtime Parameters & Execution Command Reference

### 6.1 Critical Environment Variables

```bash
# 1. Select the active InfiniBand port (avoids down ports or Ethernet NICs)
export UCX_NET_DEVICES=mlx5_0:1

# 2. Enforce hardware RDMA for inter-node and shared memory for intra-node
export UCX_TLS=rc,sm,self

# 3. Disable multi-threading inside BLAS when running pure MPI (1 rank per core)
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
```

### 6.2 Hostfile Definition
Create `/shared/hpl/hosts.txt` using the InfiniBand IPoIB addresses:
```text
10.10.72.1:36
10.10.72.2:36
```
*(36 slots per host corresponding to physical CPU cores).*

### 6.3 Complete Standard Launch Script
```bash
#!/bin/bash
set -euo pipefail

# Initialize Spack environment
source /shared/spack/share/spack/setup-env.sh
spack load openmpi hpl

# Environment optimization
export OMP_NUM_THREADS=1
export UCX_NET_DEVICES=mlx5_0:1
export UCX_TLS=rc,sm,self

# Launch with optimal binding
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

---

## 7. Diagnostics & Performance Verification

### 7.1 Verify Active OpenMPI Frameworks
```bash
ompi_info | grep -E "pml|btl|coll"
```
Confirms `pml: ucx` is available and active.

### 7.2 Inspect UCX Active Transports
```bash
ucx_info -d | grep -E "Transport: rc|bandwidth"
```
Expected output:
```text
#      Transport: rc_mlx5
#         Device: mlx5_0:1
#      capabilities:
#            bandwidth: 11794.23/ppn + 0.00 MB/sec
```

### 7.3 Debugging Communication Issues
To trace active protocols and connection handshakes during execution, add:
```bash
mpirun ... -x UCX_LOG_LEVEL=info ...
```
This logs connection establishment, buffer registrations, and protocol transitions to `stderr`.
