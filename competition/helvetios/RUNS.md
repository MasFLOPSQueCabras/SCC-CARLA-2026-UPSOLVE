# Helvetios run evidence

## Preparation — 2026-09-23

- Base: cabritactl 1.0.1.dev1, commit `3f44b8c`.
- Branch: `competition/helvetios-submission`.
- Bastion SSH access verified using `scc-bastion`.
- BMC authentication initially failed; updated access subsequently worked on all three nodes.
- All three BMC inventories saved under `test-results/helvetios-submission/inventory/`.
- Verified actual machine model, CPUs, RAM, Ethernet MACs and NVMe serials.
- Corrected media IP from preset `.101` to observed `10.7.12.102`.
- Repaired recursive xorriso wrapper; installed signature-checked team-local media tools.
- Cached pristine ISO matches Rocky's published checksum; modified cached ISO excluded.
- Unit tests: 114 passed, 13 infrastructure tests skipped. Ansible syntax checks passed.

## Hardware gates

ISO-only installation, SSH and reboot persistence: passed.
Basic Ansible and its repeat configuration: passed.
Full HPL configuration, repeat configuration and three-node smoke: passed.
Performance tuning and final submission are pending.

## ISO-stage issues found and repaired

- BMC reads through the bastion exceeded the original two-second timeout; increased to 15 seconds.
- The team account allows virtual media but rejects `ComputerSystem.Boot` changes with HTTP 403.
  HPE `VirtualMedia.Oem.Hpe.BootOnNextServerReset` succeeded on all three nodes and is now used.
- The ISO builder updated the visible GRUB file but left the EFI FAT menu unchanged.
  The original EFI menu still selected media verification and omitted Kickstart.
  Rocky also contains a separate GPT EFI copy. Both paths now reference the patched menu.
- Added a native FAT/ISO regression test verifying ISO, El Torito and GPT EFI contents.
- The first boot attempt was stopped before accepting any installation success; corrected media
  are being rebuilt and checked before retrying.
- Bastion xorriso 1.5.6 could not replay a replaced EFI image; explicit reported boot commands
  now preserve both boot entries and insert the patched GPT EFI copy. Verified all three
  production ISO menus and boot catalogs on the bastion. Local tests: 121 passed, 13 skipped.
- Powered-off BMC boot inventories contained stale NVMe serials. After POST, live inventories
  report node1 `ZC403005`, node2 `ZC40255E`, node3 `ZC4024DT`; manifests updated accordingly.
  Node1's existing Rocky 10.2 installation is reachable by SSH and its chassis serial,
  Ethernet MAC and NVMe serial match the refreshed BMC. This does not count as the fresh ISO gate.
- Live events exposed an asynchronous iLO power race: a power-on acknowledgement could precede
  the physical OFF transition, causing the installation monitor to eject media as though setup
  had completed. Mount/boot now waits for confirmed OFF before mounting and ON before monitoring.
  A regression test covers delayed OFF and ON observations.
- Corrected media now boots Anaconda on physical hardware. Node2 passed the disk guard,
  formatted its intended NVMe and progressed to package configuration. All three guest IPs
  respond to ping. SSH and the completed fresh-install gate are still pending.
- iLO can temporarily time out while serving media. Login retries now distinguish transient
  transport/server failures from rejected credentials. Installation monitoring retries transient
  BMC outages within its existing deadline instead of aborting healthy installs.

## ISO-only installation and SSH — passed 2026-09-23 17:19 UTC

All three nodes completed fresh Rocky 10.2 installations and passed SSH, sudo,
OS/disk identity, management addressing, DNS, HTTPS and repository checks.
Evidence: `iso.j7Eki3/stage.log` plus `inventory/guest-node{1,2,3}.log`.
Anaconda timestamps confirm these are the fresh installations, not earlier OS copies.
Node3's installer completed despite its original monitor timing out; media was ejected
following observed shutdown, disk boot requested, then the gate resumed successfully.

Each host has 36 physical cores, 72 hardware threads, two NUMA nodes and ~192 GiB RAM.
All three have active 100 Gb/s EDR on `mlx5_0:1`, mapped to `ibs5f0`.
HPL configuration now uses those verified devices and team-specific IPoIB addresses
`10.148.72.1–3/24`. Reboot persistence passed at 17:22 UTC on all three nodes,
with changed boot IDs and working passwordless sudo. Evidence: `reboot.od9dqzgx/`.

## ISO + basic Ansible — passed 2026-09-23 17:45 UTC

Fresh installation started at 17:22 UTC. All three nodes passed installation,
SSH, sudo, network and repository checks. Initial configuration completed with
11 successful tasks and four changes per node. The repeat completed with zero
changes, zero failures and zero unreachable hosts. All nine peer SSH checks passed.
Evidence: `basic.UgsiaS/`, including both configuration logs.

## ISO + full HPL setup — passed

Fresh installation started at 17:46 UTC. Source builds use 36 jobs on the head node.
The user confirmed a deadline two hours after 17:43:32 UTC: **19:43:32 UTC today**.
Tuning must fit the time remaining after setup, with time reserved for validation
and delivery; the earlier two-hour tuning cap does not extend this deadline.

All three fresh installs reached SSH by 18:02 UTC. InfiniBand port verification,
peer SSH over `10.148.72.1–3`, unlimited memlock and shared storage access passed.
The first concretization exposed UCX's default disabled verbs/RC transports.
The source build was deliberately interrupted before accepting the environment;
the recipe now requests UCX 1.17.0 with verbs, RC, UD, mlx5 direct verbs and CMA.
OpenBLAS uses its detected CPU target without unnecessary multi-architecture
dispatch kernels. Configuration resumed without reinstalling the OS.
The selected compiler is GCC 14.3.1 and the target is `skylake_avx512`.

UCX 1.17.0 then exposed a build incompatibility between enabled mlx5 direct verbs
and disabled device memory: `uct_ib_mlx5_devx_mem_t` lacked its `dm` member.
Enabled `+dm`, preserving the 57 completed dependencies, and rebuilt UCX/MPI/HPL.
The failed build log is retained as `configure-failed-ucx-dm.log`; the resumed
source build is logged on the head node in `/shared/hpl/build-dm.log`.

## Source build and smoke — passed 2026-09-23 18:54 UTC

The corrected source build completed UCX, OpenMPI and HPL successfully. Runtime
UCX reports RC verbs and RC mlx5 on `mlx5_0:1`. Explicit MPI hostfiles verified
one rank per node and two ranks per node bound to the two 18-core NUMA domains.
Full configuration completed without failures; its repeat check is in progress.

The three-node smoke run used N=4096, NB=128, grid 1×3 and one thread per rank:
**161.08 GFLOPS**, 0.28 seconds, residual **0.00294957629**, one pass, zero failures
and zero skipped tests. Evidence is in `smoke/` and `/shared/hpl/results/smoke`.
The source collector now uses Spack's `spack-src` subdirectory and accommodates
pre-1980 source timestamps in ZIP metadata. Build evidence and the modified HPL
source archive were collected successfully.

## Complete setup repeat and package replay — passed 18:59 UTC

The final full-stage run is `hpl.ZPtf7B/`. Repeat configuration returned
node1 `ok=49 changed=0` and node2/node3 `ok=35 changed=0`, with no failed or
unreachable hosts. All final node verifications passed. A package assembled from
the smoke result passed checksums and replayed successfully on the installed
cluster. Local validation: 126 passed, 13 infrastructure tests skipped; lint,
type checks, Bash syntax and source/wheel build passed.

Tuning started at 18:59 UTC with a 1,700-second cap and the three InfiniBand IPs.
The sweep compares 36×1, 2×18 and 4×9 ranks×threads per node; NB 128/192/256/384;
two process grids; and alternative process ordering for the strongest layouts.

## Compiler, BLAS and MPI comparison — passed 19:18 UTC

The baseline sweep produced 26 valid screening results; its best was 3205.2
GFLOPS at N=32256, NB=256, grid 6×18, 36 MPI ranks per node and one thread per
rank. The larger original case lost its controller session before the runner
recorded exit status and is excluded. A detached rerun was started at 19:19 UTC.

All three Intel-library variants were built locally from verified Netlib HPL 2.3
source. Each passed its smoke and all three layout comparisons. At N=32256,
NB=256 and the strongest 108-rank 6×18 layout, measured results were:

| Compiler / BLAS / MPI | GFLOPS |
| --- | ---: |
| GCC 14.3.1 / OpenBLAS 0.3.28 / OpenMPI 5.0.5 | 3205.2 |
| GCC 14.3.1 / oneMKL 2026.1 / OpenMPI 5.0.5 | 3076.0 |
| icx 2026.1.1 / oneMKL 2026.1 / OpenMPI 5.0.5 | 3080.4 |
| icx 2026.1.1 / oneMKL 2026.1 / Intel MPI 2021.18 | 2924.5 |

Intel MPI used OFI mlx; OpenMPI used source-built UCX RC. The two hybrid layouts
(2×18 and 4×9 ranks×threads per node) were slower in this screen. Compiler and
library packages are permitted by the user's clarification; no vendor HPL
executable was used. Detailed attempts remain in `oneapi-comparison/`.

At N=101376 with NB=256 and grid 6×18, the detached baseline rerun passed at
**4020.0 GFLOPS**, 172.78 seconds, residual 0.00114003923. The equivalent icx/
oneMKL/OpenMPI run passed at **3767.7 GFLOPS**, 184.35 seconds, residual
0.000779295629. The baseline was selected for the final N=129024 run and replay.
Its recorded HPL flags are `CFLAGS=-O3` plus Spack wrapper target arguments
`-march=skylake-avx512 -mtune=skylake-avx512`; build system: configure/GNU make.
The final local checks passed: 129 tests, 13 infrastructure skips, Ruff and ty.

## Final larger result and initial delivery — passed 19:33 UTC

N=129024, NB=256, grid 6×18, 108 MPI ranks (36 per node), one OpenBLAS thread
per rank: **4179.1 GFLOPS**, 342.65 seconds, residual **0.00089969177**.
The process exited zero and passed the full result validator. Rank binding logs
record 36 ranks on each node; the executable checksum matches build evidence.

The current best package was delivered to `/home/scct-2672/submission` on the
bastion at **2026-09-23 19:33:19 UTC**. All 34 checksums passed. It includes the
input, complete output/stderr, Bash reproduction scripts, source ZIP, exact
build records and README pinned to tested Cabrita commit
`2e862636047dcbb4fcfe30136a62ac85ce2b7608`. The full packaged replay is running;
its final outcome and any replacement will be recorded below.

At 19:36 UTC, live Redfish reads reconfirmed active
`WorkloadProfile=HighPerformanceCompute(HPC)` on all three nodes, with
`PowerRegulator=StaticHighPerf`, `EnergyPerfBias=MaxPerf`, `ProcTurbo=Enabled`,
`MinProcIdlePower=NoCStates`, `UncoreFreqScaling=Maximum` and
`EnergyEfficientTurbo=Disabled`. All three deployment manifests request the HPC
BIOS profile. Hyperthreading is enabled, but the HPL mapping uses only physical
cores. Active BIOS attribute snapshots are retained with build evidence.

## Full packaged replay — passed 19:38 UTC

The packaged Bash launcher reran N=129024 successfully: **4170.6 GFLOPS**,
343.34 seconds, residual **0.00089969177**, MPI exit zero. This is within 0.21%
of the original result. The original **4179.1 GFLOPS** remains the highest
complete, validated measurement and is the only submitted result. The final
package adds the live HPC BIOS attribute snapshots and README description.
Local evidence: `submission-final/`, `final-runs.tar.gz`,
`tuning-and-toolchains.tar.gz`, and the delivery receipts.

Final delivery completed at **2026-09-23 19:39:08 UTC**, before the confirmed
19:43:32 UTC deadline, to `/home/scct-2672/submission`. All **37** checksums
passed on the bastion. SHA-256 of `SHA256SUMS`:
`23b9c5b3671f9dd160d3eb7071c78ed28b54e8f4beacea88069486cbc93f76ab`.
A fresh audit of **42** complete tuning/comparison runs revalidated their full
outputs and exit statuses and confirmed **4179.1 GFLOPS** as the maximum.
The final package retains the exact replayed input and Bash launch scripts.

### Post-submission Intel documentation review (2026-09-23)

The on-time `~/submission` is frozen at the user's request. Extended tuning uses
`/shared/hpl/extended-20260923T194450` and retains the original 21:44:50 UTC end.
The runtime experiments use self-built Netlib HPL, not Intel's distributed HPL
binary. Intel libraries, compiler, and MPI were explicitly allowed by the user.

Primary references and the resulting controls:

- [Intel oneMKL GEMM thread partitioning](https://www.intel.com/content/www/us/en/docs/onemkl/developer-guide-linux/2026-0/mkl-num-stripes.html):
  compare default partitioning with `MKL_NUM_STRIPES=1` and `3` at 18 threads per
  rank (one rank/socket), and at six threads per rank. The runner exports the
  setting explicitly through Open MPI and captures it in each immutable case.
- [Intel LINPACK parameter guidance](https://www.intel.com/content/www/us/en/docs/onemkl/developer-guide-linux/2026-0/configuring-parameters.html):
  align N to NB × LCM(P,Q), use P ≤ Q, and budget matrix storage plus workspace.
  Intel recommends NB=384 for its AVX-512 LINPACK distribution. Our Netlib build
  also tests 192, 256, and 512; measured results select the candidate.
- [Intel threading controls](https://www.intel.com/content/www/us/en/docs/onemkl/developer-guide-linux/2023-1/mkl-dynamic.html):
  `MKL_DYNAMIC=FALSE` and explicit `MKL_NUM_THREADS` were already used in all
  Intel comparisons. Core binding and one physical-core allocation per worker
  prevent rank/thread oversubscription.
- [Building Netlib HPL with Intel libraries](https://www.intel.com/content/www/us/en/docs/onemkl/developer-guide-linux/2026-0/building-the-netlib-hpl-from-source-code.html):
  vendor LINPACK-specific controls are not assumed to apply to Netlib HPL.

Runtime overrides are retained through checkpoint recovery and the packaged
repeat. A failed or incomplete solve cannot become the selected result.

At the end of screening, 33 of 34 attempts passed; the final six-thread stripe
screen exceeded its remaining 92-second screening budget (exit 124), so it has
no accepted score. The leading screen was OpenBLAS, N=72576, NB=192, P×Q=6×18,
108 ranks, one thread/rank: **3969.6 GFLOPS**. The same case with BCAST=3 reached
3957.2 GFLOPS. Intel compiler + oneMKL + Intel MPI at the same dimensions reached
3930.1 GFLOPS; using Open MPI reached 3676.4 GFLOPS.

At N=73728, NB=384, six MPI ranks and 18 threads/rank, oneMKL + Open MPI yielded:

| GEMM partitioning | GFLOPS | Residual |
| --- | ---: | ---: |
| Default | 3017.0 | 0.00137878056 |
| `MKL_NUM_STRIPES=1` | 2904.1 | 0.00123332544 |
| `MKL_NUM_STRIPES=3` | 2947.1 | 0.00120888907 |

Live `/proc` inspection confirmed 18 compute workers bound to distinct physical
cores within each socket on all nodes, `MKL_DYNAMIC=FALSE`, and the stripe setting
propagated to remote ranks. The processes loaded `libmkl_avx512.so.3` and
`libmkl_intel_thread.so.3`. Overrides did not beat the default in this comparison.
The large candidate starts at N=217728 with the leading OpenBLAS configuration.

The first large post-submission result reached **4474.0 GFLOPS** at N=217728,
NB=192, P×Q=6×18, 108 ranks and one thread/rank (1538.01 seconds; residual
0.000729418605). The exact packaged replay passed at **4450.0 GFLOPS** with the
same residual, within 0.54%. The original submission's manifest hash and all
37 files were verified unchanged during tuning.

The user subsequently authorized one additional hour, extending the tuning
cutoff from 21:44:50 to **22:44:50 UTC**. New results remain separate.

[Compiler floating-point controls](https://www.intel.com/content/www/us/en/docs/dpcpp-cpp-compiler/developer-guide-reference/2026-0/floating-point-optimizations.html)
and [AVX-512 vector-generation controls](https://www.intel.com/content/www/us/en/docs/dpcpp-cpp-compiler/developer-guide-reference/2026-0/qopt-zmm-usage-qopt-zmm-usage.html)
motivated a separate `icx-mkl-intelmpi-fast` build using
`-O3 -xCORE-AVX512 -fp-model=fast=2 -qopt-zmm-usage=high`. Its smoke test failed:
HPL's machine-precision calculation returned zero, and its residual was infinite.
The validator rejected this result even though MPI itself exited zero.

The corrected `icx-mkl-intelmpi-mixed` build retains those flags for computational
code but compiles HPL_dlamch, HPL_pdlamch, and all testing/validation code with
`-fp-model=precise`. No source algorithm or residual threshold was changed.
The build log records each override. Its N=6912 smoke test passed with machine
precision 1.110223e-16 and residual 0.00186949013. Failed and corrected builds and
runs are kept separately; the unsuccessful build is never eligible for selection.
