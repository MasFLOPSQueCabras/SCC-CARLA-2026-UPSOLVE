# One local libvirt cluster

Install Python 3.14, uv, QEMU/KVM, libvirt (including its development headers),
OpenSSH, and xorriso. Start libvirt's default network and confirm your account can
run `virsh -c qemu:///system list --all`. Use a Linux host with hardware
virtualization. From this checkout:

```bash
uv sync --extra libvirt
```

Download a Rocky Linux cloud qcow2 image and verify its vendor checksum. Create
an Ed25519 SSH key if you do not already have one. Then initialize a workspace:

```bash
uv run cabritactl init ./demo --artifact /path/to/Rocky-cloud.qcow2
```

Review `demo/cluster.yaml`: choose an unused cluster name, node IPs and MACs on
your libvirt network, and your SSH key paths. The generated profile declares
three nodes; remove nodes you do not need. `configuration.profile: none` gives a
small OS-only example. The image checksum is computed during initialization.

```bash
uv run cabritactl validate --cluster demo/cluster.yaml
uv run cabritactl doctor --cluster demo/cluster.yaml
uv run cabritactl plan --cluster demo/cluster.yaml --json
uv run cabritactl up --cluster demo/cluster.yaml --yes
uv run cabritactl verify --cluster demo/cluster.yaml --json
uv run cabritactl ssh --cluster demo/cluster.yaml --node 1 -- hostname
uv run cabritactl down --cluster demo/cluster.yaml --yes
```

A later `up` restarts the installed nodes. To remove these VMs and their managed
disks, run `uv run cabritactl destroy --cluster demo/cluster.yaml --yes`.

Continue with [authoring](docs/authoring.md), [installer media](docs/bootstrap-methods.md),
or [two-node HPC configuration](docs/hpc-configuration.md).
