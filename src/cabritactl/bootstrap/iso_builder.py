"""Standalone ISO builder; copy this file to a bastion without installing Cabrita."""

import argparse
import hashlib
import re
import shlex
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def boot_menu(text: str, argument: str, *, isolinux: bool = False) -> str:
    if isolinux:
        text = re.sub(r"(?m)^default\s+.*$", "default linux", text)
        text = re.sub(r"(?m)^timeout\s+.*$", "timeout 1", text)
    else:
        text = re.sub(r"(?m)^set default=.*$", 'set default="0"', text)
        text = re.sub(r"(?m)^set timeout=.*$", "set timeout=1", text)
    return re.sub(
        r"(?m)^(\s*(?:append|linux|linuxefi)\s+.*)$",
        lambda match: match[0] + f" {argument} console=ttyS0,115200n8",
        text,
    )


def build_iso(
    base: Path, kickstart: Path, output: Path, *, embedded: bool = True
) -> Path:
    """Replay the source ISO's BIOS/UEFI boot metadata after editing boot menus."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".partial.iso")
    temporary.unlink(missing_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix="iso-build-") as directory:
        work = Path(directory)
        changes: list[str] = []
        efi_options: list[str] = []
        boot_argument = (
            "inst.ks=cdrom:/ks.cfg" if embedded else "inst.ks=hd:LABEL=OEMDRV:/ks.cfg"
        )
        listing = subprocess.run(
            [
                "xorriso",
                "-indev",
                str(base),
                "-find",
                "/",
                "-name",
                "*",
                "-exec",
                "echo",
                "--",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout
        menus = [
            path
            for path in (
                "isolinux/isolinux.cfg",
                "EFI/BOOT/grub.cfg",
                "boot/grub2/grub.cfg",
            )
            if f"'/{path}'" in listing
        ]
        if not menus:
            raise ValueError("ISO has no supported GRUB or ISOLINUX boot menus")
        for relative in menus:
            extracted = work / relative.replace("/", "_")
            result = subprocess.run(
                [
                    "xorriso",
                    "-osirrox",
                    "on",
                    "-indev",
                    str(base),
                    "-extract",
                    f"/{relative}",
                    str(extracted),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Cannot extract required boot menu {relative}: {result.stderr}"
                )
            text = boot_menu(
                extracted.read_text(),
                boot_argument,
                isolinux=relative.startswith("isolinux"),
            )
            extracted.chmod(0o600)
            extracted.write_text(text)
            changes.extend(["-map", str(extracted), f"/{relative}"])
        # Physical UEFI firmware can load GRUB's menu from this FAT image,
        # independently of the identically named menu in the ISO filesystem.
        if "'/images/efiboot.img'" in listing:
            efi_image = work / "efiboot.img"
            subprocess.run(
                [
                    "xorriso",
                    "-osirrox",
                    "on",
                    "-indev",
                    str(base),
                    "-extract",
                    "/images/efiboot.img",
                    str(efi_image),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            efi_image.chmod(0o600)
            efi_menu = work / "efi-grub.cfg"
            subprocess.run(
                ["mcopy", "-i", str(efi_image), "::/EFI/BOOT/grub.cfg", str(efi_menu)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            efi_menu.chmod(0o600)
            efi_menu.write_text(boot_menu(efi_menu.read_text(), boot_argument))
            subprocess.run(
                [
                    "mcopy",
                    "-o",
                    "-i",
                    str(efi_image),
                    str(efi_menu),
                    "::/EFI/BOOT/grub.cfg",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            changes.extend(["-map", str(efi_image), "/images/efiboot.img"])
            # Replace the separate GPT EFI copy as well as the El Torito image.
            efi_options = ["-boot_image", "any", f"efi_boot_part={efi_image}"]
        if embedded:
            changes.extend(["-map", str(kickstart), "/ks.cfg"])
        # Explicit boot commands retain both BIOS and EFI entries on xorriso
        # 1.5.6, whose replay loses entries after their image is replaced.
        boot_report = subprocess.run(
            ["xorriso", "-indev", str(base), "-report_el_torito", "cmd"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout
        boot_commands = shlex.split(boot_report, comments=True)
        if efi_options:
            boot_commands = [
                efi_options[-1]
                if item.startswith("efi_boot_part=")
                else item
                for item in boot_commands
            ]
        subprocess.run(
            [
                "xorriso",
                "-indev",
                str(base),
                "-outdev",
                str(temporary),
                *changes,
                *boot_commands,
                "-commit",
                "-end",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    temporary.replace(output)
    output.chmod(0o644)
    return output


def build_oemdrv(kickstart: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".partial.img")
    with temporary.open("wb") as stream:
        stream.truncate(4 * 1024 * 1024)
    subprocess.run(
        ["mkfs.vfat", "-n", "OEMDRV", str(temporary)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        ["mcopy", "-o", "-i", str(temporary), str(kickstart), "::ks.cfg"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    temporary.replace(output)
    output.chmod(0o644)
    return output


def cached_build(base: Path, kickstart: Path, output: Path, *, embedded: bool) -> None:
    """Reuse only verified completed builds; the caller holds the build lock."""
    outputs = [output] if embedded else [output, output.with_suffix(".img")]
    for path in outputs:
        checksum = path.with_suffix(path.suffix + ".sha256")
        if path.exists() and checksum.exists():
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != checksum.read_text():
                raise ValueError(f"Cached media checksum mismatch: {path}")
            continue
        if path == output:
            build_iso(base, kickstart, path, embedded=embedded)
        else:
            build_oemdrv(kickstart, path)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        temporary = checksum.with_suffix(".partial.sha256")
        temporary.write_text(digest)
        temporary.replace(checksum)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("kickstart", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--oemdrv", action="store_true")
    arguments = parser.parse_args()
    try:
        cached_build(
            arguments.base,
            arguments.kickstart,
            arguments.output,
            embedded=not arguments.oemdrv,
        )
    except subprocess.CalledProcessError as error:
        # The remote caller needs the tool diagnostic, not just its exit code.
        diagnostic = error.stderr or error.stdout or ""
        if isinstance(diagnostic, bytes):
            diagnostic = diagnostic.decode(errors="replace")
        print(diagnostic, file=sys.stderr)
        raise
