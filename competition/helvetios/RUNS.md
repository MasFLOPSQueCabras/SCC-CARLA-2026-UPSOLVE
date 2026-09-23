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
Full HPL configuration, smoke, performance tuning and final submission are pending.
No FLOPS result is claimed.

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

## ISO + full HPL setup — in progress

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
