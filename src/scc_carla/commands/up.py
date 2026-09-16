import shutil
import subprocess
import threading
import time
from pathlib import Path

from rich.console import Console

from scc_carla.bmc import BMCController
from scc_carla.config import ClusterSettings
from scc_carla.db import NodeLifecycle, ensure_db, update_node_state
from scc_carla.http_server import EphemeralRangeHTTPServer, is_running_on_bastion
from scc_carla.nodes import resolve_target_nodes
from scc_carla.ssh import SSHSession
from scc_carla.templating import TemplateEngine

console = Console()


def _generate_cidata(
    user_data_path: Path,
    meta_data_path: Path,
    output_path: Path,
) -> None:
    if shutil.which("cloud-localds"):
        cmd = [
            "cloud-localds",
            "-f",
            "vfat",
            str(output_path),
            str(user_data_path),
            str(meta_data_path),
        ]
        subprocess.run(cmd, check=True)
    else:
        cmd = (
            f"dd if=/dev/zero of={output_path} bs=1M count=2 2>/dev/null && "
            f"mkfs.vfat -n cidata {output_path} 2>/dev/null && "
            f"mcopy -i {output_path} {user_data_path} {meta_data_path} :: 2>/dev/null"
        )
        subprocess.run(cmd, shell=True, check=False)


def _prepare_bastion_staging(
    settings: ClusterSettings,
    ssh: SSHSession,
    user_data_path: Path,
    meta_data_path: Path,
    cidata_path: Path,
) -> None:
    if is_running_on_bastion(settings.bastion_hostname):
        staging = Path.home() / "scc_serve"
        staging.mkdir(parents=True, exist_ok=True)
        iso_src = Path.home() / settings.iso_name
        iso_dst = staging / settings.iso_name
        if iso_src.exists() and not iso_dst.exists():
            iso_dst.symlink_to(iso_src)
    else:
        ssh.run("mkdir -p ~/scc_serve", check=True)
        ssh.scp_to(
            [user_data_path, meta_data_path, cidata_path],
            "~/scc_serve/",
        )
        remote_cmd = f"ln -sf ~/{settings.iso_name} ~/scc_serve/{settings.iso_name}"
        ssh.run(remote_cmd, check=True)


def _provision_single_node(
    settings: ClusterSettings,
    ssh: SSHSession,
    node: int,
    pubkey: str,
    template_engine: TemplateEngine,
    staging_dir: Path,
    bmc: BMCController,
    poll_timeout: int,
) -> bool:
    node_ip = settings.get_node_ip(node)
    hostname = settings.get_hostname(node)

    console.print(f"[cyan]Provisioning {hostname} ({node_ip})...[/cyan]")
    update_node_state(settings.db_path, node, NodeLifecycle.INSTALLING, pubkey=pubkey)

    context = {
        "node_ip": node_ip,
        "gateway_ip": settings.gateway_ip,
        "dns_ip": settings.dns_ip,
        "hostname": hostname,
        "node_username": settings.node_username,
        "pubkey": pubkey,
    }

    user_data_path = staging_dir / "user-data"
    meta_data_path = staging_dir / "meta-data"
    cidata_path = staging_dir / "cidata.img"
    template_engine.render_to_file("cloud-init/user-data.j2", context, user_data_path)
    template_engine.render_to_file("cloud-init/meta-data.j2", context, meta_data_path)
    _generate_cidata(user_data_path, meta_data_path, cidata_path)
    _prepare_bastion_staging(settings, ssh, user_data_path, meta_data_path, cidata_path)

    iso_url = f"http://{settings.bastion_http_ip}:{settings.bastion_http_port}/{settings.iso_name}"
    cidata_url = (
        f"http://{settings.bastion_http_ip}:{settings.bastion_http_port}/cidata.img"
    )

    console.print(f"[cyan]Mounting Virtual Media on {hostname} via iLO...[/cyan]")
    if not bmc.mount_and_boot(node, iso_url=iso_url, cidata_url=cidata_url):
        console.print(f"[bold red]Failed to mount and boot {hostname}.[/bold red]")
        update_node_state(settings.db_path, node, NodeLifecycle.OFFLINE)
        return False

    console.print(
        f"[green]✓[/green] Virtual Media mounted and {hostname} reboot triggered."
    )

    start_time = time.time()
    ssh_ready = False
    stop_timer = threading.Event()

    with console.status(
        f"[bold cyan][00:00] Waiting for {hostname} installation and SSH (port 22)...[/bold cyan]",
        spinner="dots",
    ) as status:

        def update_timer() -> None:
            while not stop_timer.wait(1.0):
                elapsed_sec = int(time.time() - start_time)
                mins = elapsed_sec // 60
                secs = elapsed_sec % 60
                status.update(
                    f"[bold cyan][{mins:02d}:{secs:02d}] "
                    f"Waiting for {hostname} installation and SSH (port 22)...[/bold cyan]"
                )

        timer_thread = threading.Thread(target=update_timer, daemon=True)
        timer_thread.start()

        try:
            while time.time() - start_time < poll_timeout:
                if ssh.is_port_open(node_ip, 22):
                    ssh_ready = True
                    break
                time.sleep(2)
        finally:
            stop_timer.set()
            timer_thread.join(timeout=1.0)

    if not ssh_ready:
        console.print(
            f"[bold red]Timed out waiting for {hostname} SSH to become available.[/bold red]"
        )
        update_node_state(settings.db_path, node, NodeLifecycle.OFFLINE)
        return False

    elapsed_total = int(time.time() - start_time)
    console.print(
        f"[green]✓[/green] {hostname} SSH online in {elapsed_total // 60}m {elapsed_total % 60}s."
    )

    bmc.eject_virtual_media(node)
    console.print(f"[green]✓[/green] Ejected Virtual Media on {hostname}.")
    update_node_state(settings.db_path, node, NodeLifecycle.BOOTSTRAPPED, pubkey=pubkey)
    console.print(
        f"[bold green]{hostname} successfully provisioned and online at {node_ip}![/bold green]"
    )
    return True


def up_command(
    settings: ClusterSettings,
    node: int | None = None,
    all_nodes: bool = False,
    pubkey_path: Path | None = None,
    poll_timeout: int = 600,
) -> None:
    ensure_db(settings.db_path, settings.team_id)

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
    staging_dir = Path.home() / "scc_serve"
    staging_dir.mkdir(parents=True, exist_ok=True)
    template_engine = TemplateEngine()

    with (
        SSHSession(settings.bastion_ssh_host, settings.bastion_hostname) as ssh,
        BMCController(settings) as bmc,
        EphemeralRangeHTTPServer(
            port=settings.bastion_http_port,
            bind_ip=settings.bastion_http_ip,
            bastion_ssh_host=settings.bastion_ssh_host,
            bastion_hostname=settings.bastion_hostname,
            serve_dir=staging_dir,
        ),
    ):
        for n in target_nodes:
            success = _provision_single_node(
                settings=settings,
                ssh=ssh,
                node=n,
                pubkey=pubkey,
                template_engine=template_engine,
                staging_dir=staging_dir,
                bmc=bmc,
                poll_timeout=poll_timeout,
            )
            if not success and len(target_nodes) > 1:
                console.print(
                    f"[bold red]Stopping batch provisioning due to failure on Node {n}.[/bold red]"
                )
                break
