# Golden-image capture and recovery

Configure a libvirt node once, verify its services, then stop it and capture its
managed disk:

```bash
cabritactl down --cluster cluster.yaml --node 1 --yes
cabritactl image capture --cluster cluster.yaml --node 1 --output ./golden
cabritactl image inspect ./golden/golden.json
sha256sum ./golden/golden.json ./golden/golden.raw.zst ./images/installer.iso
```

The node must have a successful `ready` checkpoint and be powered off. Capture
creates an independent `golden.qcow2`, a compressed raw payload, and metadata in
a new directory. Existing destinations are refused. Failed captures retain a
hidden `.NAME.capture-*` working directory and `capture.log` for diagnosis.
Destroying the source cluster leaves the capture and artifact cache intact.

Capture requires QEMU, libguestfs (`virt-inspector` and `virt-sysprep`), and zstd.
The built-in recovery supports Linux guests with NetworkManager, systemd,
OpenSSH, sudo, and SELinux tools. The root filesystem must be a plain partition
on the captured disk; separate `/boot` and `/boot/efi` partitions are supported.
Separate home/var/usr filesystems, LVM, encrypted disks, and multi-disk captures
are rejected. Rocky Linux 10 is the acceptance-test guest. Other layouts need
a user-authored custom bootstrap method.

Capture removes SSH keys, machine identity, DHCP state, and source cloud-init
state, and disables passwords for root and the provisioning user. Application
secrets and other local accounts are the author's responsibility: remove or
rotate them before capture.

## Restore manifest

Use a fresh cluster identity and declare each target node, MAC address, IP,
firmware, and disk capacity. Keep normal access and network settings in the
manifest. Replace the checksum placeholders with the values from `sha256sum`:

```yaml
bootstrap:
  method: golden-restore
  artifact: installer
  payload: golden
  metadata: metadata
  build_on: local
  inputs:
    http_port: 8072
artifacts:
  installer:
    source: images/installer.iso
    sha256: REPLACE_WITH_INSTALLER_SHA256
    format: iso
  golden:
    source: golden/golden.raw.zst
    sha256: REPLACE_WITH_PAYLOAD_SHA256
    format: raw.zst
  metadata:
    source: golden/golden.json
    sha256: REPLACE_WITH_METADATA_SHA256
    format: json
```

Paths are relative to the manifest. The metadata records source, architecture,
firmware, disk size, checksum, and provisioning inputs. Targets must match the
architecture and firmware and have at least the recorded disk capacity.
The installer must have enough RAM-backed `/run` space for the compressed
payload, in addition to its own runtime memory.

For libvirt, Cabrita serves cached payloads on the manifest's network gateway;
override the address with `bootstrap.inputs.http_bind_ip`. The selected TCP port
must be reachable from guests through the host firewall. Cabrita does not change
firewall rules. For Helvetios, use `build_on: bastion` to prepare media there;
the bastion HTTP endpoint must be reachable from both the BMC and the booted
installer. Only the declared `hardware.target_disk` is restored.

Run `validate`, `doctor`, `plan`, and `up`, then `verify`. Restoration verifies the
payload checksum and complete compressed frame before writing the target disk.
It applies hostname, static networking, and authorized keys, regenerates machine
and SSH host identity, detaches installation media, and boots from disk. A unique
recovery marker is checked over SSH; interrupted recovery cannot become ready
merely because an old system responds. Repeated `up` preserves installed nodes;
replacement requires `--reinstall`.

## Verification

The real libvirt test captures a working HTTP service, destroys its source,
restores two nodes through ISO plus HTTP, checks distinct identities and SSH
host keys, rejects the source login key, and verifies the captured service and
disk boot. It runs with both BIOS and UEFI:

```bash
uv run pytest -m e2e --run-e2e --provider libvirt --bootstrap golden-restore \
  --installer-iso /path/to/Rocky-minimal.iso \
  --cloud-image /path/to/Rocky-cloud.qcow2
```

Tests use the default libvirt subnet and port 18072 for payload delivery. Failed
attempts retain console, SSH, preparation, and service logs in `test-results/e2e`.
Helvetios uses the same restore program, but physical media boot and recovery
still require separate hardware acceptance; libvirt results do not prove them.

The old `image build` and `image export` interfaces are removed. Use `image
capture` with an explicit managed source node and output directory.
