# SCC CARLA 2026 preparation and recovery

Initialize the preserved competition profile with an installer ISO:

```bash
cabrita init ./competition --provider helvetios --artifact /path/to/Rocky-minimal.iso
```

Review every node's BMC endpoint, management IP/MAC, target disk, hardware, user,
and InfiniBand address. Set the manifest's bastion SSH alias and HTTP address to
reachable endpoints. Supply `CABRITA_BMC_USER` and `CABRITA_BMC_PASSWORD` in the
controller environment. Credentials are deployment inputs, not repository data.

First installation defaults to embedded Kickstart and bastion preparation.
Prefer a verified ISO URL to download/cache it on the bastion; local ISO inputs
use resumable upload. Run `validate`, `doctor`, and `plan` before `up`.
See [bootstrap methods](bootstrap-methods.md) for local build and OEMDRV options.

Use the [shared HPC profile](hpc-configuration.md) to configure the declared IB
interfaces, ranks, storage, Spack environment, and HPL grid. Collect HPL output
through SSH and check numerical success before interpreting performance.

Prepare and validate a compatible libvirt software image, shut it down, and
[capture it](golden-recovery.md). Preserve its metadata and compressed payload
outside cluster state. Restore through the installer ISO and the bastion's
internal HTTP endpoint. Recovery verifies compatibility and integrity before
writing only the declared target disk, then regenerates node identity. Shared
configuration re-establishes NFS roles, peer keys, and benchmark inputs.

Physical acceptance remains required: virtual-media boot, disk selection,
restore, BIOS settings, InfiniBand, full HPL, and the actual submission rules.
Record hardware, firmware, manifest checksums, logs, numerical results, and
organizer requirements with the run. VM acceptance does not establish those
hardware or competition claims.
