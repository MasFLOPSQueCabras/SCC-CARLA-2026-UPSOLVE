# Troubleshooting

See [host installation and confinement diagnostics](host-installation.md) for
Fedora 44 and Ubuntu 26.04. `doctor --json` reports pass/fail/warning/unknown
checks; unknown does not mean a prerequisite was verified.

Run `cabritactl doctor --cluster cluster.yaml` first. Missing native tools, SSH
keys, provider libraries, or provider access must be corrected before deployment.
Provider extras install Python libraries; they do not install host services.

For libvirt authorization errors, run `virsh -c qemu:///system list --all` in a
terminal and complete the host's authentication flow. Tool approval does not
grant polkit permission. Check that KVM, the selected libvirt network, and OVMF
(for UEFI) are available.

For installer hangs, inspect the VM console and retained bootstrap logs. Verify
that the ISO matches its checksum, the chosen method matches the manifest, and
Kickstart ends with poweroff. Cabrita detaches installation media before disk
boot and SSH verification.

Golden recovery needs enough installer RAM to stage the entire compressed
payload. Rocky uses roughly 20% of RAM for `/run`; the HPC acceptance image uses
8 GiB recovery VMs. Insufficient space or checksum mismatch fails before disk
writes. Ensure guests can reach the selected host/bastion HTTP port. Permit only
the necessary network/port in the host firewall; use explicit `cabritactl host setup --cluster cluster.yaml --apply` for scoped rules.

NFS recovery has a grace period during which new opens wait. Shared configuration
waits for its completion before mounting clients. Use `down` for orderly cluster
shutdown so clients stop before the NFS server. Check exports, peer firewall
rules, and the declared storage addresses if reads still fail.

Failed operations return nonzero and retain checkpoint errors and work logs in
Cabrita's persistent state directory. Retry `up` after correcting the cause; do not
use `--reinstall` unless replacement is intended. Active locks release when their
owning process exits. Do not delete a lock file to bypass a live operation.

Infrastructure tests preserve diagnostics under `test-results/e2e` or the
selected `--e2e-log-dir`. A skipped test is not deployment evidence.
