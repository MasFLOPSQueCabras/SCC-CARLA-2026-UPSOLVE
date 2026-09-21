"""Golden Image builder, inspection, and streaming export commands."""

import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cabrita.config import get_settings
from cabrita.core.image import compress_zstd, convert_qcow2_to_raw, inspect_image
from cabrita.paths import (
    get_golden_image_dir,
    get_image_cache_dir,
    get_iso_cache_dir,
)

console = Console()

image_app = typer.Typer(
    name="image",
    help="Build, inspect, and export golden cluster base images",
    no_args_is_help=True,
)


def _format_bytes(num_bytes: float) -> str:
    val = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(val) < 1024.0:
            return f"{val:3.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} PB"


@image_app.command("list")
def list_images() -> None:
    """List cached base images, bootable ISOs, and golden images."""
    table = Table(title="Available OS Images & Caches", header_style="bold cyan")
    table.add_column("Category", style="dim", min_width=14)
    table.add_column("File Name", style="bold", min_width=24)
    table.add_column("Format", min_width=10)
    table.add_column("Disk Size", min_width=12)
    table.add_column("Virtual Size", min_width=12)

    dirs_to_scan = [
        ("Golden Base", get_golden_image_dir()),
        ("Cloud Base", get_image_cache_dir()),
        ("Boot ISO", get_iso_cache_dir()),
    ]

    total_files = 0
    for category, d in dirs_to_scan:
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                try:
                    meta = inspect_image(p)
                    table.add_row(
                        category,
                        p.name,
                        meta.format,
                        _format_bytes(meta.actual_size_bytes),
                        _format_bytes(meta.virtual_size_bytes),
                    )
                    total_files += 1
                except Exception:  # noqa: BLE001
                    table.add_row(
                        category,
                        p.name,
                        "unknown",
                        _format_bytes(p.stat().st_size),
                        "-",
                    )
                    total_files += 1

    if total_files == 0:
        console.print("[yellow]No cached images or ISOs found.[/yellow]")
    else:
        console.print(table)


@image_app.command("build")
def build_cmd(
    source: Annotated[
        Path | None,
        typer.Option(
            "--source",
            "-s",
            help="Source disk image (.qcow2, .raw) or installed VM disk to convert to golden base",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Target golden base image path (defaults to ~/.cache/cabrita/golden/golden-rocky-base.qcow2)",
        ),
    ] = None,
    from_installed: Annotated[
        Path | None,
        typer.Option(
            "--from-installed",
            help="Capture an existing installed VM disk into the golden base",
        ),
    ] = None,
    compress: Annotated[
        bool,
        typer.Option(
            "--compress/--no-compress",
            "-c",
            help="Enable QCOW2 internal compression",
        ),
    ] = True,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Overwrite existing golden image if present",
        ),
    ] = False,
) -> None:
    """Build or capture a pristine golden QCOW2 base image for zero-install cluster deployments."""
    golden_dir = get_golden_image_dir()
    golden_dir.mkdir(parents=True, exist_ok=True)
    target = (
        output.expanduser().resolve()
        if output
        else (golden_dir / "golden-rocky-base.qcow2")
    )

    if target.exists() and not force:
        console.print(
            f"[yellow]Golden image already exists at [bold]{target}[/bold].[/yellow]\n"
            "Use [bold]--force[/bold] / [bold]-f[/bold] to rebuild/overwrite."
        )
        meta = inspect_image(target)
        console.print(
            f"[dim]Format: {meta.format} | Virtual Size: {_format_bytes(meta.virtual_size_bytes)} | Allocation: {_format_bytes(meta.actual_size_bytes)}[/dim]"
        )
        return

    # Determine source image
    src_cand: Path | None = None
    if from_installed:
        src_cand = from_installed.expanduser().resolve()
    elif source:
        src_cand = source.expanduser().resolve()
    else:
        # Check cached cloud base image first
        settings = get_settings()
        cloud_cand = get_image_cache_dir() / settings.cloud_image_name
        if cloud_cand.exists():
            src_cand = cloud_cand
        else:
            img_dir = get_image_cache_dir()
            if img_dir.exists():
                qcow2_files = [
                    p
                    for p in sorted(img_dir.iterdir())
                    if p.suffix == ".qcow2" and not p.name.startswith(".")
                ]
                if qcow2_files:
                    src_cand = qcow2_files[0]

        if src_cand is None:
            iso_dir = get_iso_cache_dir()
            if iso_dir.exists():
                iso_files = [
                    p
                    for p in sorted(iso_dir.iterdir())
                    if p.suffix == ".iso" and not p.name.startswith(".")
                ]
                if iso_files:
                    src_cand = iso_files[0]

    if src_cand is None or not src_cand.exists():
        console.print(
            "[bold red]No valid source image found to build golden base.[/bold red]\n"
            "Please provide a source with [bold]--source <path>[/bold] or [bold]--from-installed <path>[/bold]."
        )
        raise typer.Exit(code=1)

    console.print(
        f"[cyan]Building golden base image from [bold]{src_cand.name}[/bold] -> [bold]{target.name}[/bold]...[/cyan]"
    )

    cmd = ["qemu-img", "convert", "-O", "qcow2"]
    if compress:
        cmd.append("-c")
    cmd.extend([str(src_cand), str(target)])

    try:
        subprocess.run(cmd, check=True)
        try:
            target.chmod(0o644)
        except OSError:
            pass
        console.print(
            f"[bold green]✓ Successfully built golden base image: {target}[/bold green]"
        )
        meta = inspect_image(target)
        console.print(
            f"[dim]Format: {meta.format} | Virtual Size: {_format_bytes(meta.virtual_size_bytes)} | Allocation: {_format_bytes(meta.actual_size_bytes)}[/dim]"
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]Image build failed: {e}[/bold red]")
        raise typer.Exit(code=1) from e


@image_app.command("inspect")
def inspect_cmd(
    image_path: Annotated[
        Path,
        typer.Argument(help="Path to disk image (.qcow2, .raw, .img, .iso)"),
    ],
) -> None:
    """Inspect disk image details, virtual size, allocation, and format."""
    p = image_path.expanduser().resolve()
    if not p.exists():
        console.print(f"[bold red]File not found: {p}[/bold red]")
        raise typer.Exit(code=1)

    meta = inspect_image(p)
    table = Table(title=f"Image Metadata: {p.name}", header_style="bold magenta")
    table.add_column("Property", style="bold")
    table.add_column("Value")

    table.add_row("Path", str(meta.path))
    table.add_row("Format", meta.format)
    table.add_row(
        "Virtual Size",
        f"{_format_bytes(meta.virtual_size_bytes)} ({meta.virtual_size_bytes} bytes)",
    )
    table.add_row(
        "Disk Allocation",
        f"{_format_bytes(meta.actual_size_bytes)} ({meta.actual_size_bytes} bytes)",
    )
    table.add_row("Compressed", str(meta.compressed))

    console.print(table)


@image_app.command("export")
def export_cmd(
    source_image: Annotated[
        Path,
        typer.Option(
            "--source",
            "-s",
            help="Source QCOW2 image to convert and stream",
        ),
    ],
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            "-o",
            help="Target output directory for exported raw/zst image",
        ),
    ] = None,
    compress: Annotated[
        bool,
        typer.Option(
            "--compress/--no-compress",
            help="Compress raw image using zstd for fast network streaming",
        ),
    ] = True,
) -> None:
    """Convert a QCOW2 golden image into raw format and compress with zstd for Bastion streaming."""
    src = source_image.expanduser().resolve()
    if not src.exists():
        console.print(f"[bold red]Source image not found: {src}[/bold red]")
        raise typer.Exit(code=1)

    out_dir = (
        output_dir.expanduser().resolve() if output_dir else get_golden_image_dir()
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_name = src.stem + ".raw"
    raw_path = out_dir / raw_name

    console.print(f"[cyan]Converting {src.name} to raw format: {raw_path}...[/cyan]")
    try:
        convert_qcow2_to_raw(src, raw_path)
        console.print(
            f"[green]✓[/green] Converted to raw: {raw_path} ({_format_bytes(raw_path.stat().st_size)})"
        )
    except Exception as e:
        console.print(f"[bold red]Conversion failed: {e}[/bold red]")
        raise typer.Exit(code=1) from e

    if compress:
        zst_path = out_dir / (raw_name + ".zst")
        console.print(f"[cyan]Compressing with zstandard: {zst_path}...[/cyan]")
        try:
            compress_zstd(raw_path, zst_path)
            console.print(
                f"[bold green]✓ Export complete: {zst_path} ({_format_bytes(zst_path.stat().st_size)})[/bold green]\n"
                f"Ready for high-speed Bastion HTTP block streaming to bare-metal nodes."
            )
        except Exception as e:
            console.print(f"[bold red]Compression failed: {e}[/bold red]")
            raise typer.Exit(code=1) from e
