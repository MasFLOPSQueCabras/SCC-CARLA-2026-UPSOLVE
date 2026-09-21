# Lifecycle and recovery

Use `cabrita init --artifact /path/to/base.qcow2`, then `validate`, `doctor`,
`plan`, `up`, and `verify`. Select the same manifest with `--cluster` and any
subset with repeated `--node` options. `plan`, `status`, and `verify` support
`--json`. Mutation commands accept `--yes` and `--dry-run`.

`up` checkpoints installation and configuration separately. Repeated execution
verifies ready nodes without replacing their disks. Failed configuration can be
retried without OS installation. Changing provisioning inputs or replacing a
missing installed VM requires explicit `--reinstall`. Unmanaged live resources
are protected. Configuration changes trigger configuration without replacement.

`deploy` only deploys the OS; `configure` runs the selected playbook. A required
node failure aborts configuration. Failure details persist under the cluster's
state directory; Ansible logs remain under its work directory.

`down` stops nodes while preserving disks. `destroy` removes managed virtual
machines and their disks. Physical destruction ejects media and powers off;
it does not wipe disks. Cached base and golden artifacts are independent of
cluster state and destruction.

Power, BIOS, SSH, and lock commands use the manifest's targets. Examples:

```bash
cabrita power status --cluster cluster.yaml --node 10
cabrita power off --cluster cluster.yaml --node 10 --yes
cabrita ssh --cluster cluster.yaml --node 10 -- uname -a
cabrita bios show --cluster helvetios.yaml --json
cabrita lock list --cluster cluster.yaml
```

Local locks use flock; Helvetios locks are held on the bastion by an SSH session.
Locks identify the actual virtual resource or BMC endpoint. They release on
process exit and cannot be forcibly broken while an operation remains active.

`access.public_key` and `access.private_key` default to the user's Ed25519 key.
`access.timeout` bounds installation verification and configuration;
`access.max_workers` limits parallelism to between one and eight workers.
