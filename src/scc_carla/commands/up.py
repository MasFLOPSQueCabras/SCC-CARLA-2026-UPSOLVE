import mmap
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from scc_carla.bios import BiosProfile, get_profile_attributes
from scc_carla.commands.configure import configure_command
from scc_carla.config import ClusterSettings
from scc_carla.db import (
    LockError,
    NodeLifecycle,
    ensure_db,
    get_all_nodes,
    update_node_state,
)
from scc_carla.http_server import EphemeralRangeHTTPServer, is_running_on_bastion
from scc_carla.nodes import resolve_target_nodes
from scc_carla.ops import cluster_lock
from scc_carla.providers.base import NodeProvider
from scc_carla.providers.bmc import BMCProvider
from scc_carla.providers.chameleon import ChameleonProvider
from scc_carla.providers.factory import get_provider
from scc_carla.providers.libvirt import LibvirtProvider
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
        iso_cache_path = cache_dir / settings.iso_name
        dest_iso_path = Path(remote_serve_dir).expanduser() / settings.iso_name

        if not iso_cache_path.exists():
            console.print(
                f"[cyan]Downloading {settings.iso_name} (~2 GB) to local cache...[/cyan]"
            )
            subprocess.run(
                ["curl", "-L", "-o", str(iso_cache_path), settings.iso_url],
                check=True,
            )
            _patch_iso_in_place(iso_cache_path)

        if not dest_iso_path.exists():
            dest_iso_path.parent.mkdir(parents=True, exist_ok=True)
            console.print(
                f"[cyan]Copying cached ISO to HTTP serving directory {dest_iso_path}...[/cyan]"
            )
            subprocess.run(["cp", str(iso_cache_path), str(dest_iso_path)], check=True)
        console.print("[green]✓[/green] Bastion direct-boot ISO ready.")
        return

    # Off-bastion: check if ISO is already in bastion cache or serving directory
    check_script = (
        f"mkdir -p ~/.cache/scc_carla/iso {remote_serve_dir} && "
        f"if [ -f {remote_serve_dir}/{settings.iso_name} ]; then "
        f"  echo 'READY'; "
        f"elif [ -f ~/.cache/scc_carla/iso/{settings.iso_name} ]; then "
        f"  cp ~/.cache/scc_carla/iso/{settings.iso_name} {remote_serve_dir}/{settings.iso_name} && echo 'COPIED'; "
        f"else "
        f"  echo 'MISSING'; "
        f"fi"
    )
    res = subprocess.run(
        ["ssh", settings.bastion_ssh_host, check_script],
        capture_output=True,
        text=True,
        check=True,
    )
    status = res.stdout.strip()

    if status == "MISSING":
        console.print(
            f"[cyan]Downloading {settings.iso_name} (~2 GB) on bastion...[/cyan]"
        )
        download_cmd = (
            f"curl -L -o ~/.cache/scc_carla/iso/{settings.iso_name} {settings.iso_url} && "
            f"python3 -c '"
            f"import mmap; "
            f'f = open("{Path.home()}/.cache/scc_carla/iso/{settings.iso_name}", "r+b"); '
            f"mm = mmap.mmap(f.fileno(), 0); "
            f'i = mm.find(b"set default=\\"1\\""); '
            f'mm[i:i+15] = b"set default=\\"0\\""; '
            f'i = mm.find(b"set timeout=60"); '
            f'mm[i:i+14] = b"set timeout=02"; '
            f"mm.flush(); f.close()' && "
            f"cp ~/.cache/scc_carla/iso/{settings.iso_name} {remote_serve_dir}/{settings.iso_name}"
        )
        subprocess.run(
            ["ssh", settings.bastion_ssh_host, download_cmd],
            check=True,
        )
        console.print("[green]✓[/green] Bastion direct-boot ISO downloaded and ready.")
    else:
        console.print("[green]✓[/green] Bastion direct-boot ISO ready.")


def _provision_single_node(
    settings: ClusterSettings,
    node: int,
    pubkey: str,
    template_engine: TemplateEngine,
    local_staging_dir: Path,
    prov: NodeProvider,
    poll_timeout: int,
    progress: Progress,
    task_id: TaskID,
    bios_profile: BiosProfile = BiosProfile.HPC,
    privkey_path: Path | None = None,
    remote_serve_dir: str = "~/scc_serve",
    no_timeout: bool = False,
) -> bool:
    node_ip = settings.get_node_ip(node)
    hostname = settings.get_hostname(node)

    progress.update(
        task_id,
        description=f"[bold cyan]{hostname}[/bold cyan]: Staging kickstart & configuration...",
    )
    update_node_state(
        settings,
        node,
        NodeLifecycle.INSTALLING,
        pubkey=pubkey,
        bios_profile=bios_profile.value,
    )

    context = {
        "node_ip": node_ip,
        "gateway_ip": settings.gateway_ip,
        "dns_ip": settings.dns_ip,
        "hostname": hostname,
        "node_username": settings.node_username,
        "pubkey": pubkey,
    }

    ks_cfg_path = local_staging_dir / f"ks_node{node}.cfg"
    template_engine.render_to_file("kickstart/ks.cfg.j2", context, ks_cfg_path)

    match prov:
        case BMCProvider() as bmc_prov:
            oemdrv_name = f"oemdrv_node{node}.img"
            oemdrv_path = local_staging_dir / oemdrv_name
            _generate_oemdrv(ks_cfg_path, oemdrv_path)

            on_bastion = is_running_on_bastion(settings.bastion_hostname)
            if on_bastion:
                dest_path = Path(remote_serve_dir).expanduser() / oemdrv_name
                dest_path.write_bytes(oemdrv_path.read_bytes())
            else:
                subprocess.run(
                    [
                        "scp",
                        "-q",
                        str(oemdrv_path),
                        f"{settings.bastion_ssh_host}:{remote_serve_dir}/{oemdrv_name}",
                    ],
                    check=True,
                )

            progress.update(
                task_id,
                description=f"[bold cyan]{hostname}[/bold cyan]: Configuring BIOS '{bios_profile.value}' profile...",
            )
            bios_attrs = get_profile_attributes(bios_profile)
            bmc_prov.set_bios_settings(node, bios_attrs)

            iso_url = f"http://{settings.bastion_http_ip}:{settings.bastion_http_port}/{settings.iso_name}"
            oemdrv_url = f"http://{settings.bastion_http_ip}:{settings.bastion_http_port}/{oemdrv_name}"

            progress.update(
                task_id,
                description=f"[bold cyan]{hostname}[/bold cyan]: Mounting Virtual Media & triggering boot...",
            )
            if not bmc_prov.provision_node(
                node,
                ks_cfg_path=ks_cfg_path,
                pubkey=pubkey,
                bios_profile=bios_profile.value,
                iso_url=iso_url,
                oemdrv_url=oemdrv_url,
            ):
                progress.update(
                    task_id,
                    description=f"[bold red]✗ {hostname}[/bold red]: Failed to mount and boot.",
                    completed=100,
                )
                update_node_state(settings, node, NodeLifecycle.OFFLINE)
                return False

        case LibvirtProvider() as libvirt_prov:
            progress.update(
                task_id,
                description=f"[bold cyan]{hostname}[/bold cyan]: Bootstrapping local VM domain...",
            )
            cache_dir = Path.home() / ".cache" / "scc_carla" / "iso"
            iso_path = cache_dir / settings.iso_name
            if not libvirt_prov.provision_node(
                node,
                ks_cfg_path=ks_cfg_path,
                pubkey=pubkey,
                bios_profile=bios_profile.value,
                iso_path=iso_path if iso_path.exists() else None,
            ):
                progress.update(
                    task_id,
                    description=f"[bold red]✗ {hostname}[/bold red]: Failed to provision VM.",
                    completed=100,
                )
                update_node_state(settings, node, NodeLifecycle.OFFLINE)
                return False

        case ChameleonProvider() as cham_prov:
            progress.update(
                task_id,
                description=f"[bold cyan]{hostname}[/bold cyan]: Deploying Chameleon bare-metal node...",
            )
            if not cham_prov.provision_node(
                node,
                ks_cfg_path=ks_cfg_path,
                pubkey=pubkey,
                bios_profile=bios_profile.value,
            ):
                progress.update(
                    task_id,
                    description=f"[bold red]✗ {hostname}[/bold red]: Failed to deploy Chameleon node.",
                    completed=100,
                )
                update_node_state(settings, node, NodeLifecycle.OFFLINE)
                return False

    start_time = time.time()
    ssh_ready = False
    timeout_suffix = " (no timeout)" if (no_timeout or poll_timeout <= 0) else ""

    progress.update(
        task_id,
        description=f"[bold cyan]{hostname}[/bold cyan]: Installing OS & waiting for SSH{timeout_suffix}...",
    )

    while True:
        if (
            not no_timeout
            and poll_timeout > 0
            and (time.time() - start_time >= poll_timeout)
        ):
            break
        if is_ssh_authenticated(node_ip, settings.node_username, key_path=privkey_path):
            ssh_ready = True
            break
        time.sleep(5)

    if not ssh_ready:
        progress.update(
            task_id,
            description=f"[bold red]✗ {hostname}[/bold red]: Timed out waiting for SSH.",
            completed=100,
        )
        update_node_state(settings, node, NodeLifecycle.OFFLINE)
        return False

    elapsed_install = int(time.time() - start_time)
    progress.update(
        task_id,
        description=(
            f"[bold cyan]{hostname}[/bold cyan]: SSH online ({elapsed_install // 60}m {elapsed_install % 60}s). "
            "Running Ansible (node_independent)..."
        ),
    )

    match prov:
        case BMCProvider() as bmc_prov:
            bmc_prov.eject_virtual_media(node)
        case _:
            pass

    update_node_state(
        settings,
        node,
        NodeLifecycle.BOOTSTRAPPED,
        pubkey=pubkey,
        bios_profile=bios_profile.value,
    )

    # Execute Node-Independent Ansible configuration in parallel
    ansible_ok = configure_command(
        settings,
        limit=hostname,
        tags="node_independent",
        exit_on_error=False,
    )

    if ansible_ok:
        update_node_state(
            settings,
            node,
            NodeLifecycle.READY,
            pubkey=pubkey,
            bios_profile=bios_profile.value,
        )
        progress.update(
            task_id,
            description=f"[bold green]✓ {hostname}[/bold green]: Provisioned & configured ({node_ip})",
            completed=100,
        )
        return True
    else:
        progress.update(
            task_id,
            description=f"[bold yellow]⚠ {hostname}[/bold yellow]: OS installed but Ansible had warnings ({node_ip})",
            completed=100,
        )
        return True


def up_command(
    settings: ClusterSettings,
    node: int | list[int] | None = None,
    pubkey_path: Path | None = None,
    poll_timeout: int = 1800,
    bios_profile: BiosProfile = BiosProfile.HPC,
    no_timeout: bool = False,
    force_lock: bool = False,
    provider: str | None = None,
) -> None:
    ensure_db(settings)

    try:
        target_nodes = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
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

    try:
        with (
            cluster_lock(
                settings,
                targets=target_nodes,
                operation="up",
                force=force_lock,
            ),
            get_provider(settings, provider) as prov,
        ):
            active_prov_name = provider or settings.provider
            console.print(
                f"[cyan]Active Provider: [bold]{active_prov_name}[/bold][/cyan]"
            )

            # BMC provider requires ephemeral HTTP server for virtual media
            match prov:
                case BMCProvider():
                    http_ctx = EphemeralRangeHTTPServer(
                        port=settings.bastion_http_port,
                        bind_ip=settings.bastion_http_ip,
                        bastion_ssh_host=settings.bastion_ssh_host,
                        bastion_hostname=settings.bastion_hostname,
                        remote_serve_dir="~/scc_serve",
                    )
                    _ensure_bastion_iso(settings, remote_serve_dir="~/scc_serve")
                case _:
                    http_ctx = nullcontext()

            with http_ctx:
                console.print(
                    f"[cyan]Initiating parallel provisioning across {len(target_nodes)} target node(s)...[/cyan]"
                )

                progress = Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    TimeElapsedColumn(),
                    console=console,
                )

                results: dict[int, bool] = {}
                with progress:
                    node_tasks = {
                        n: progress.add_task(
                            f"[bold cyan]{settings.get_hostname(n)}[/bold cyan]: Initializing...",
                            total=None,
                        )
                        for n in target_nodes
                    }

                    with ThreadPoolExecutor(max_workers=len(target_nodes)) as executor:
                        futures = {
                            executor.submit(
                                _provision_single_node,
                                settings=settings,
                                node=n,
                                pubkey=pubkey,
                                template_engine=template_engine,
                                local_staging_dir=local_staging_dir,
                                prov=prov,
                                poll_timeout=poll_timeout,
                                progress=progress,
                                task_id=node_tasks[n],
                                bios_profile=bios_profile,
                                privkey_path=privkey_file,
                                remote_serve_dir="~/scc_serve",
                                no_timeout=no_timeout,
                            ): n
                            for n in target_nodes
                        }

                        for future in as_completed(futures):
                            n = futures[future]
                            try:
                                results[n] = future.result()
                            except Exception as exc:  # noqa: BLE001
                                results[n] = False
                                progress.update(
                                    node_tasks[n],
                                    description=f"[bold red]✗ {settings.get_hostname(n)}[/bold red]: Exception: {exc}",
                                    completed=100,
                                )

                # Check if all 3 cluster nodes are ready to run cluster coordination
                try:
                    all_cluster_nodes = get_all_nodes(settings)
                    all_3_online = len(all_cluster_nodes) == 3 and all(
                        cn.state in (NodeLifecycle.READY, NodeLifecycle.BOOTSTRAPPED)
                        for cn in all_cluster_nodes
                    )
                    if all_3_online:
                        console.print(
                            "\n[cyan]All 3 cluster nodes online! Running cluster-wide coordination (SSH trust, MPI hostfile, InfiniBand)...[/cyan]"
                        )
                        coord_ok = configure_command(
                            settings,
                            tags="cluster_coordination",
                            exit_on_error=False,
                        )
                        if coord_ok:
                            console.print(
                                "[bold green]✓ Full 3-node cluster coordinated and verified for MPI/HPL.[/bold green]"
                            )
                        else:
                            console.print(
                                "[bold yellow]⚠ Cluster coordination completed with non-fatal warnings.[/bold yellow]"
                            )
                    else:
                        ready_count = sum(
                            1
                            for cn in all_cluster_nodes
                            if cn.state
                            in (NodeLifecycle.READY, NodeLifecycle.BOOTSTRAPPED)
                        )
                        console.print(
                            f"\n[dim]Active nodes: {ready_count}/3. Cluster-wide coordination will run when all 3 nodes are online.[/dim]"
                        )
                except Exception as e:  # noqa: BLE001
                    console.print(
                        f"[dim]Could not evaluate cluster coordination: {e}[/dim]"
                    )

    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force-lock to override or 'scc-carla lock list' to view active locks.[/dim]"
        )
        return
