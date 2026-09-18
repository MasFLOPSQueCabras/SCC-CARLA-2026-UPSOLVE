import mmap
import subprocess
import threading
import time
from pathlib import Path

from rich.console import Console

from scc_carla.bios import BiosProfile, get_profile_attributes
from scc_carla.bmc import BMCController
from scc_carla.config import ClusterSettings
from scc_carla.db import (
    ClusterLock,
    LockError,
    NodeLifecycle,
    ensure_db,
    update_node_state,
)
from scc_carla.http_server import EphemeralRangeHTTPServer, is_running_on_bastion
from scc_carla.nodes import resolve_target_nodes
from scc_carla.ssh import is_ssh_authenticated
from scc_carla.templating import TemplateEngine

console = Console()


def _generate_oemdrv(
    ks_cfg_path: Path,
    output_path: Path,
) -> None:
    cmd = (
        f"dd if=/dev/zero of={output_path} bs=1M count=4 2>/dev/null && "
        f"mkfs.vfat -n OEMDRV {output_path} 2>/dev/null && "
        f"mcopy -i {output_path} {ks_cfg_path} ::ks.cfg 2>/dev/null"
    )
    subprocess.run(cmd, shell=True, check=True)


def _patch_iso_in_place(iso_path: Path) -> None:
    with open(iso_path, "r+b") as f, mmap.mmap(f.fileno(), 0) as mm:
        start = 0
        while True:
            idx = mm.find(b'set default="1"', start)
            if idx == -1:
                break
            mm[idx : idx + 15] = b'set default="0"'
            start = idx + 15

        start = 0
        while True:
            idx = mm.find(b"set timeout=60", start)
            if idx == -1:
                break
            mm[idx : idx + 14] = b"set timeout=02"
            start = idx + 14
        mm.flush()


def _ensure_bastion_iso(
    settings: ClusterSettings,
    remote_serve_dir: str = "~/scc_serve",
) -> None:
    on_bastion = is_running_on_bastion(settings.bastion_hostname)
    console.print(
        "[cyan]Ensuring Rocky Linux minimal ISO is cached and direct-boot patched on bastion...[/cyan]"
    )

    if on_bastion:
        cache_dir = Path.home() / ".cache" / "scc_carla" / "iso"
        cache_dir.mkdir(parents=True, exist_ok=True)
        iso_path = cache_dir / settings.iso_name

        if not iso_path.exists():
            console.print(
                f"[cyan]Downloading {settings.iso_name} directly on bastion...[/cyan]"
            )
            subprocess.run(
                ["wget", "-c", settings.iso_url, "-O", str(iso_path)],
                check=True,
            )

        _patch_iso_in_place(iso_path)
        serve_path = Path(remote_serve_dir).expanduser()
        serve_path.mkdir(parents=True, exist_ok=True)
        symlink_path = serve_path / settings.iso_name
        if not symlink_path.exists():
            symlink_path.symlink_to(iso_path)
        console.print("[green]✓[/green] Bastion direct-boot ISO ready.")
    else:
        # Check if already cached on bastion
        check_cmd = [
            "ssh",
            settings.bastion_ssh_host,
            f"test -f ~/.cache/scc_carla/iso/{settings.iso_name} && echo EXISTS || echo MISSING",
        ]
        res = subprocess.run(
            check_cmd, capture_output=True, text=True, check=True
        )
        if "MISSING" in res.stdout:
            console.print(
                f"[cyan]Downloading {settings.iso_name} directly on bastion (wire speed)...[/cyan]"
            )
            subprocess.run(
                [
                    "ssh",
                    settings.bastion_ssh_host,
                    f"mkdir -p ~/.cache/scc_carla/iso && wget -c '{settings.iso_url}' -O ~/.cache/scc_carla/iso/{settings.iso_name}",
                ],
                check=True,
            )

        patch_and_link_script = (
            f"import os, mmap\n"
            f"iso_path = os.path.expanduser('~/.cache/scc_carla/iso/{settings.iso_name}')\n"
            f"f = open(iso_path, 'r+b')\n"
            f"mm = mmap.mmap(f.fileno(), 0)\n"
            f"s = 0\n"
            f"while True:\n"
            f"    i = mm.find(b'set default=\"1\"', s)\n"
            f"    if i == -1: break\n"
            f"    mm[i:i+15] = b'set default=\"0\"'\n"
            f"    s = i + 15\n"
            f"s = 0\n"
            f"while True:\n"
            f"    i = mm.find(b'set timeout=60', s)\n"
            f"    if i == -1: break\n"
            f"    mm[i:i+14] = b'set timeout=02'\n"
            f"    s = i + 14\n"
            f"mm.flush()\n"
            f"f.close()\n"
            f"serve_dir = os.path.expanduser('{remote_serve_dir}')\n"
            f"os.makedirs(serve_dir, exist_ok=True)\n"
            f"link = os.path.join(serve_dir, '{settings.iso_name}')\n"
            f"if not os.path.exists(link):\n"
            f"    os.symlink(iso_path, link)\n"
        )
        subprocess.run(
            [
                "ssh",
                settings.bastion_ssh_host,
                f'python3 -c "{patch_and_link_script}"',
            ],
            check=True,
        )
        console.print("[green]✓[/green] Bastion direct-boot ISO ready.")


def _provision_single_node(
    settings: ClusterSettings,
    node: int,
    pubkey: str,
    template_engine: TemplateEngine,
    local_staging_dir: Path,
    bmc: BMCController,
    poll_timeout: int,
    bios_profile: BiosProfile = BiosProfile.HPC,
    privkey_path: Path | None = None,
    remote_serve_dir: str = "~/scc_serve",
    no_timeout: bool = False,
) -> bool:
    node_ip = settings.get_node_ip(node)
    hostname = settings.get_hostname(node)

    console.print(
        f"[cyan]Provisioning {hostname} ({node_ip}) with BIOS profile '{bios_profile.value}'...[/cyan]"
    )
    update_node_state(
        settings,
        node,
        NodeLifecycle.INSTALLING,
        pubkey=pubkey,
        bios_profile=bios_profile.value,
    )

    context = {
        "node_id": node,
        "team_id": settings.team_id,
        "node_ip": node_ip,
        "ib_ip": f"10.10.{settings.team_id}.{node}",
        "gateway_ip": settings.gateway_ip,
        "dns_ip": settings.dns_ip,
        "hostname": hostname,
        "node_username": settings.node_username,
        "pubkey": pubkey,
    }

    ks_cfg_path = local_staging_dir / f"ks_node{node}.cfg"
    oemdrv_name = f"oemdrv_node{node}.img"
    oemdrv_path = local_staging_dir / oemdrv_name

    template_engine.render_to_file("kickstart/ks.cfg.j2", context, ks_cfg_path)
    _generate_oemdrv(ks_cfg_path, oemdrv_path)

    # Stage OEMDRV image to bastion HTTP serving directory
    on_bastion = is_running_on_bastion(settings.bastion_hostname)
    if on_bastion:
        dest_path = Path(remote_serve_dir).expanduser() / oemdrv_name
        dest_path.write_bytes(oemdrv_path.read_bytes())
    else:
        console.print(f"[cyan]Uploading {oemdrv_name} (~4 MB) to bastion...[/cyan]")
        subprocess.run(
            [
                "scp",
                "-q",
                str(oemdrv_path),
                f"{settings.bastion_ssh_host}:{remote_serve_dir}/{oemdrv_name}",
            ],
            check=True,
        )

    # 1. Stage BIOS Profile Attributes via Redfish
    bios_attrs = get_profile_attributes(bios_profile)
    console.print(
        f"[cyan]Configuring BIOS '{bios_profile.value}' profile on {hostname}...[/cyan]"
    )
    if bmc.set_bios_settings(node, bios_attrs):
        console.print(
            f"[green]✓[/green] Staged BIOS '{bios_profile.value}' settings on {hostname}."
        )
    else:
        console.print(
            f"[bold yellow]⚠ Warning: Could not stage BIOS settings on {hostname}, proceeding with boot...[/bold yellow]"
        )

    # 2. Virtual Media Mount & Reboot
    iso_url = f"http://{settings.bastion_http_ip}:{settings.bastion_http_port}/{settings.iso_name}"
    oemdrv_url = (
        f"http://{settings.bastion_http_ip}:{settings.bastion_http_port}/{oemdrv_name}"
    )

    console.print(f"[cyan]Mounting Virtual Media on {hostname} via iLO...[/cyan]")
    if not bmc.mount_and_boot(node, iso_url=iso_url, floppy_url=oemdrv_url):
        console.print(f"[bold red]Failed to mount and boot {hostname}.[/bold red]")
        update_node_state(settings, node, NodeLifecycle.OFFLINE)
        return False

    console.print(
        f"[green]✓[/green] Virtual Media mounted and {hostname} reboot triggered."
    )

    start_time = time.time()
    ssh_ready = False
    stop_timer = threading.Event()
    timeout_suffix = " (no timeout)" if (no_timeout or poll_timeout <= 0) else ""

    with console.status(
        f"[bold cyan][00:00] Waiting for {hostname} installation and authenticated SSH{timeout_suffix}...[/bold cyan]",
        spinner="dots",
    ) as status:

        def update_timer() -> None:
            while not stop_timer.wait(1.0):
                elapsed_sec = int(time.time() - start_time)
                mins = elapsed_sec // 60
                secs = elapsed_sec % 60
                status.update(
                    f"[bold cyan][{mins:02d}:{secs:02d}] "
                    f"Waiting for {hostname} installation and authenticated SSH{timeout_suffix}...[/bold cyan]"
                )

        timer_thread = threading.Thread(target=update_timer, daemon=True)
        timer_thread.start()

        try:
            while True:
                if not no_timeout and poll_timeout > 0 and (time.time() - start_time >= poll_timeout):
                    break
                if is_ssh_authenticated(
                    node_ip, settings.node_username, key_path=privkey_path
                ):
                    ssh_ready = True
                    break
                time.sleep(5)
        finally:
            stop_timer.set()
            timer_thread.join(timeout=1.0)

    if not ssh_ready:
        console.print(
            f"[bold red]Timed out waiting for {hostname} installation and authenticated SSH.[/bold red]"
        )
        update_node_state(settings, node, NodeLifecycle.OFFLINE)
        return False

    elapsed_total = int(time.time() - start_time)
    console.print(
        f"[green]✓[/green] {hostname} SSH online in {elapsed_total // 60}m {elapsed_total % 60}s."
    )

    bmc.eject_virtual_media(node)
    console.print(f"[green]✓[/green] Ejected Virtual Media on {hostname}.")
    update_node_state(
        settings,
        node,
        NodeLifecycle.BOOTSTRAPPED,
        pubkey=pubkey,
        bios_profile=bios_profile.value,
    )
    console.print(
        f"[bold green]{hostname} successfully provisioned and online at {node_ip}![/bold green]"
    )
    return True


def up_command(
    settings: ClusterSettings,
    node: int | None = None,
    all_nodes: bool = False,
    pubkey_path: Path | None = None,
    poll_timeout: int = 1800,
    bios_profile: BiosProfile = BiosProfile.HPC,
    no_timeout: bool = False,
    force: bool = False,
) -> None:
    ensure_db(settings)

    try:
        target_nodes = resolve_target_nodes(node, all_nodes)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    if not target_nodes:
        console.print("[cyan]Initializing cluster state...[/cyan]")
        console.print("[green]✓[/green] State database ready")
        console.print("[bold green]Cluster foundation is up.[/bold green]")
        return

    key_file = (
        pubkey_path
        if pubkey_path is not None
        else (Path.home() / ".ssh" / "carla_scc_ed25519.pub")
    )
    if not key_file.exists():
        console.print(
            f"[bold red]SSH public key file not found: {key_file}[/bold red]\n"
            "Please specify a valid key with --pubkey <path>"
        )
        return

    pubkey = key_file.read_text(encoding="utf-8").strip()
    if key_file.name.endswith(".pub"):
        privkey_file = key_file.with_name(key_file.name[:-4])
    else:
        privkey_file = Path.home() / ".ssh" / "carla_scc_ed25519"

    local_staging_dir = Path.home() / ".cache" / "scc_carla" / "staging"
    local_staging_dir.mkdir(parents=True, exist_ok=True)
    template_engine = TemplateEngine()

    resources = [f"node-{n}" for n in target_nodes]
    try:
        with (
            ClusterLock(
                settings, resources=resources, operation="up", force=force
            ),
            BMCController(settings) as bmc,
            EphemeralRangeHTTPServer(
                port=settings.bastion_http_port,
                bind_ip=settings.bastion_http_ip,
                bastion_ssh_host=settings.bastion_ssh_host,
                bastion_hostname=settings.bastion_hostname,
                remote_serve_dir="~/scc_serve",
            ),
        ):
            _ensure_bastion_iso(settings, remote_serve_dir="~/scc_serve")

            for n in target_nodes:
                success = _provision_single_node(
                    settings=settings,
                    node=n,
                    pubkey=pubkey,
                    template_engine=template_engine,
                    local_staging_dir=local_staging_dir,
                    bmc=bmc,
                    poll_timeout=poll_timeout,
                    bios_profile=bios_profile,
                    privkey_path=privkey_file,
                    remote_serve_dir="~/scc_serve",
                    no_timeout=no_timeout,
                )
                if not success and len(target_nodes) > 1:
                    console.print(
                        f"[bold red]Stopping batch provisioning due to failure on Node {n}.[/bold red]"
                    )
                    break
    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force to override or 'scc-carla lock list' to view active locks.[/dim]"
        )
        return
