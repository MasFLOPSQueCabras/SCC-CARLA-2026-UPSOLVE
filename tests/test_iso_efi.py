"""Exercise real FAT/ISO remastering without booting a VM or physical machine."""

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from cabritactl.bootstrap.iso_builder import build_iso


@pytest.mark.skipif(
    any(
        shutil.which(tool) is None
        for tool in ("xorriso", "mkfs.vfat", "mmd", "mcopy", "mtype")
    ),
    reason="requires native ISO and FAT tools",
)
def test_kickstart_updates_iso_eltorito_and_gpt_efi_menu(tmp_path: Path):
    def run(*argv):
        return subprocess.run(argv, check=True, capture_output=True, text=True).stdout

    tree = tmp_path / "tree"
    (tree / "EFI/BOOT").mkdir(parents=True)
    (tree / "images").mkdir()
    menu = tree / "EFI/BOOT/grub.cfg"
    menu.write_text(
        'set default="1"\nset timeout=60\nmenuentry install {\n linuxefi /vmlinuz quiet\n}\n'
    )
    fat = tree / "images/efiboot.img"
    with fat.open("wb") as stream:
        stream.truncate(8 * 1024**2)
    run("mkfs.vfat", str(fat))
    run("mmd", "-i", str(fat), "::/EFI", "::/EFI/BOOT")
    run("mcopy", "-i", str(fat), str(menu), "::/EFI/BOOT/grub.cfg")
    base = tmp_path / "base.iso"
    # Create a distinct prepended GPT EFI copy, as in Rocky's production ISO.
    run(
        "xorriso",
        "-as",
        "mkisofs",
        "-o",
        str(base),
        "-V",
        "CABRITA_TEST",
        "-e",
        "images/efiboot.img",
        "-no-emul-boot",
        "-efi-boot-part",
        str(fat),
        str(tree),
    )
    ks = tmp_path / "ks.cfg"
    ks.write_text("poweroff\n")
    built = build_iso(base, ks, tmp_path / "unattended.iso")
    outer = tmp_path / "outer.cfg"
    run(
        "xorriso",
        "-osirrox",
        "on",
        "-indev",
        str(built),
        "-extract",
        "/EFI/BOOT/grub.cfg",
        str(outer),
    )
    inner = tmp_path / "inner.img"
    run(
        "xorriso",
        "-osirrox",
        "on",
        "-indev",
        str(built),
        "-extract",
        "/images/efiboot.img",
        str(inner),
    )
    with built.open("rb") as iso:
        iso.seek(512)
        header = iso.read(512)
        assert header[:8] == b"EFI PART"
        table = struct.unpack_from("<Q", header, 72)[0]
        count, size = struct.unpack_from("<II", header, 80)
        iso.seek(table * 512)
        entries = iso.read(count * size)
        efi = next(
            entries[i : i + size]
            for i in range(0, len(entries), size)
            if entries[i : i + 16] == bytes.fromhex("28732ac11ff8d211ba4b00a0c93ec93b")
        )
        first, last = struct.unpack_from("<QQ", efi, 32)
        iso.seek(first * 512)
        gpt = tmp_path / "gpt.img"
        gpt.write_bytes(iso.read((last - first + 1) * 512))
    for text in (
        outer.read_text(),
        run("mtype", "-i", str(inner), "::/EFI/BOOT/grub.cfg"),
        run("mtype", "-i", str(gpt), "::/EFI/BOOT/grub.cfg"),
    ):
        assert 'set default="0"' in text
        assert "set timeout=1" in text
        assert "inst.ks=cdrom:/ks.cfg" in text
        assert "console=ttyS0,115200n8" in text
