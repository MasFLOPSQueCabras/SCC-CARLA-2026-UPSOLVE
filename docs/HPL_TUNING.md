# High-Performance Linpack (HPL) Tuning & Optimization Guide

This guide provides practical and mathematical tuning strategies for running the **High-Performance Linpack (HPL)** benchmark on the SCC@CARLA compute cluster (Intel Xeon Gold 6140 Skylake-SP with Mellanox ConnectX-5 100G InfiniBand). It covers parameter selection for both **single-node** (36 cores) and **multi-node** (72 cores and 108 cores) configurations.

---

## Table of Contents
1. [HPL Parameter Tuning Overview](#1-hpl-parameter-tuning-overview)
2. [Tuning Matrix Problem Size ($N$)](#2-tuning-matrix-problem-size-n)
3. [Tuning Block Size ($NB$)](#3-tuning-block-size-nb)
4. [Tuning the Process Grid ($P \times Q$)](#4-tuning-the-process-grid-p-times-q)
5. [Algorithmic Parameters in `HPL.dat`](#5-algorithmic-parameters-in-hpldat)
6. [Host & Kernel Tuning Checklist](#6-host--kernel-tuning-checklist)
7. [Ready-to-Use `HPL.dat` Configurations](#7-ready-to-use-hpldat-configurations)
8. [Benchmarking Workflow & Step-by-Step Runbook](#8-benchmarking-workflow--step-by-step-runbook)

---

## 1. HPL Parameter Tuning Overview

Achieving high computational efficiency ($\eta = R_{\text{max}} / R_{\text{peak}}$) requires balancing three competing factors:
1. **Memory Utilization**: $N$ must be large enough so that $O(N^3)$ compute operations dominate $O(N^2)$ memory and network transfers, but not so large that the OS invokes swap space (which instantly kills performance).
2. **Cache Hierarchy**: $NB$ must be sized so that submatrices fit into L2 cache (1 MB private per core) to sustain peak `DGEMM` throughput on AVX-512 FMA execution units.
3. **Communication Overhead**: The process grid $P \times Q$ and broadcast algorithm ($BCAST$) must minimize network contention over the InfiniBand fabric.

---

## 2. Tuning Matrix Problem Size ($N$)

### 2.1 The Memory Footprint Formula

Because matrix $A$ stores 64-bit (8-byte) double-precision floating-point numbers, the total physical memory consumed by matrix $A$ is:

$$\text{Memory (bytes)} = 8 \times N^2$$

$$\text{Memory (Gigabytes)} = \frac{8 \times N^2}{10^9}$$

Solving for $N$ given an allocated memory budget $M_{\text{bytes}}$:

$$N = \sqrt{\frac{M_{\text{bytes}}}{8}}$$

### 2.2 Sizing Guidelines
* **Target Memory Budget**: **80% to 85%** of physical cluster RAM. 
* **Never exceed 88%**: The Linux kernel, network buffers (InfiniBand Queue Pairs and registered memory), and MPI runtime structures require roughly 10% to 15% of free RAM. If memory pressure triggers page swapping, execution time increases by orders of magnitude.
* **Block Alignment**: Ensure $N$ is an exact multiple of the chosen block size ($NB$).

### 2.3 Optimal Problem Sizes for Our Cluster

| Scenario | Total RAM | Target RAM (80–83%) | Ideal Mathematical $N$ | Recommended Aligned $N$ ($NB=384$) | Expected Execution Time |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Quick Sanity Test** | Any | ~7.2 GB | ~30,000 | **30,000** | **~9 seconds** |
| **Medium Calibration**| Any | ~28.8 GB | ~60,000 | **60,000** | **~1 minute** |
| **Single Node (36 cores)** | 192 GB | ~153 GB | ~138,290 | **138,240** ($360 \times 384$) | **~12–15 minutes** |
| **Two Nodes (72 cores)**   | 384 GB | ~307 GB | ~195,890 | **195,840** ($510 \times 384$) | **~20–25 minutes** |
| **Three Nodes (108 cores)**| 576 GB | ~465 GB | ~241,090 | **241,152** ($628 \times 384$) | **~30–35 minutes** |

---

## 3. Tuning Block Size ($NB$)

The block size $NB$ controls the size of matrix blocks distributed across the process grid and the panel width factorized at each iteration.

```
       Small NB (e.g., 64)                       Large NB (e.g., 384)
  +--------------------------------+        +--------------------------------+
  | Many small messages            |        | Fewer, larger messages         |
  | High network latency overhead  |        | Full 100G InfiniBand saturation|
  | Sub-optimal DGEMM performance  |        | Maximum AVX-512 FMA saturation |
  +--------------------------------+        +--------------------------------+
```

### 3.1 Architectural Constraints on Intel Skylake-SP
1. **AVX-512 Vector Width**: Each AVX-512 register holds 8 FP64 elements. $NB$ **must be a multiple of 8** to avoid vector register masking or unaligned memory access.
2. **L2 Cache Capacity**: Each core on the Xeon Gold 6140 features a **1 MB private L2 cache**. Submatrices of size $NB \times NB$ processed in Level 3 BLAS (`DGEMM`) achieve highest reuse when $NB \times NB \times 8 \text{ bytes} \le 1 \text{ MB}$, meaning $NB \le \sqrt{1,048,576 / 8} \approx 362$.
3. **Network Message Granularity**: For 100 Gbps InfiniBand, larger panel broadcasts ($NB \approx 256 - 384$) amortize PCIe doorbell overhead and achieve wire-speed RDMA.

### 3.2 Recommended $NB$ Values
* **$NB = 384$ (Recommended Primary)**: Provides the highest sustained performance on Skylake-SP AVX-512 architectures when paired with OpenBLAS or Intel oneAPI MKL.
* **$NB = 256$ (Alternative)**: Excellent balance for smaller problem sizes ($N \le 100,000$).
* **$NB = 192$ (Alternative)**: Conservative block size with slightly faster panel factorization.

---

## 4. Tuning the Process Grid ($P \times Q$)

HPL maps MPI processes onto a two-dimensional grid of size $P \times Q$, where:

$$\text{Total MPI Ranks} = P \times Q$$

### 4.1 Golden Rules for $P \times Q$
1. **$P \le Q$**: In HPL's right-looking algorithm, column panels are broadcast horizontally across process rows. Because horizontal communication volume exceeds vertical communication volume, having **more columns ($Q$) than rows ($P$)** minimizes communication bottlenecks.
2. **$Q / P \approx 1$ to $2$**: Avoid extreme aspect ratios (e.g., $1 \times 72$ or $1 \times 108$), as they overload the column broadcast tree. A nearly square grid with $P \approx Q$ and $P \le Q$ yields the best balance.
3. **Pure MPI (1 Rank per Physical Core)**: For pure MPI execution, allocate exactly 1 rank per physical core (36 ranks per node).

### 4.2 Optimal Grids for Each Cluster Scale

| Configuration | Total Cores / Ranks | Recommended ($P \times Q$) | Alternative ($P \times Q$) | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Single Node** | 36 cores | **$6 \times 6 = 36$** | $4 \times 9 = 36$ | Perfectly square grid; intra-node shared memory. |
| **Two Nodes**   | 72 cores | **$6 \times 12 = 72$** | $8 \times 9 = 72$ | 1:2 ratio; ideal for InfiniBand RDMA. |
| **Three Nodes** | 108 cores | **$9 \times 12 = 108$**| $6 \times 18 = 108$ | $9 \times 12$ offers optimal grid squareness. |

---

## 5. Algorithmic Parameters in `HPL.dat`

The configuration file `HPL.dat` contains several algorithmic tuning flags:

```text
HPLinpack benchmark input file
Innovative Computing Laboratory, University of Tennessee
HPL.out      output file name (if any)
6            device out (6=stdout,7=stderr,file)
1            # of problems sizes (N)
30000        Ns
1            # of NBs
384          NBs
0            PMAP process mapping (0=Row-,1=Column-major)
1            # of process grids (P x Q)
6            Ps
12           Qs
16.0         threshold
1            # of panel fact
2            PFACTs (0=left, 1=Crout, 2=Right)
1            # of recursive stopping criterium
4            NBMINs (>= 1)
1            # of panels in recursion
2            NDIVs
1            # of recursive panel fact.
1            RFACTs (0=left, 1=Crout, 2=Right)
1            # of broadcast
1            BCASTs (0=1rg,1=1rM,2=2rg,3=2rM,4=Lng,5=LnM)
1            # of lookahead depth
1            DEPTHs (>=0)
1            SWAP (0=bin-exch,1=long,2=mix)
64           swapping threshold
0            L1 in (0=transposed,1=no-transposed) form
0            U  in (0=transposed,1=no-transposed) form
1            Equilibration (0=no,1=yes)
8            memory alignment in double (> 0)
```

### Key Parameter Explanations

* **`PMAP` (0 = Row-major, 1 = Column-major)**:
  Use **`0` (Row-major)**. Ensures consecutive MPI ranks are placed along process rows, maximizing intra-node shared-memory communication on the same server before crossing InfiniBand.
* **`PFACT` (Panel Factorization)**:
  * `0 = Left-looking`, `1 = Crout`, `2 = Right-looking`.
  * **Recommendation**: **`2` (Right)** or **`1` (Crout)**. Right-looking exposes maximal parallelism for trailing updates.
* **`NBMIN` & `NDIV`**:
  * Sets the threshold for recursive panel subdivision.
  * **Recommendation**: `NBMIN = 4`, `NDIV = 2`.
* **`RFACT` (Recursive Factorization)**:
  * **Recommendation**: **`1` (Crout)** or **`2` (Right)**.
* **`BCAST` (Panel Broadcast Algorithm)**:
  * `0 = 1ring`, `1 = 1ringM`, `2 = 2ring`, `3 = 2ringM`, `4 = Long`, `5 = LongM`.
  * **Recommendation**: **`1` ($1\text{ringM}$)** or **`3` ($2\text{ringM}$)**. The modified ring algorithms (`1ringM`, `2ringM`) split messages and send in both ring directions, preventing network congestion over InfiniBand switches.
* **`DEPTH` (Lookahead Depth)**:
  * `0` = No lookahead (synchronous).
  * `1` = 1-level lookahead (asynchronous overlap).
  * **Recommendation**: **`1`**. Lookahead overlaps the panel factorization of step $k+1$ with the trailing matrix update of step $k$, hiding panel communication latency behind `DGEMM` computation.
* **`SWAP` (Pivoting Row Swapping)**:
  * `1 = Long`, `2 = Mix`.
  * **Recommendation**: **`1` (Long)** or **`2` (Mix)** with swapping threshold `64`.
* **`memory alignment in double`**:
  * Set to **`8`** (aligns matrix pointers to 64-byte boundaries, matching AVX-512 512-bit cache lines).

---

## 6. Host & Kernel Tuning Checklist

Before launching high-load Linpack runs, verify that the Linux operating system is tuned for throughput:

### 6.1 Disable Kernel NUMA Balancing
Automatic NUMA balancing scans memory pages and moves them between sockets, causing catastrophic latency spikes during `DGEMM`:
```bash
sysctl -w kernel.numa_balancing=0
```

### 6.2 Set Swappiness to Minimum
Prevent the Linux virtual memory manager from evicting application pages:
```bash
sysctl -w vm.swappiness=10
```

### 6.3 Performance CPU Governor
Lock CPU cores at maximum frequency and disable power-saving C-state transitions:
```bash
tuned-adm profile throughput-performance
```

### 6.4 Enable Transparent Huge Pages (THP)
Reduces Translation Lookaside Buffer (TLB) misses on large matrix arrays:
```bash
echo madvise > /sys/kernel/mm/transparent_hugepage/enabled
```

### 6.5 Verify Unlimited Locked Memory (`memlock`)
```bash
ulimit -l
# Must return: unlimited
```

---

## 7. Ready-to-Use `HPL.dat` Configurations

### 7.1 Single-Node Production Run (36 Cores, 192 GB RAM)
* **Matrix Size**: $N = 138,240$ (~153 GB RAM, ~80% memory).
* **Grid**: $P = 6, Q = 6$.
* **Block Size**: $NB = 384$.

```text
HPLinpack benchmark input file
Innovative Computing Laboratory, University of Tennessee
HPL.out      output file name (if any)
6            device out (6=stdout,7=stderr,file)
1            # of problems sizes (N)
138240       Ns
1            # of NBs
384          NBs
0            PMAP process mapping (0=Row-,1=Column-major)
1            # of process grids (P x Q)
6            Ps
6            Qs
16.0         threshold
1            # of panel fact
2            PFACTs (0=left, 1=Crout, 2=Right)
1            # of recursive stopping criterium
4            NBMINs (>= 1)
1            # of panels in recursion
2            NDIVs
1            # of recursive panel fact.
1            RFACTs (0=left, 1=Crout, 2=Right)
1            # of broadcast
1            BCASTs (0=1rg,1=1rM,2=2rg,3=2rM,4=Lng,5=LnM)
1            # of lookahead depth
1            DEPTHs (>=0)
1            SWAP (0=bin-exch,1=long,2=mix)
64           swapping threshold
0            L1 in (0=transposed,1=no-transposed) form
0            U  in (0=transposed,1=no-transposed) form
1            Equilibration (0=no,1=yes)
8            memory alignment in double (> 0)
```

---

### 7.2 Two-Node Cluster Run (72 Cores, 384 GB RAM)
* **Matrix Size**: $N = 195,840$ (~307 GB RAM, ~80% memory).
* **Grid**: $P = 6, Q = 12$.
* **Block Size**: $NB = 384$.

```text
HPLinpack benchmark input file
Innovative Computing Laboratory, University of Tennessee
HPL.out      output file name (if any)
6            device out (6=stdout,7=stderr,file)
1            # of problems sizes (N)
195840       Ns
1            # of NBs
384          NBs
0            PMAP process mapping (0=Row-,1=Column-major)
1            # of process grids (P x Q)
6            Ps
12           Qs
16.0         threshold
1            # of panel fact
2            PFACTs (0=left, 1=Crout, 2=Right)
1            # of recursive stopping criterium
4            NBMINs (>= 1)
1            # of panels in recursion
2            NDIVs
1            # of recursive panel fact.
1            RFACTs (0=left, 1=Crout, 2=Right)
1            # of broadcast
1            BCASTs (0=1rg,1=1rM,2=2rg,3=2rM,4=Lng,5=LnM)
1            # of lookahead depth
1            DEPTHs (>=0)
1            SWAP (0=bin-exch,1=long,2=mix)
64           swapping threshold
0            L1 in (0=transposed,1=no-transposed) form
0            U  in (0=transposed,1=no-transposed) form
1            Equilibration (0=no,1=yes)
8            memory alignment in double (> 0)
```

---

### 7.3 Full 3-Node Cluster Competition Run (108 Cores, 576 GB RAM)
* **Matrix Size**: $N = 241,152$ (~465 GB RAM, ~81% memory).
* **Grid**: $P = 9, Q = 12$.
* **Block Size**: $NB = 384$.

```text
HPLinpack benchmark input file
Innovative Computing Laboratory, University of Tennessee
HPL.out      output file name (if any)
6            device out (6=stdout,7=stderr,file)
1            # of problems sizes (N)
241152       Ns
1            # of NBs
384          NBs
0            PMAP process mapping (0=Row-,1=Column-major)
1            # of process grids (P x Q)
9            Ps
12           Qs
16.0         threshold
1            # of panel fact
2            PFACTs (0=left, 1=Crout, 2=Right)
1            # of recursive stopping criterium
4            NBMINs (>= 1)
1            # of panels in recursion
2            NDIVs
1            # of recursive panel fact.
1            RFACTs (0=left, 1=Crout, 2=Right)
1            # of broadcast
1            BCASTs (0=1rg,1=1rM,2=2rg,3=2rM,4=Lng,5=LnM)
1            # of lookahead depth
1            DEPTHs (>=0)
1            SWAP (0=bin-exch,1=long,2=mix)
64           swapping threshold
0            L1 in (0=transposed,1=no-transposed) form
0            U  in (0=transposed,1=no-transposed) form
1            Equilibration (0=no,1=yes)
8            memory alignment in double (> 0)
```

---

## 8. Benchmarking Workflow & Step-by-Step Runbook

### Step 1: Deploy the Desired Configuration
Copy the target `HPL.dat` into `/shared/hpl/configs/HPL.dat`:
```bash
cp /shared/hpl/configs/HPL.dat_3node_240k /shared/hpl/HPL.dat
```

### Step 2: Ensure Hosts File Matches Cluster State
Ensure `/shared/hpl/hosts.txt` lists all active nodes:
```text
10.10.72.1:36
10.10.72.2:36
10.10.72.3:36
```

### Step 3: Launch in a Detached Session (`tmux`)
Because large Linpack runs take 20–35 minutes, always run inside `tmux` to prevent accidental terminal disconnects:
```bash
tmux new -s hpl_run
cd /shared/hpl
./run_hpl.sh | tee hpl_output_$(date +%Y%m%d_%H%M%S).txt
```

### Step 4: Verify Scaled Residual
Check the bottom of the output log:
```text
||Ax-b||_oo/(eps*(||A||_oo*||x||_oo+||b||_oo)*N)=   1.50868244e-03 ...... PASSED
```
A status of **`PASSED`** validates both arithmetic accuracy and hardware stability.
