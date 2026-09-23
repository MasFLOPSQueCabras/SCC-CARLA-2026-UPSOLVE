# Fedora 44 and Ubuntu 26.04 hosts

This guide targets x86-64 Linux hosts and Rocky Linux 10 guests. It does not add
Ubuntu/Fedora HPC guest support or migrate existing clusters. Existing checkouts
with local storage patches should remain separate from new deployments.

## Install the controller

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git, clone
this repository, and run `uv python install 3.14` followed by `uv sync`.
The base installation deliberately does not require the libvirt Python extension.
Use `uv run cabritactl` for the following commands when running from a checkout.

```bash
cabritactl host setup             # Preview, no host changes
cabritactl host setup --apply     # Install packages, enable services, prepare pool
# Include installer/golden-image prerequisites when needed:
cabritactl host setup --full --apply
```

Setup invokes individual package-manager/systemctl/virsh commands through sudo;
it does not run the entire Python application as root. Logs are saved under
`$XDG_STATE_HOME/cabrita/host-setup.log` (normally `~/.local/state/cabrita`).
Package steps are repeatable. Setup preserves the installed active libvirt daemon
family and never restarts running VMs. Conflicting daemon families require manual
resolution. New group membership requires a fresh login before deployment.

After host setup:

```bash
uv sync --extra libvirt
uv run ansible-galaxy collection install -r src/cabritactl/ansible/requirements.yml
ssh-keygen -t ed25519   # Only if you do not already have a suitable key
```

The `libvirt` group grants powerful system VM management privileges. Only add
trusted administrators. Do not chmod the libvirt socket to make it world writable.
On Fedora, polkit may provide access instead; verify authorization from the same
login/session that will run deployments.

## Native package lists

For manual setup, install the same packages used by `host setup`.

Fedora 44:

```bash
sudo dnf install qemu-kvm qemu-img libvirt-daemon-kvm libvirt-client \
  libvirt-devel gcc python3-devel pkgconf-pkg-config edk2-ovmf xorriso \
  openssh-clients curl policycoreutils-python-utils
# Optional OEMDRV and golden capture/recovery tools:
sudo dnf install mtools dosfstools guestfs-tools zstd
```

Ubuntu 26.04:

```bash
sudo apt update
sudo apt install qemu-kvm qemu-utils libvirt-daemon-system libvirt-clients \
  libvirt-dev build-essential python3-dev pkg-config ovmf xorriso \
  openssh-client curl apparmor apparmor-utils
# Optional OEMDRV and golden capture/recovery tools (Universe repository):
sudo apt install mtools dosfstools libguestfs-tools zstd
```

QEMU/KVM runs guests; libvirt manages VM lifecycle, storage, networking and
confinement. Development packages compile `libvirt-python`. OVMF supplies UEFI;
xorriso builds installation media. `mtools` updates embedded EFI boot menus;
`mtools` and `dosfstools` also build OEMDRV media.
Golden-image capture additionally uses virt-inspector, virt-sysprep and zstd.
GUI tools such as virt-manager and cockpit-machines are optional.

Check the active daemon family instead of enabling both:

```bash
systemctl is-active virtqemud.socket virtqemud.service
systemctl is-active libvirtd.socket libvirtd.service
```

For a modular installation, setup enables `virtqemud`, `virtnetworkd`,
`virtstoraged`, `virtnodedevd`, `virtnwfilterd`, and `virtsecretd` sockets. It
retains an active monolithic installation. Verify:

```bash
virsh -c qemu:///system list --all
virsh -c qemu:///system pool-list --all
virt-host-validate qemu
```

If OpenSSH rejects a system/user configuration file, `doctor` reports its parser
error. Inspect the named file with `namei -l` and correct its owner/mode through
the host administrator; do not silently bypass system SSH policy.

Hardware virtualization must be enabled in firmware. A virtual controller also
needs nested KVM enabled by its hypervisor. Missing `/dev/kvm` is not a Python
package problem. Keep enough RAM for the host in addition to all guest allocations.

## First cluster

Obtain a Rocky cloud image and verify the vendor checksum first.

```bash
cabritactl init ./demo --artifact /path/to/Rocky-cloud.qcow2
cabritactl doctor --cluster demo/cluster.yaml
cabritactl up --cluster demo/cluster.yaml --yes
cabritactl verify --cluster demo/cluster.yaml --network --json
```

New manifests select an unused `/24` in `192.168.0.0/16`, a cluster-owned NAT
network, stable MACs and static addresses beginning at `.101`. The DHCP range
`.2`–`.99` is separate. Initialization inspects existing host routes and libvirt
networks; it does not reserve the subnet until `up`. Deployment checks again
under a host allocation lock. A newly introduced VPN/route conflict causes an
explicit failure, not silent renumbering.

Use `--network existing` for offline authoring, then review every network field.
External networks are never created or modified. Static addresses inside an
external DHCP range require matching IP/MAC reservations configured by its owner.

The managed pool is named `cabrita`, normally targeting
`/var/lib/libvirt/images/cabrita`. Override it with `libvirt.storage_pool` to use
an already prepared directory pool. Do not use hidden home-directory paths.
Downloads remain in the disposable user cache; VM disks, base images, and attached
media are uploaded through libvirt. Work logs and retry metadata live under
`~/.local/state/cabrita/clusters/<name>/work`. Disk identities are recorded in
`storage.json`. Golden capture downloads an independent temporary copy of the
stopped disk and its backing image; it does not weaken system storage permissions.

`down` preserves storage and the network. `destroy` removes managed node volumes
and removes its network only when no domains use it. Shared base images remain
available for reuse. There is intentionally no migration command.

## Host firewall

Libvirt supplies NAT and its own network forwarding rules. Preserve its rules
and use its integration with the installed firewall manager. Do not install and
activate a second firewall manager to fix connectivity.

`doctor` reports the detected firewalld/UFW/custom policy state. On an active UFW
host, or for golden recovery HTTP, preview and apply cluster-specific rules:

```bash
cabritactl host setup --cluster demo/cluster.yaml
cabritactl host setup --cluster demo/cluster.yaml --apply
```

UFW rules allow DNS/DHCP on the cluster bridge and forwarding from its subnet.
Firewalld uses libvirt's bridge zone. Recovery HTTP is restricted to the cluster
subnet, gateway destination, and configured port; the server binds to that gateway.
A custom nftables policy or simultaneous firewall managers requires administrator
review. Inaccessible policy is reported as unknown, never as a successful check.
Commands do not flush tables, disable filtering, enable UFW, or change global
forwarding defaults.

Only rules added by Cabrita are recorded. After destroying the cluster:

```bash
cabritactl host cleanup --cluster demo/cluster.yaml
cabritactl host cleanup --cluster demo/cluster.yaml --apply
```

Keep the original manifest until cleanup is complete. Changed manifests and
unrecognized/tampered rule receipts are rejected. Package/service setup is not
undone by firewall cleanup.

## Guest firewall and connectivity

The Rocky HPC profile creates a dedicated `cabrita-peers` zone. Only declared
peer `/32` addresses receive access for MPI's dynamic ports. Peer membership is
reconciled on configuration; stale Cabrita entries are removed. NFS exports remain
restricted to declared clients and use NFSv4 over TCP.

Management SSH uses source-scoped rules. The default controller source is the
libvirt gateway (or the Helvetios bastion address). Override
`configuration.inputs.management_sources` with explicit IPv4 addresses/subnets
when using another management route. Ensure this is correct before configuration.

Diagnose in order: VM running, address/route, controller SSH, guest DNS/HTTPS,
peer SSH, NFS, then MPI. `verify --network` performs guest-side checks; HPL can
then be run on the head node using `/shared/hpl/run_hpl.sh`. Ping failure alone
does not identify a firewall problem. NAT guests are reached through the
controller; this workflow does not expose their SSH ports on the public interface.

## SELinux and AppArmor

Keep Fedora SELinux **enforcing** and Ubuntu AppArmor **enabled**. Standard libvirt
storage paths allow libvirt to manage QEMU's DAC permissions and sVirt/AppArmor
profiles, including all backing images and attached media. No 0777 directories,
0666 disks, QEMU root account, or `security_driver="none"` workaround is needed.

Fedora diagnostics:

```bash
getenforce
ls -Zd /var/lib/libvirt/images/cabrita
sudo ausearch -m AVC,USER_AVC -ts recent
sudo journalctl -u virtqemud --since '10 minutes ago'
```

If a Cabrita-owned inactive storage path has an incorrect label, inspect the
expected label with `matchpathcon` and use `restorecon` on that path. Do not relabel
running VM disks or blindly generate an `audit2allow` policy. Custom storage
locations need persistent, administrator-reviewed SELinux labeling.

Ubuntu diagnostics:

```bash
sudo aa-status
sudo journalctl -k --since '10 minutes ago' | grep 'apparmor="DENIED"'
sudo journalctl -u libvirtd -u virtqemud --since '10 minutes ago'
```

Cabrita selects ordinary split CODE/VARS firmware for UEFI. On Ubuntu 26.04,
automatic firmware selection can otherwise choose an AMD-SEV monolithic ROM
that `virt-aa-helper` rejects. The firmware fix requires no AppArmor exception.

Inspect both the per-domain profile and `virt-aa-helper` denials. Ubuntu's helper
can explicitly deny hidden home-directory paths; ordinary chmod/ACL adjustments
cannot override that policy. The managed pool avoids those paths. Do not disable
the helper or add blanket home-directory access.

`doctor` distinguishes static inspection from actual QEMU access. It cannot prove
all runtime confinement decisions before a VM exists; it reports that limitation
as unknown. Startup errors include individual node failures and retained log paths.

## Sources and validation

- [Fedora KVM packages](https://packages.fedoraproject.org/pkgs/libvirt/libvirt-daemon-kvm/)
- [Fedora OVMF](https://packages.fedoraproject.org/pkgs/edk2/edk2-ovmf/)
- [Ubuntu libvirt installation](https://ubuntu.com/server/docs/how-to/virtualisation/libvirt/)
- [Ubuntu firewall](https://documentation.ubuntu.com/server/how-to/security/firewalls/index.html)
- [Libvirt daemons](https://libvirt.org/daemons.html)
- [Libvirt storage](https://libvirt.org/formatstorage.html)
- [Backing images and confinement](https://libvirt.org/kbase/backing_chains.html)
- [Libvirt firewalld integration](https://libvirt.org/firewall.html)

Run the opt-in managed-host acceptance test on each distro; package documentation
is not evidence that a host passed boot/network/confinement tests. See
[release validation](release-validation.md) for recorded results and limitations.
