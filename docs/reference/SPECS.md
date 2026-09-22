> Historical competition reference. Hardware specifications and performance estimates
> have not been verified by Cabrita’s current release acceptance tests.

# Cluster Hardware Specifications & Theoretical Performance (SPECS)

This document details the complete hardware architecture of the **Helvetios** supercomputer nodes allocated to the team for the SCC@CARLA 2026 competition, providing rigorous mathematical derivations of double-precision theoretical peak performance ($R_{\text{peak}}$), AVX-512 vector execution mechanics, memory bandwidth, and interconnect capabilities.

---

## Table of Contents
1. [Aggregated Cluster Hardware Profile](#1-aggregated-cluster-hardware-profile)
2. [Per-Node Subsystem Architecture](#2-per-node-subsystem-architecture)
3. [Theoretical Peak Performance (FP64 $R_{\text{peak}}$)](#3-theoretical-peak-performance-fp64-r_textpeak)
4. [AVX-512 Frequency Tiers & Real-World Peak](#4-avx-512-frequency-tiers--real-world-peak)
5. [Memory Subsystem & Theoretical Bandwidth](#5-memory-subsystem--theoretical-bandwidth)
6. [Interconnect & Network Fabrics](#6-interconnect--network-fabrics)
7. [Cluster Provenance & Background](#7-cluster-provenance--background)

---

## 1. Aggregated Cluster Hardware Profile

The team is allocated **3 dedicated compute nodes** of the Helvetios supercomputer.

| Subsystem | Per Node | Aggregated Total (3 Nodes) |
| :--- | :--- | :--- |
| **Compute Nodes** | 1 server | **3 servers** |
| **Processor Sockets** | 2 sockets (Dual-socket) | **6 sockets** |
| **Processor Model** | 2x Intel Xeon Gold 6140 @ 2.30 GHz | 6x Intel Xeon Gold 6140 |
| **Physical CPU Cores** | 36 cores | **108 physical cores** |
| **Logical Threads (SMT/HT)**| 72 threads | **216 threads** |
| **System Memory (RAM)** | 192 GB DDR4-2666 ECC Reg | **576 GB DDR4** |
| **Memory Channels** | 12 channels (6 per socket) | **36 memory channels** |
| **Aggregate Memory Bandwidth**| 255.9 GB/s | **767.8 GB/s** |
| **Local NVMe Storage** | 1x 800 GB NVMe SSD | **2.4 TB NVMe Storage** |
| **InfiniBand Fabrics** | 1x 100 Gbps EDR (ConnectX-5) | **3x 100 Gbps EDR links** |
| **Nominal Theoretical Peak ($R_{\text{peak}}$ @ 2.3 GHz)** | **2.65 TFLOPS** | **7.95 TFLOPS** |
| **Sustained AVX-512 Peak ($R_{\text{peak}}$ @ 2.1 GHz)**| **2.42 TFLOPS** | **7.26 TFLOPS** |

---

## 2. Per-Node Subsystem Architecture

```
                                  Per-Node Dual-Socket Architecture
  +---------------------------------------------------------------------------------------------------+
  |                                        Node (192 GB RAM)                                          |
  |                                                                                                   |
  |  +--------------------------------------------+   UPI    +--------------------------------------------+  |
  |  |                 Socket 0                   |<-------->|                 Socket 1                   |  |
  |  |        Intel Xeon Gold 6140 (18 cores)     |(10.4GT/s)|        Intel Xeon Gold 6140 (18 cores)     |  |
  |  |  Base: 2.30 GHz | L3 Cache: 24.75 MB       |          |  Base: 2.30 GHz | L3 Cache: 24.75 MB       |  |
  |  |  2x 512-bit FMA Units per Core (Port 0, 5) |          |  2x 512-bit FMA Units per Core (Port 0, 5) |  |
  |  +--------------------------------------------+          +--------------------------------------------+  |
  |           | (6 Channels DDR4-2666)                                  | (6 Channels DDR4-2666)              |
  |      +----+----+                                               +----+----+                                |
  |      | 96 GB   |                                               | 96 GB   |                                |
  |      | NUMA 0  |                                               | NUMA 1  |                                |
  |      +---------+                                               +---------+                                |
  |           | PCIe Gen3 x16                                           | PCIe Gen3 x4                        |
  |  +-----------------------------------+                     +-----------------------------------+          |
  |  | Mellanox ConnectX-5 (100G EDR IB) |                     |   800 GB Enterprise NVMe SSD      |          |
  |  | mlx5_0:1 (IPoIB: 10.10.72.X)      |                     |   OS + /home/shared Storage       |          |
  |  +-----------------------------------+                     +-----------------------------------+          |
  +---------------------------------------------------------------------------------------------------+
```

### 2.1 Central Processing Unit (CPU)
* **Model**: Intel Xeon Gold 6140 (Skylake-SP, 14nm FinFET process).
* **Socket Configuration**: Dual-socket (Socket 0, Socket 1) interconnected via 3 Intel Ultra Path Interconnect (UPI) links operating at 10.4 GT/s.
* **Core Count**: 18 physical cores per CPU (36 physical cores per node).
* **Simultaneous Multithreading**: Intel Hyper-Threading enabled (36 logical threads per CPU, 72 logical threads per node).
* **Clock Frequencies**:
  * Nominal Base Frequency: **2.30 GHz**.
  * Single-Core Max Turbo Frequency: **3.70 GHz**.
  * AVX-512 All-Core Frequency: **~2.00 – 2.10 GHz** (depending on thermal and power constraints).
* **Cache Hierarchy**:
  * **L1 Data Cache**: 32 KB per core, 8-way set associative (64-byte line size).
  * **L1 Instruction Cache**: 32 KB per core, 8-way set associative.
  * **L2 Cache (Mid-Level)**: 1 MB non-inclusive private cache per core (significantly enlarged compared to older Broadwell architectures to accelerate vector operations).
  * **L3 Cache (Last-Level)**: 24.75 MB non-inclusive shared cache per socket (1.375 MB per core distributed on a 2D mesh topology).
* **Thermal Design Power (TDP)**: 140 W per socket (280 W total CPU dissipation per node).
* **Instruction Set Extensions**: AVX-512 Foundation (F), Conflict Detection (CD), Byte and Word (BW), Doubleword and Quadword (DQ), Vector Length (VL).

---

## 3. Theoretical Peak Performance (FP64 $R_{\text{peak}}$)

Theoretical Peak Performance ($R_{\text{peak}}$) is the strict upper bound of double-precision (64-bit) floating-point calculations that the cluster hardware can execute per second, assuming all vector units are saturated on every clock cycle.

### 3.1 Mathematical Derivation

For an architecture featuring fused multiply-add (FMA) vector execution units:

$$R_{\text{peak}} = N_{\text{cores}} \times f \times \text{FLOPs/cycle/core}_{\text{FP64}}$$

Where the per-cycle floating-point throughput per core is defined by:

$$\text{FLOPs/cycle/core}_{\text{FP64}} = N_{\text{FMA}} \times \text{Vector\_Width}_{\text{FP64}} \times \text{Ops}_{\text{FMA}}$$

### 3.2 Architectural Parameters (Intel Skylake-SP Xeon Gold 6140)

| Parameter | Symbol | Value | Explanation |
| :--- | :--- | :--- | :--- |
| **FMA Units per Core** | $N_{\text{FMA}}$ | **2** | Executed simultaneously on execution Port 0 and Port 5. |
| **Vector Register Width** | $W_{\text{vector}}$ | **512 bits** | Intel AVX-512 register size (ZMM0–ZMM31). |
| **Data Element Width** | $W_{\text{element}}$ | **64 bits** | IEEE 754 double-precision floating-point (FP64). |
| **Elements per Register**| $\text{Vector\_Width}_{\text{FP64}}$| **8** | $\frac{512 \text{ bits}}{64 \text{ bits/element}} = 8 \text{ elements}$. |
| **Operations per FMA** | $\text{Ops}_{\text{FMA}}$ | **2** | An FMA computes $a \times b + c$ (1 multiply + 1 add). |

Calculating the per-cycle core throughput:
$$\text{FLOPs/cycle/core}_{\text{FP64}} = 2 \times 8 \times 2 = \mathbf{32 \text{ DP FLOPs/cycle/core}}$$

*(For single precision FP32, the throughput doubles to 64 FLOPs/cycle/core).*

---

### 3.3 Nominal Peak Calculations (@ Base Frequency $f = 2.30 \text{ GHz}$)

#### A. Single Physical Core
$$R_{\text{peak, core}} = 2.30 \times 10^9 \text{ cycles/s} \times 32 \text{ FLOPs/cycle} = 73.6 \times 10^9 \text{ FLOPs} = \mathbf{73.6 \text{ GFLOPS}}$$

#### B. Single Compute Node (36 Cores)
$$R_{\text{peak, node}} = 36 \text{ cores} \times 73.6 \text{ GFLOPS/core} = 2,649.6 \text{ GFLOPS} = \mathbf{2.6496 \text{ TFLOPS}}$$

#### C. Aggregated 3-Node Cluster (108 Cores)
$$R_{\text{peak, cluster}} = 108 \text{ cores} \times 73.6 \text{ GFLOPS/core} = 7,948.8 \text{ GFLOPS} = \mathbf{7.9488 \text{ TFLOPS}}$$

---

## 4. AVX-512 Frequency Tiers & Real-World Peak

Modern Intel processors implement dynamic frequency scaling based on instruction license levels to prevent exceeding the socket's TDP and current limits:

```
+---------------------+-------------------------------+-------------------------+
| Instruction Class   | License Level                 | Typical All-Core Clock  |
+---------------------+-------------------------------+-------------------------+
| Scalar / SSE / AVX2 | License 0 / 1 (Standard)      | ~2.30 - 2.80 GHz        |
| AVX-512 Light       | License 1 (F, CD without FMA) | ~2.10 - 2.30 GHz        |
| AVX-512 Heavy       | License 2 (Dual 512-bit FMA)  | ~2.00 - 2.10 GHz        |
+---------------------+-------------------------------+-------------------------+
```

When running `DGEMM` in HPL, all 36 cores per node continuously fire both 512-bit FMA units on Ports 0 and 5. This triggers **License Level 2**, throttling the all-core sustained clock speed below the 2.30 GHz nominal base to roughly **2.00 GHz to 2.10 GHz**.

### Adjusted Theoretical Peak under AVX-512 Load

| Frequency Assumption | Per Core Peak | Single Node (36 Cores) | 3-Node Cluster (108 Cores) |
| :--- | :--- | :--- | :--- |
| **Nominal Base (2.30 GHz)** | 73.60 GFLOPS | **2.650 TFLOPS** | **7.949 TFLOPS** |
| **AVX-512 High (2.10 GHz)** | 67.20 GFLOPS | **2.419 TFLOPS** | **7.258 TFLOPS** |
| **AVX-512 Low (2.00 GHz)**  | 64.00 GFLOPS | **2.304 TFLOPS** | **6.912 TFLOPS** |

> [!TIP]
> When evaluating HPL benchmark results:
> * Achieving **~2.015 TFLOPS** on 2 nodes (72 cores) against a 2.10 GHz peak ($72 \times 67.2 = 4.838 \text{ TFLOPS}$) yields an initial test efficiency of **~41.6%** for a small $N=30000$ matrix.
> * Scaling to full problem sizes ($N \approx 240,000$) on 3 nodes typically raises real-world computational efficiency to **75% – 85%** of $R_{\text{peak}}$.

---

## 5. Memory Subsystem & Theoretical Bandwidth

Linpack panel factorizations and communication buffering require substantial memory bandwidth.

### Specifications
* **Memory Type**: DDR4 ECC Registered.
* **Module Transfer Rate**: 2666 MT/s (MegaTransfers per second).
* **Channels per Socket**: 6 channels.
* **Channels per Node**: 12 channels (dual-socket).
* **Bus Width**: 64 bits (8 bytes) per channel.

### Theoretical Memory Bandwidth Calculation

$$\text{BW}_{\text{channel}} = 2.666 \times 10^9 \text{ transfers/s} \times 8 \text{ bytes} = 21.328 \text{ GB/s}$$

$$\text{BW}_{\text{node}} = 12 \text{ channels} \times 21.328 \text{ GB/s} = \mathbf{255.936 \text{ GB/s}}$$

$$\text{BW}_{\text{cluster}} = 3 \text{ nodes} \times 255.936 \text{ GB/s} = \mathbf{767.808 \text{ GB/s}}$$

### Memory-to-Compute Ratio (FLOP-to-Byte Ratio)
$$\text{Ratio}_{\text{node}} = \frac{2,649.6 \text{ GFLOPS}}{255.9 \text{ GB/s}} \approx 10.35 \text{ FLOPs/Byte}$$

A ratio of ~10.35 FLOPs per byte confirms that kernels operating with an arithmetic intensity below 10.35 FLOPs/byte will be memory-bandwidth bound. Since HPL has an arithmetic intensity that grows as $O(N)$, large matrix dimensions ensure execution remains purely compute-bound.

---

## 6. Interconnect & Network Fabrics

| Interface | Hardware Device | Bandwidth | Protocol / Usage |
| :--- | :--- | :--- | :--- |
| **InfiniBand** | Mellanox ConnectX-5 (MT4115) | **100 Gbps (12.1 GB/s)** | Native RDMA, OpenMPI UCX, NFS shared storage (`10.10.72.0/24`) |
| **Ethernet** | Intel 10G / 1G NICs | **10 Gbps / 1 Gbps** | Cluster management, SSH ProxyJump, Bastion access (`10.2.72.0/24`) |
| **Out-of-Band** | HPE iLO 5 Dedicated BMC | **1 Gbps** | Redfish API, virtual media, power telemetry (`10.1.72.0/24`) |

---

## 7. Cluster Provenance & Background

* **Name**: Helvetios Supercomputer.
* **Host Institution**: Universidad Nacional de Córdoba (UNC) – Centro de Computación de Alto Rendimiento (CCAR), Argentina.
* **Origin**: Donated to UNC by the École Polytechnique Fédérale de Lausanne (EPFL), Switzerland.
* **Original Deployment**:
  * Total compute nodes: 288 nodes (all-CPU compute fabric).
  * Processors: 576x Intel Xeon Gold 6140 CPUs (10,368 cores).
  * Aggregate cluster RAM: ~54 TiB.
  * Interconnect: 100G InfiniBand EDR fabric.
  * Theoretical peak capacity: ~763 TFLOPS.
