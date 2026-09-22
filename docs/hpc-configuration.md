# Shared HPC configuration

Libvirt and Helvetios use the same packaged Ansible roles. A lightweight profile
uses the management network, distribution MPI/BLAS packages, and a verified HPL
2.3 source archive. The SCC CARLA 2026 profile defaults to InfiniBand/UCX, system
tuning, and a pinned Spack installation. Both require every declared node to be
available; a failed node or Ansible task fails configuration.

Install the controller collection before configuring:

```bash
ansible-galaxy collection install ansible.posix:2.2.2
```

For a small two-node Rocky Linux 10 cluster, add:

```yaml
configuration:
  profile: lightweight
  inputs:
    ranks_per_node: 1
    hpl_n: 1024
    hpl_nb: 64
    hpl_p: 1
    hpl_q: 2
```

`hpl_p × hpl_q` must equal the declared node count times `ranks_per_node`.
If omitted, `hpl_q` is derived from those inputs. The NFS server defaults to the
declared head node, or the first declared node; select it with `nfs_server`.
Storage paths, mount options, OpenMP threads, MPI launcher/arguments, HPL binary,
and build concurrency are configurable. Defaults use `/opt/cabrita/shared` for
the server export and `/shared` as the common path. NFS exports are published
before clients mount them, and configuration waits for the server's NFSv4
recovery grace period to end. Shutdown stops clients before their NFS server.
Internal SSH keys are generated independently on
each node, and only public keys are exchanged.

Golden recovery needs RAM for the complete compressed software payload. The
Rocky installer allocates roughly 20% of RAM to `/run`; the acceptance test uses
8 GiB for recovery of its prepared HPC image. Larger software images require
more RAM. An undersized installer refuses restoration before writing the disk.

Run `cabrita configure --cluster cluster.yaml --yes` with all nodes selected.
Repeated configuration preserves the compiled binary unless its build inputs
change. Run and collect a benchmark through SSH, substituting the declared head
node ID:

```bash
cabrita ssh --cluster cluster.yaml --node 1 -- /shared/hpl/run_hpl.sh
cabrita ssh --cluster cluster.yaml --node 1 -- tar -C /shared/hpl -czf - results > hpl-results.tar.gz
```

Each invocation creates a result directory containing `HPL.dat` and `HPL.out`.
The runner returns nonzero for MPI failure, failed residual checks, skipped
problems, or missing numerical results. A small HPL solve exercises the same
reference implementation as a larger benchmark; it is not a performance claim.
HPL's numerical test and MPI/BLAS requirements are described by
[the HPL maintainers](https://www.netlib.org/benchmark/hpl/).

## Competition software and transport

Declare an InfiniBand address for every hostname, plus the actual interface and
UCX device. For example, adapt these addresses and tuning inputs to your cluster:

```yaml
configuration:
  profile: scc-carla-2026
  inputs:
    transport: ucx
    ib_interface: ib0
    ib_prefix: 24
    ib_addresses:
      head: 192.0.2.11
      worker: 192.0.2.12
    ucx_device: mlx5_0:1
    ranks_per_node: 16
    hpl_p: 4
    hpl_q: 8
    hpl_n: 32768
    hpl_nb: 256
    build_jobs: 8
    software: spack
    tuning: true
```

Spack is pinned to commit `2bfcc69fa870d3c6919be87593f22647981b648a` (v0.23.1),
including its package recipes. The environment specifies HPL 2.3, OpenMPI 5.0.5,
and OpenBLAS 0.3.28, explicitly enables OpenMPI's UCX fabric for UCX transport,
and retains the concretized `spack.lock` alongside the
installation. An optional `spack_mirror` adds a user-provided mirror; Cabrita
does not publish caches. See [Spack environments](https://spack.readthedocs.io/en/v0.23.1/environments.html)
for the specification and lock-file model.

The software environment lives on the head node's local exported disk and is
included when that node is [captured](golden-recovery.md). Restore fresh nodes
from that image, then run shared configuration to establish the new peer keys,
NFS roles, and hostfile. Existing software is reused. Keep the mount path stable
because installed libraries can embed it.

## Evidence and remaining hardware checks

The libvirt acceptance test exercises two-node SSH, NFS reads/writes, MPI,
numerically validated mini-HPL, repeated configuration, and golden recovery
without a binary rebuild. Run it explicitly with:

```bash
uv run pytest tests/e2e/test_hpc.py -m e2e --run-e2e --provider libvirt \
  --installer-iso /path/to/Rocky-minimal.iso \
  --cloud-image /path/to/Rocky-cloud.qcow2
```

Helvetios still needs physical verification of virtual-media boot, golden
restore, BIOS tuning, InfiniBand, the full-size HPL solve, and competition
submission requirements. Small VM results cannot establish those claims.
Experiments, dedicated HPL CLI commands, Lmod, cache publishing, and Slurm remain
outside this release's scope.
