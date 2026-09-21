import hashlib
import json
from compression import zstd
from pathlib import Path

import pytest

from cabrita.bootstrap.artifacts import ArtifactCache
from cabrita.bootstrap.recovery import load_metadata
from cabrita.bootstrap.restore_runtime import decompress, download, restore
from cabrita.core.manifest import parse_manifest
from cabrita.core.resolved import ResolvedCluster


def test_decoder_preserves_bytes_and_rejects_truncation(tmp_path: Path) -> None:
    original = b"golden filesystem blocks" * 100_000
    payload = tmp_path / "disk.zst"
    output = tmp_path / "restored.raw"
    payload.write_bytes(zstd.compress(original))
    assert decompress(payload, output, len(original)) == len(original)
    assert output.read_bytes() == original
    payload.write_bytes(payload.read_bytes()[:-1])
    with pytest.raises(ValueError, match="Truncated"):
        decompress(payload, output, len(original))


def test_decoder_refuses_oversized_payload(tmp_path: Path) -> None:
    payload = tmp_path / "disk.zst"
    payload.write_bytes(zstd.compress(b"large" * 100))
    with pytest.raises(ValueError, match="exceeds declared"):
        decompress(payload, tmp_path / "output", 10)


def test_payload_download_checks_integrity(tmp_path: Path) -> None:
    payload = tmp_path / "payload.zst"
    payload.write_bytes(b"content")
    with pytest.raises(ValueError, match="integrity"):
        download(payload.as_uri(), tmp_path / "download", "a" * 64, 7)


def test_restore_refuses_regular_file_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "important-data"
    target.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="block device"):
        restore({"metadata": {}, "identity": {}, "target_disk": str(target)})
    assert target.read_bytes() == b"preserve"


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"firmware": "efi"}, "firmware mismatch"),
        ({"disk_bytes": 100 * 1024**3}, "smaller than"),
        ({"sha256": "b" * 64}, "checksums differ"),
    ],
)
def test_incompatible_golden_metadata_rejected_before_preparation(
    tmp_path: Path, change: dict, expected: str
) -> None:
    import platform

    metadata = {
        "schema_version": 1,
        "sha256": "a" * 64,
        "source": "captured.qcow2",
        "architecture": platform.machine(),
        "firmware": "bios",
        "compressed_bytes": 10,
        "disk_bytes": 1024**3,
        "root_partition": 4,
        "username": "cabrita",
        "provisioning": {},
    }
    metadata.update(change)
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(metadata))
    manifest = parse_manifest("""name: restore-test
nodes:
  - {id: 7, hostname: restored, ip: 192.0.2.7, mac: '52:54:00:00:00:07'}
""")
    from cabrita.core.bootstrap import ArtifactSpec

    manifest.bootstrap.payload = "payload"
    manifest.bootstrap.metadata = "metadata"
    manifest.artifacts = {
        "payload": ArtifactSpec(
            source="payload.raw.zst", sha256="a" * 64, format="raw.zst"
        ),
        "metadata": ArtifactSpec(
            source=str(path),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            format="json",
        ),
    }
    cluster = ResolvedCluster(tmp_path / "cluster.yaml", manifest)
    with pytest.raises(ValueError, match=expected):
        load_metadata(cluster, manifest.nodes[0], ArtifactCache(tmp_path / "cache"))


def test_invalid_compressed_frame_does_not_modify_target(tmp_path: Path) -> None:
    from cabrita.bootstrap.restore_runtime import write_verified_payload

    payload = tmp_path / "truncated.zst"
    data = b"disk blocks" * 100_000
    payload.write_bytes(zstd.compress(data)[:-1])
    disk = tmp_path / "disk"
    disk.write_bytes(b"preserve existing disk")
    with pytest.raises(ValueError, match="Truncated"):
        write_verified_payload(payload, disk, len(data))
    assert disk.read_bytes() == b"preserve existing disk"


@pytest.mark.parametrize(
    "phase,power,message",
    [("ready", "ON", "Shut down"), ("new", "OFF", "configured, ready")],
)
def test_capture_refuses_unready_or_running_nodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    power: str,
    message: str,
) -> None:
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import Mock

    from typer.testing import CliRunner

    from cabrita.commands import image
    from cabrita.core.lifecycle.service import ResourceLocks
    from cabrita.core.providers.base import PowerState

    manifest = parse_manifest("""name: capture-test
nodes:
  - {id: 7, hostname: source, ip: 192.0.2.7, mac: '52:54:00:00:00:07'}
""")
    provider = SimpleNamespace(
        name="libvirt", get_power_status=Mock(return_value=PowerState(power))
    )
    service = SimpleNamespace(
        cluster=ResolvedCluster(tmp_path / "cluster.yaml", manifest),
        backend=SimpleNamespace(provider=provider),
        locks=ResourceLocks(tmp_path / "locks"),
        state=SimpleNamespace(
            read=lambda node: SimpleNamespace(phase=phase, error=None)
        ),
        libvirt_uri="qemu:///system",
    )
    monkeypatch.setattr(image, "service_context", lambda path: nullcontext(service))
    capture_mock = Mock()
    monkeypatch.setattr(image, "capture", capture_mock)
    result = CliRunner().invoke(
        image.image_app,
        ["capture", "--node", "7", "--output", str(tmp_path / "golden")],
    )
    assert result.exit_code == 1
    assert message in result.output
    capture_mock.assert_not_called()


def test_capture_rejects_separate_identity_filesystems() -> None:
    import xml.etree.ElementTree as ET

    from cabrita.bootstrap.golden import validate_system

    system = ET.fromstring("""<operatingsystem><name>linux</name><applications>
<application><name>NetworkManager</name></application>
<application><name>systemd</name></application>
<application><name>openssh-server</name></application>
<application><name>policycoreutils</name></application>
<application><name>sudo</name></application>
</applications><mountpoints><mountpoint dev='/dev/sdb1'>/home</mountpoint></mountpoints></operatingsystem>""")
    with pytest.raises(ValueError, match="Unsupported recovery mount"):
        validate_system(system)


def test_reachable_old_system_cannot_pass_recovery_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from cabrita.core.bootstrap import BootstrapMethod
    from cabrita.core.lifecycle.service import StateStore
    from cabrita.core.providers.base import NodeProvider
    from cabrita.lifecycle import ProviderBackend

    manifest = parse_manifest("""name: verify-restore
nodes:
  - {id: 7, hostname: restored, ip: 192.0.2.7, mac: '52:54:00:00:00:07'}
""")
    manifest.bootstrap.method = BootstrapMethod.GOLDEN_RESTORE
    provider = Mock(spec=NodeProvider)
    provider.paths = Mock(bastion_ssh_host=None)
    backend = ProviderBackend(
        ResolvedCluster(tmp_path / "cluster.yaml", manifest),
        provider,
        StateStore(tmp_path / "state"),
        tmp_path,
        tmp_path / "key.pub",
        tmp_path / "key",
        30,
        ArtifactCache(tmp_path / "cache"),
    )
    work = tmp_path / "node-7"
    work.mkdir()
    (work / "restore.json").write_text(
        json.dumps({"identity": {"token": "current-attempt"}})
    )
    monkeypatch.setattr(backend, "_reachable", lambda node: True)
    monkeypatch.setattr(
        "cabrita.lifecycle.subprocess.run",
        Mock(return_value=Mock(stdout='{"token":"previous-attempt"}')),
    )
    with pytest.raises(ValueError, match="completion marker mismatch"):
        backend.verify(manifest.nodes[0])
