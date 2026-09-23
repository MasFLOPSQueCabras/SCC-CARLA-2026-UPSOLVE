"""Bastion media preparation and explicitly requested resumable local uploads."""

import hashlib
import shlex
import subprocess
from pathlib import Path
from urllib.parse import quote

from cabritactl.bootstrap import iso_builder
from cabritactl.bootstrap.artifacts import ArtifactCache, artifact_lock
from cabritactl.bootstrap.media import prepare_media, render_kickstart
from cabritactl.bootstrap.recovery import render_recovery
from cabritactl.core.bootstrap import ArtifactSpec, BootstrapMethod
from cabritactl.core.manifest import NodeSpec
from cabritactl.core.resolved import ResolvedCluster
from cabritactl.core.templating import TemplateEngine


class BastionMedia:
    def __init__(self, cluster: ResolvedCluster, templates: TemplateEngine) -> None:
        self.cluster = cluster
        self.templates = templates
        self.host = cluster.manifest.bastion.ssh_host
        if self.host.startswith("-"):
            raise ValueError("Invalid bastion host")

    def _remote(
        self, argv: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                self.host,
                shlex.join(argv),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=1900,
        )
        if check and result.returncode:
            raise RuntimeError(
                f"Bastion command failed (exit {result.returncode}): "
                f"{result.stderr[-6000:]}"
            )
        return result

    def _upload(self, source: Path, target: str) -> None:
        subprocess.run(
            [
                "rsync",
                "--partial",
                "--append-verify",
                "--protect-args",
                "-e",
                "ssh -o BatchMode=yes -o ConnectTimeout=10",
                str(source),
                f"{self.host}:{target}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=1900,
        )

    def _publish_local(self, source: Path, target: str, cache: ArtifactCache) -> None:
        cache.directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256((self.host + target).encode()).hexdigest()
        with artifact_lock(cache.directory / f"upload-{key}.lock"):
            self._upload_verified(source, target)

    def _upload_verified(self, source: Path, target: str) -> None:
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        existing = self._remote(["sha256sum", "--", target], check=False)
        if existing.returncode == 0 and existing.stdout.split()[0] == digest:
            return
        partial = target + ".partial"
        self._upload(source, partial)
        checked = self._remote(["sha256sum", "--", partial])
        if checked.stdout.split()[0] != digest:
            raise ValueError("Uploaded media checksum mismatch")
        self._remote(["mv", "--", partial, target])

    def _materialize(
        self, artifact: ArtifactSpec, root: str, cache: ArtifactCache
    ) -> str:
        base = root + f"/artifacts/{artifact.sha256.lower()}.{artifact.format}"
        if artifact.source.startswith(("http://", "https://")):
            partial = base + ".partial"
            check_base = shlex.join(["sha256sum", "--", base])
            download = shlex.join(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--retry",
                    "3",
                    "--max-time",
                    "1800",
                    "--continue-at",
                    "-",
                    "--output",
                    partial,
                    artifact.source,
                ]
            )
            verify = shlex.join(
                [
                    "python3",
                    "-c",
                    "import hashlib,sys; f=open(sys.argv[1],'rb'); assert hashlib.file_digest(f,'sha256').hexdigest()==sys.argv[2], 'Checksum mismatch'",
                    partial,
                    artifact.sha256.lower(),
                ]
            )
            script = f"if test -f {shlex.quote(base)}; then {check_base}; else {download} && {verify} && {shlex.join(['mv', partial, base])}; fi"
            self._remote(["flock", "-w", "1800", base + ".lock", "sh", "-c", script])
            checked = self._remote(["sha256sum", "--", base])
            if checked.stdout.split()[0] != artifact.sha256.lower():
                raise ValueError("Cached bastion artifact checksum mismatch")
        else:
            artifact.verify(Path(artifact.source))
            self._publish_local(Path(artifact.source), base, cache)
        return base

    def prepare(
        self, node: NodeSpec, public_key: str, work: Path, cache: ArtifactCache
    ) -> tuple[str, str | None]:
        manifest = self.cluster.manifest
        bootstrap = manifest.bootstrap
        if bootstrap.method not in (
            BootstrapMethod.EMBEDDED_KICKSTART,
            BootstrapMethod.OEMDRV,
            BootstrapMethod.GOLDEN_RESTORE,
        ):
            raise ValueError(
                f"Unsupported bastion media preparation: {bootstrap.method}"
            )
        assert bootstrap.artifact is not None
        artifact = manifest.artifacts[bootstrap.artifact]
        root = manifest.bastion.remote_serve_dir
        if root.startswith("~/"):
            home = self._remote(
                ["python3", "-c", "from pathlib import Path; print(Path.home())"]
            ).stdout.strip()
            root = home + root[1:]
        if not root.startswith("/"):
            raise ValueError(
                "Bastion media directory must be absolute or start with ~/"
            )
        self._remote(["mkdir", "-p", root + "/artifacts"])
        required_tools = ["sha256sum", "flock", "rsync"]
        if bootstrap.build_on == "bastion":
            required_tools += ["xorriso", "curl", "mcopy"]
            if bootstrap.method == BootstrapMethod.OEMDRV:
                required_tools += ["mkfs.vfat"]
        self._remote(
            [
                "python3",
                "-c",
                "import shutil,sys; missing=[x for x in sys.argv[1:] if shutil.which(x) is None]; assert not missing, 'Missing bastion tools: '+str(missing)",
                *required_tools,
            ]
        )
        self._remote(
            [
                "python3",
                "-c",
                "import os,sys; s=os.statvfs(sys.argv[1]); assert s.f_bavail*s.f_frsize >= int(sys.argv[2]), 'Insufficient bastion media storage'",
                root,
                str(bootstrap.inputs.get("required_free_bytes", 8 * 1024**3)),
            ]
        )
        prefix = f"http://{manifest.bastion.http_bind_ip}:{manifest.bastion.http_port}/"
        if bootstrap.method == BootstrapMethod.GOLDEN_RESTORE:
            assert bootstrap.payload is not None
            payload = manifest.artifacts[bootstrap.payload]
            self._materialize(payload, root, cache)
            kickstart = render_recovery(
                self.cluster,
                node,
                cache,
                public_key,
                work,
                prefix + f"artifacts/{payload.sha256.lower()}.{payload.format}",
            )
        else:
            kickstart = render_kickstart(
                self.cluster, node, self.templates, public_key, work
            )
        key = hashlib.sha256(
            (
                artifact.sha256
                + bootstrap.method.value
                + kickstart.read_text()
                + Path(iso_builder.__file__).read_text()
            ).encode()
        ).hexdigest()
        relative = f"artifacts/{key}.iso"
        output = root + "/" + relative
        auxiliary = (
            output.removesuffix(".iso") + ".img"
            if bootstrap.method == BootstrapMethod.OEMDRV
            else None
        )
        if bootstrap.build_on == "local":
            media = prepare_media(
                self.cluster,
                node,
                cache,
                self.templates,
                public_key,
                work,
                kickstart=kickstart,
            )
            self._publish_local(media.source, output, cache)
            if media.auxiliary is not None:
                assert auxiliary is not None
                self._publish_local(media.auxiliary, auxiliary, cache)
        else:
            base = self._materialize(artifact, root, cache)
            builder = Path(iso_builder.__file__)
            script_key = hashlib.sha256(builder.read_bytes()).hexdigest()
            remote_builder = root + f"/artifacts/builder-{script_key}.py"
            remote_ks = root + f"/artifacts/{key}.cfg"
            self._publish_local(builder, remote_builder, cache)
            self._publish_local(kickstart, remote_ks, cache)
            command = ["python3", remote_builder, base, remote_ks, output]
            if auxiliary:
                command.append("--oemdrv")
            self._remote(["flock", "-w", "1800", output + ".lock", *command])
        prefix = f"http://{manifest.bastion.http_bind_ip}:{manifest.bastion.http_port}/"
        return prefix + quote(relative), prefix + quote(
            relative.removesuffix(".iso") + ".img"
        ) if auxiliary else None
