# Authoring a cluster

The resolved authoring API is `ResolvedCluster.load(Path("cluster.yaml"))`.
It resolves paths relative to that file, merges nested node defaults once, and
rejects undeclared targets, missing artifact checksums, and unsupported
provider/media combinations before execution. The lifecycle CLI integration
uses this same resolved object for planning and execution.

A cluster declares its stable name, provider, explicit nodes, bootstrap,
artifacts, template inputs, and configuration. Names contain lowercase letters,
numbers, and hyphens. Node IDs may be any positive unique integers. Cluster names
namespace virtual machine resources; physical locks identify the BMC endpoint,
so different cluster names cannot independently lock the same physical machine.

```yaml
name: workshop
provider: libvirt
bootstrap:
  method: cloud-init
  artifact: os
  user_data: ./user-data.yaml
artifacts:
  os:
    source: ./rocky.qcow2
    sha256: REPLACE_WITH_THE_ACTUAL_64_CHARACTER_SHA256
    format: qcow2
configuration:
  profile: custom
  playbook: ./site.yaml
nodes:
  - id: 10
    hostname: head
    ip: 192.168.122.110
    mac: '52:54:00:00:00:10'
```

Bootstrap methods are `cloud-init`, `embedded-kickstart`, `oemdrv`,
`golden-restore`, and `custom`. Cloud-init requires qcow2 and is libvirt-only.
The ISO methods require an ISO artifact. Golden restoration additionally
requires a `payload` reference to a `raw.zst` artifact. The `kickstart`,
`network_config`, and `templates` paths support user-authored inputs;
`template_inputs` and `bootstrap.inputs` hold template variables.

Custom preparation takes `prepare.argv` (an executable argument list),
`prepare.execution` (`local` or `bastion`), and a positive `prepare.timeout`.
Cabrita appends `--context <resolved-context.json> --output <artifact.json>`.
The executable must exit zero and produce an artifact object containing an
absolute `source`, `format` (`iso`, `qcow2`, or `raw.zst`), and `sha256`.
Cabrita validates the checksum on the preparation host. Old output manifests
are removed before each attempt; logs are retained on errors and timeouts.
Remote execution requires a bastion and absolute remote working directory.
Arguments are passed literally; a shell expression must be explicitly requested
as an executable argument, such as `sh -c`.

Chameleon/OpenStack is future work. It raises an unsupported-provider error;
no operation reports simulated success.

## Managed local resources

New libvirt workspaces use `network.managed: true`. Subnet, bridge, network name,
node IPs and MACs are explicit in the generated manifest. `init --network existing`
preserves offline authoring for administrator-managed networks. Omitted `managed`
means external; Cabrita does not modify such networks.

`libvirt.storage_pool` selects a prepared directory pool, defaulting to `cabrita`.
New deployments upload all QEMU-visible media into that pool. These changes do
not migrate existing VM disks. See [host setup](host-installation.md).
