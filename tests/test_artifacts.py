import hashlib
from pathlib import Path

import pytest

from cabritactl.bootstrap.artifacts import ArtifactCache, artifact_lock
from cabritactl.core.bootstrap import ArtifactSpec


def test_cache_reuses_verified_artifact_after_source_removed(tmp_path: Path) -> None:
    source = tmp_path / "base.iso"
    source.write_bytes(b"immutable artifact")
    spec = ArtifactSpec(
        source=str(source),
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        format="iso",
    )
    cache = ArtifactCache(tmp_path / "cache")
    cached = cache.materialize(spec)
    source.unlink()
    assert cache.materialize(spec) == cached
    assert cached.read_bytes() == b"immutable artifact"


def test_corrupt_artifact_never_published(tmp_path: Path) -> None:
    source = tmp_path / "base.iso"
    source.write_bytes(b"wrong content")
    spec = ArtifactSpec(source=str(source), sha256="a" * 64, format="iso")
    cache = ArtifactCache(tmp_path / "cache")
    with pytest.raises(ValueError, match="checksum mismatch"):
        cache.materialize(spec)
    assert not list(cache.directory.glob("*.iso"))
    assert not list(cache.directory.glob("*.partial"))


def test_artifact_lock_wait_is_bounded(tmp_path: Path) -> None:
    with (
        artifact_lock(tmp_path / "artifact.lock"),
        pytest.raises(TimeoutError),
        artifact_lock(tmp_path / "artifact.lock", timeout=0.01),
    ):
        pytest.fail("A held lock must not be acquired twice")


def test_cached_build_checks_both_installer_and_auxiliary_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cabritactl.bootstrap import iso_builder

    built: list[Path] = []

    def build_iso(base: Path, kickstart: Path, output: Path, *, embedded: bool) -> Path:
        built.append(output)
        output.write_bytes(b"installer")
        return output

    def build_oemdrv(kickstart: Path, output: Path) -> Path:
        built.append(output)
        output.write_bytes(b"kickstart")
        return output

    monkeypatch.setattr(iso_builder, "build_iso", build_iso)
    monkeypatch.setattr(iso_builder, "build_oemdrv", build_oemdrv)
    target = tmp_path / "prepared.iso"
    iso_builder.cached_build(tmp_path / "base", tmp_path / "ks", target, embedded=False)
    iso_builder.cached_build(tmp_path / "base", tmp_path / "ks", target, embedded=False)
    assert built == [target, target.with_suffix(".img")]
    target.with_suffix(".img").write_bytes(b"damaged auxiliary media")
    with pytest.raises(ValueError, match="checksum mismatch"):
        iso_builder.cached_build(
            tmp_path / "base", tmp_path / "ks", target, embedded=False
        )


def test_kickstart_uses_declared_subnet_and_disk_bus(tmp_path: Path) -> None:
    from importlib.resources import files

    from cabritactl.bootstrap.media import render_kickstart
    from cabritactl.core.manifest import parse_manifest
    from cabritactl.core.resolved import ResolvedCluster
    from cabritactl.core.templating import TemplateEngine

    manifest = parse_manifest("""
name: authored
network:
  subnet: 192.0.2.0/25
  gateway: 192.0.2.1
defaults:
  vm:
    disk: {bus: scsi}
nodes:
  - {id: 7, hostname: worker, ip: 192.0.2.7, mac: '52:54:00:00:00:07'}
""")
    cluster = ResolvedCluster(tmp_path / "cluster.yaml", manifest)
    templates = TemplateEngine(
        [Path(str(files("cabritactl.providers.helvetios").joinpath("templates")))]
    )
    output = render_kickstart(
        cluster, manifest.nodes[0], templates, "ssh-ed25519 test", tmp_path
    )
    assert "--netmask=255.255.255.128" in output.read_text()
    assert "ignoredisk --only-use=sda" in output.read_text()
