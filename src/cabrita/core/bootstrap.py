"""User-authored bootstrap inputs and provider-independent preparation contracts."""

import hashlib
import json
import shlex
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BootstrapMethod(StrEnum):
    CLOUD_INIT = "cloud-init"
    EMBEDDED_KICKSTART = "embedded-kickstart"
    OEMDRV = "oemdrv"
    GOLDEN_RESTORE = "golden-restore"
    CUSTOM = "custom"


class ArtifactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    format: Literal["qcow2", "iso", "raw.zst"]

    def verify(self, path: Path) -> None:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != self.sha256.lower():
            raise ValueError(f"Artifact checksum mismatch: {path}")


class PreparationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    argv: list[str] = Field(min_length=1)
    execution: Literal["local", "bastion"] = "local"
    timeout: float = Field(default=600, gt=0, le=86400)

    @model_validator(mode="after")
    def validate_command(self) -> Self:
        if not self.argv[0] or any("\0" in part for part in self.argv):
            raise ValueError(
                "Preparation requires a nonempty executable and NUL-free arguments"
            )
        return self


class BootstrapSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: BootstrapMethod = BootstrapMethod.CLOUD_INIT
    artifact: str | None = None
    payload: str | None = None
    user_data: Path | None = None
    network_config: Path | None = None
    kickstart: Path | None = None
    templates: Path | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    prepare: PreparationSpec | None = None
    build_on: Literal["local", "bastion"] = "local"

    @model_validator(mode="after")
    def validate_custom(self) -> Self:
        if self.method == BootstrapMethod.CUSTOM and self.prepare is None:
            raise ValueError("Custom bootstrap requires prepare.argv")
        if self.method != BootstrapMethod.CUSTOM and self.prepare is not None:
            raise ValueError("prepare is only valid for custom bootstrap")
        return self


class ConfigurationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Literal["none", "lightweight", "scc-carla-2026", "custom"] = "none"
    playbook: Path | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_custom_playbook(self) -> Self:
        if self.profile == "custom" and self.playbook is None:
            raise ValueError("Custom configuration requires a playbook")
        return self


@dataclass(frozen=True, slots=True)
class PreparationResult:
    artifact: ArtifactSpec
    path: Path | str
    execution: Literal["local", "bastion"]


class CommandRunner(Protocol):
    def __call__(
        self, argv: list[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]: ...


def run_command(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, check=True, capture_output=True, text=True, timeout=timeout
    )


class CustomPreparer:
    """Runs argv without a local shell; remote arguments are shell-quoted once.

    The executable receives --context and --output paths. It must atomically
    produce a JSON ArtifactSpec with an absolute source and a verified checksum.
    stdout/stderr and the input context are retained for failures and timeouts.
    """

    def __init__(self, runner: CommandRunner = run_command) -> None:
        self.runner = runner

    def prepare(
        self,
        spec: PreparationSpec,
        context: dict[str, Any],
        work_dir: Path,
        *,
        bastion: str | None = None,
        remote_dir: str | None = None,
    ) -> PreparationResult:
        work_dir.mkdir(parents=True, exist_ok=True)
        context_path = work_dir / "context.json"
        output_path = work_dir / "artifact.json"
        context_path.write_text(json.dumps(context, indent=2), encoding="utf-8")
        context_path.chmod(0o600)
        # A previous successful output cannot hide a failed or incomplete attempt.
        output_path.unlink(missing_ok=True)
        try:
            match spec.execution:
                case "local":
                    argv = [
                        *spec.argv,
                        "--context",
                        str(context_path),
                        "--output",
                        str(output_path),
                    ]
                    completed = self.runner(argv, timeout=spec.timeout)
                case "bastion":
                    if not bastion or not remote_dir or not remote_dir.startswith("/"):
                        raise ValueError(
                            "Bastion preparation requires a host and absolute remote directory"
                        )
                    if bastion.startswith("-"):
                        raise ValueError("Invalid bastion host")
                    remote_context = f"{remote_dir}/context.json"
                    remote_output = f"{remote_dir}/artifact.json"
                    self.runner(
                        [
                            "ssh",
                            "-o",
                            "BatchMode=yes",
                            "-o",
                            "ConnectTimeout=10",
                            bastion,
                            shlex.join(["mkdir", "-p", remote_dir]),
                        ],
                        timeout=spec.timeout,
                    )
                    self.runner(
                        [
                            "scp",
                            "-o",
                            "BatchMode=yes",
                            "-o",
                            "ConnectTimeout=10",
                            str(context_path),
                            f"{bastion}:{remote_context}",
                        ],
                        timeout=spec.timeout,
                    )
                    self.runner(
                        [
                            "ssh",
                            "-o",
                            "BatchMode=yes",
                            "-o",
                            "ConnectTimeout=10",
                            bastion,
                            shlex.join(["rm", "-f", remote_output]),
                        ],
                        timeout=spec.timeout,
                    )
                    completed = self.runner(
                        [
                            "ssh",
                            "-o",
                            "BatchMode=yes",
                            "-o",
                            "ConnectTimeout=10",
                            bastion,
                            shlex.join(
                                [
                                    *spec.argv,
                                    "--context",
                                    remote_context,
                                    "--output",
                                    remote_output,
                                ]
                            ),
                        ],
                        timeout=spec.timeout,
                    )
                    self.runner(
                        [
                            "scp",
                            "-o",
                            "BatchMode=yes",
                            "-o",
                            "ConnectTimeout=10",
                            f"{bastion}:{remote_output}",
                            str(output_path),
                        ],
                        timeout=spec.timeout,
                    )
            (work_dir / "prepare.log").write_text(completed.stdout + completed.stderr)
            if completed.returncode != 0:
                raise RuntimeError(
                    f"Preparation exited with status {completed.returncode}"
                )
            artifact = ArtifactSpec.model_validate_json(output_path.read_text())
            path = Path(artifact.source)
            if not path.is_absolute():
                raise ValueError(
                    "Preparation output must name an absolute artifact path"
                )
            if spec.execution == "local":
                artifact.verify(path)
            else:
                checked = self.runner(
                    [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "ConnectTimeout=10",
                        str(bastion),
                        shlex.join(["sha256sum", "--", artifact.source]),
                    ],
                    timeout=spec.timeout,
                )
                if (
                    checked.returncode != 0
                    or checked.stdout.split()[0] != artifact.sha256.lower()
                ):
                    raise ValueError("Remote preparation artifact checksum mismatch")
            return PreparationResult(artifact, path, spec.execution)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            (work_dir / "prepare.log").write_text(
                f"{exc}\nstdout: {exc.stdout!r}\nstderr: {exc.stderr!r}\n"
            )
            raise
