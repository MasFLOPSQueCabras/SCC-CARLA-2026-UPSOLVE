# One local libvirt cluster

Follow the [Fedora 44 / Ubuntu 26.04 host guide](docs/host-installation.md).
With uv and Python 3.14 available, prepare the controller from this checkout:

```bash
uv sync
uv run cabritactl host setup
uv run cabritactl host setup --apply
# Renew your login if group membership changed.
uv sync --extra libvirt
uv run ansible-galaxy collection install -r src/cabritactl/ansible/requirements.yml
```

Download a Rocky Linux cloud qcow2 image and verify its vendor checksum. Create
an Ed25519 SSH key if you do not already have one. Then initialize a workspace:

```bash
uv run cabritactl init ./demo --artifact /path/to/Rocky-cloud.qcow2
```

Review `demo/cluster.yaml`: review the generated cluster-owned NAT network, stable node addresses,
resources, and your SSH key paths. The generated profile declares
three nodes; remove nodes you do not need. `configuration.profile: none` gives a
small OS-only example. The image checksum is computed during initialization.

```bash
uv run cabritactl validate --cluster demo/cluster.yaml
uv run cabritactl doctor --cluster demo/cluster.yaml
uv run cabritactl plan --cluster demo/cluster.yaml --json
uv run cabritactl up --cluster demo/cluster.yaml --yes
uv run cabritactl verify --cluster demo/cluster.yaml --network --json
uv run cabritactl ssh --cluster demo/cluster.yaml --node 1 -- hostname
uv run cabritactl down --cluster demo/cluster.yaml --yes
```

A later `up` restarts the installed nodes. To remove these VMs and their managed
disks, run `uv run cabritactl destroy --cluster demo/cluster.yaml --yes`.

Continue with [authoring](docs/authoring.md), [installer media](docs/bootstrap-methods.md),
or [two-node HPC configuration](docs/hpc-configuration.md).
