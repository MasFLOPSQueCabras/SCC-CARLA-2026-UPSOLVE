# Helvetios competition workflow

Use [Cabrita (cabritactl)](https://github.com/MasFLOPSQueCabras/cabrita), based on
`1.0.1.dev1` (`3f44b8c`), on `competition/helvetios-submission`. Pin the final
hardware-tested commit before packaging the submission. Development and tests
alone do not establish a valid competition result.

Use the patched `1.0.1.dev1` branch for this allocation. It contains the physical
UEFI/Kickstart boot corrections, HPE virtual-media boot handling, confirmed power
transitions and transient BMC timeout recovery exercised by these hardware runs.
An unpatched `1.0.0` installation does not include these fixes.

## Controller and bastion

```bash
uv sync --locked --extra helvetios
uv run ansible-galaxy collection install -r src/cabritactl/ansible/requirements.yml
ssh scc-bastion 'bash -s' < scripts/helvetios-bastion-setup.sh
uv run python scripts/helvetios-inventory.py
```

The SSH alias `scc-bastion` uses team `scct-2672` and the controller key
`~/.ssh/carla_scc_ed25519`. Set `CABRITA_BMC_USER` and `CABRITA_BMC_PASSWORD` in the
ignored `.env` file. Never copy credentials into the submission. The stage wrapper
also accepts the older `SCC_BMC_*` environment names.

The bastion serves installer media at `10.7.12.102:8072`; its source IP when
connecting to the nodes is `10.2.72.254`. The verified upstream installer is
`Rocky-10.2-x86_64-minimal.iso`, SHA-256:

```text
aac6ac3ce781b91a91ce78463405f66c611a5dca4b3840c79e5e01d97302f6c8
```

The originally cached `.iso` was modified. The separate `.iso.pristine` matched
Rocky's published checksum and was copied to Cabrita's checksum-addressed cache.
Do not substitute similarly named images without checking their contents.

Media tools are extracted from signature-checked Rocky RPMs into the team's home,
with wrappers in `~/.local/bin`. No system packages or shared services are changed.
The deployment owns its temporary HTTP server and shuts it down afterward.

## Verified hardware and stages

BMC inventory identifies three HPE XL230k Gen10 systems, each with two Xeon Gold
6140 processors and 192 GiB RAM. Each system has an 800 GB NVMe boot device:

| Node | BMC | Ethernet MAC | NVMe serial |
| --- | --- | --- | --- |
| node1 | 10.1.72.1 | b8:83:03:53:a4:fa | ZC403005 |
| node2 | 10.1.72.2 | b8:83:03:53:a5:f2 | ZC40255E |
| node3 | 10.1.72.3 | b8:83:03:53:a6:3a | ZC4024DT |

Kickstart explicitly selects the Ethernet MAC and checks the disk serial in
`%pre --erroronfail` before partitioning `/dev/nvme0n1`. Reverify these identities
against live inventory before using this workflow on a different allocation.
Powered-off iLO inventory can be stale: refresh it after POST and compare disk
serials with the installed OS. The live BIOS boot inventory includes NVMe identity.
`verified-manifests.sha256` binds the reviewed manifests and Kickstart together.
The installed OS verifies `ibs5f0` and `mlx5_0:1` with active 100 Gb/s EDR on all
three nodes. HPL uses team-specific IPoIB addresses `10.148.72.1–3/24`.

Run these gates in order. **Each command reinstalls all three declared OS disks.**
Stop on any failure, diagnose it, and repeat the affected gate after fixing it.

```bash
bash scripts/helvetios-stage.sh iso
uv run --locked python scripts/helvetios-reboot-check.py
bash scripts/helvetios-stage.sh basic
bash scripts/helvetios-stage.sh hpl
```

After a transient controller failure, use the same command with `--resume` to
continue the saved installation/configuration state without starting another
fresh install. Use the default command to repeat a gate from scratch.

`iso.yaml` installs only the OS and SSH. `basic.yaml` adds operational packages,
host identities, firewall availability and distinct peer SSH keys. `hpl.yaml`
adds IB, UCX, NFS, system tuning and the source-built Spack HPL environment.
The latter stages repeat configuration to test reuse. Check the configuration
logs as well as the CLI exit status. Reboot the ISO-only installation and confirm
all three hosts return via SSH before moving to the next gate.

Logs, sanitized manifests, BMC inventory, and Cabrita state are retained under
`test-results/helvetios-submission/`. Hardware checkpoints are recorded in
`RUNS.md`; missing checkpoints must not be presented as successful runs.

## Smoke test and tuning

Copy `scripts/hpl-{eval.sh,result.py,tune.sh,tune.py,build-evidence.sh}` and
`competition/helvetios/hpl-settings.sh` to the shared head-node workspace. Set the
verified `UCX_NET_DEVICES` in the settings file. From the head node, run:

```bash
bash /shared/hpl/scripts/hpl-eval.sh /shared/hpl/HPL.dat \
  /shared/hpl/hpl-settings.sh /shared/hpl/results/smoke
bash /shared/hpl/scripts/hpl-build-evidence.sh /shared/hpl/build-evidence
bash /shared/hpl/scripts/hpl-tune.sh /shared/hpl/HPL.dat \
  /shared/hpl/hpl-settings.sh /shared/hpl/tuning --seconds 7200 \
  --hosts 10.148.72.1 10.148.72.2 10.148.72.3
```

The smoke case uses N=4096, NB=128, a 1×3 grid, three ranks and one thread per rank.
Verify IB links, NFS access, peer SSH, loaded MPI/BLAS libraries, and MPI execution
on all three nodes before tuning. The same source-built software is used for
smoke and performance runs. Spack binary caches and concretizer reuse are disabled.

The tuning timer starts after the complete setup passes its smoke test. Two hours
is a maximum: reduce `--seconds` to fit the submission deadline, reserving time
for packaging, replay validation and delivery. For the 2026-09-23 run, the user
confirmed a deadline of 19:43:32 UTC.
It compares physical-core MPI and NUMA-aware hybrid layouts, block sizes
128/192/256/384, and two near-square grids. Larger matrices are limited by available
memory, workspace reserve, measured speed and remaining runtime. Every attempt
has its own immutable input, settings, command, logs, and status. The best result
is chosen only from complete runs passing the residual threshold of 16.0.

## Package and deliver one result

Use the run directory named by `tuning/best.json`, copy it and build evidence to
the controller, and assemble a new directory:

```bash
uv run python scripts/hpl-submit.py /path/to/winning/run /path/to/build-evidence \
  /path/to/new-submission --commit FULL_TESTED_COMMIT_HASH
```

The package contains `input/HPL.dat`, full `output/`, launch and environment
`scripts/`, build evidence, a README linking to Cabrita, and checksums. Test the
packaged launch script against the installed cluster before final delivery.
If HPL source or a recipe patch changes HPL source, include `src/modified_source.zip`
and `src/README.md` describing the changes. Preserve an existing bastion submission
before replacement, copy this single selected result to `~/submission`, and run
`sha256sum --check SHA256SUMS` there. Keep other attempts outside that directory.
