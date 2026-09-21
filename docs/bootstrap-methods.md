# Bootstrap methods

Choose a method explicitly in `cluster.yaml`. Cached files never change the
selected method. Artifact sources may be manifest-relative paths or HTTP(S)
URLs; every artifact requires a SHA-256 checksum.

| Method | Input | Installation media |
|---|---|---|
| `cloud-init` | A cloud qcow2 image | Independent overlay and CIDATA ISO |
| `embedded-kickstart` | Installer ISO | Kickstart embedded in a rebuilt ISO |
| `oemdrv` | Installer ISO | Installer ISO plus an OEMDRV FAT disk containing Kickstart |

Libvirt supports these three methods. Helvetios supports the two installer
methods. Competition initialization selects `embedded-kickstart`.

```yaml
bootstrap:
  method: embedded-kickstart
  artifact: installer
  kickstart: templates/ks.cfg.j2
  build_on: bastion
artifacts:
  installer:
    source: images/installer.iso
    sha256: REPLACE_WITH_SHA256
    format: iso
```

Use `build_on: local` for libvirt. For Helvetios, `bastion` downloads URL sources
and builds media on the bastion; local ISO sources are uploaded with resumable
rsync. Explicit `local` builds upload completed artifacts. Builds preserve the
source ISO's BIOS and UEFI boot metadata through xorriso's replay operation.
Base images and completed builds are checksum verified and published atomically.
The bastion checks required tools and free space before preparing media.

Provide custom cloud-init files with `bootstrap.user_data` and
`bootstrap.network_config`, or a Kickstart file with `bootstrap.kickstart`.
These files accept Jinja template inputs. Paths resolve relative to the manifest.
Installer Kickstarts must shut down after installation so Cabrita can detach
media and boot the installed disk. Cloud-init must complete successfully before
its seed is detached. Installation verification checks SSH after disk reboot.

Native tools include QEMU/libvirt, SSH, xorriso, dosfstools (`mkfs.vfat`), and
mtools (`mcopy`). Bastion builds additionally require Python 3.11 or later,
curl, rsync, sha256sum, and flock. Its HTTP endpoint must be reachable from the
BMC. Cabrita owns the media server for the duration of installation and releases
it with its deployment session; it never terminates another port owner's server.

Run real acceptance tests using your own image paths:

```bash
uv run pytest -c pyproject.toml tests -m e2e --run-e2e \
  --provider=libvirt \
  --installer-iso=/path/to/installer.iso \
  --cloud-image=/path/to/cloud.qcow2
```

`--bootstrap=cloud-init`, `--bootstrap=embedded-kickstart`, or `--bootstrap=oemdrv`
selects a method. The matrix covers both BIOS and UEFI. Missing prerequisites
fail explicitly. Each attempt retains logs under `test-results/e2e/`; passing
libvirt tests is not evidence of physical Helvetios verification.
