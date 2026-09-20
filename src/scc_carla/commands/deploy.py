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
    update_node_state,
)
from scc_carla.http_server import EphemeralRangeHTTPServer, is_running_on_bastion
from scc_carla.image import ensure_cached_cloud_image, is_qcow2_image
from scc_carla.iso import ensure_cached_iso
from scc_carla.nodes import resolve_target_nodes
from scc_carla.oemdrv import generate_oemdrv
from scc_carla.ops import cluster_lock
from scc_carla.paths import (
    get_golden_image_dir,
    get_image_cache_dir,
    get_iso_cache_dir,
)
from scc_carla.providers.base import NodeProvider
from scc_carla.providers.bmc import BMCProvider
from scc_carla.providers.chameleon import ChameleonProvider
from scc_carla.providers.factory import get_provider
from scc_carla.providers.libvirt import LibvirtProvider
from scc_carla.ssh import is_ssh_authenticated
from scc_carla.templating import TemplateEngine

console = Console()


def _ensure_bastion_iso(
    settings: ClusterSettings,
    template_engine: TemplateEngine,
    remote_serve_dir: Path | str | None = None,
    iso_source: str | None = None,
) -> None:
    on_bastion = is_running_on_bastion(settings.bastion_hostname)
    console.print(
        "[cyan]Ensuring Rocky Linux minimal ISO is cached and direct-boot patched on bastion...[/cyan]"
    )

    serve_path = (
        Path(remote_serve_dir)
        if remote_serve_dir is not None
        else (Path.home() / "scc_serve")
    )

    if on_bastion:
        dest_iso_path = serve_path / settings.iso_name
        cached_iso = ensure_cached_iso(settings, iso_source=iso_source)

        if not dest_iso_path.exists():
            dest_iso_path.parent.mkdir(parents=True, exist_ok=True)
            console.print(
                f"[cyan]Copying cached ISO to HTTP serving directory {dest_iso_path}...[/cyan]"
            )
            subprocess.run(["cp", str(cached_iso), str(dest_iso_path)], check=True)
        console.print("[green]✓[/green] Bastion direct-boot ISO ready.")
        return

    # Off-bastion: check if ISO is already in bastion cache or serving directory
    check_script = template_engine.render(
        "scripts/bastion_check_iso.sh.j2",
        {"serve_dir": str(serve_path), "iso_name": settings.iso_name},
    )
    res = subprocess.run(
        ["ssh", settings.bastion_ssh_host, "bash -s"],
        input=check_script,
        capture_output=True,
        text=True,
        check=True,
    )
    status = res.stdout.strip()

    if status == "MISSING":
        source = iso_source or settings.iso_source
        if source.startswith(("http://", "https://", "ftp://")):
            console.print(
                f"[cyan]Downloading {settings.iso_name} on bastion from {source}...[/cyan]"
            )
            download_script = template_engine.render(
                "scripts/bastion_download_iso.sh.j2",
                {
                    "serve_dir": str(serve_path),
                    "iso_name": settings.iso_name,
                    "source_url": source,
                },
            )
            subprocess.run(
                ["ssh", settings.bastion_ssh_host, "bash -s"],
                input=download_script,
                text=True,
                check=True,
            )
        else:
            cached_iso = ensure_cached_iso(settings, iso_source=source)
            console.print(
                f"[cyan]Uploading local cached ISO ({cached_iso}) to bastion...[/cyan]"
            )
            subprocess.run(
                [
                    "scp",
                    str(cached_iso),
                    f"{settings.bastion_ssh_host}:{remote_serve_dir}/{settings.iso_name}",
                ],
                check=True,
            )
        console.print("[green]✓[/green] Bastion direct-boot ISO ready.")
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
    remote_serve_dir: Path | str | None = None,
    no_timeout: bool = False,
    image_source: str | None = None,
    iso_source: str | None = None,
    run_ansible: bool = False,
) -> bool:
    node_ip = prov.get_node_ip(node)
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
        "gateway_ip": prov.paths.gateway_ip,
        "dns_ip": prov.paths.dns_ip,
        "hostname": hostname,
        "node_username": settings.node_username,
        "pubkey": pubkey,
        "target_disk": "vda" if prov.name == "libvirt" else None,
    }

    ks_cfg_path = local_staging_dir / f"ks_node{node}.cfg"
    template_engine.render_to_file("kickstart/ks.cfg.j2", context, ks_cfg_path)

    match prov:
        case BMCProvider() as bmc_prov:
            oemdrv_name = f"oemdrv_node{node}.img"
            oemdrv_path = local_staging_dir / oemdrv_name
            generate_oemdrv(ks_cfg_path, oemdrv_path, template_engine=template_engine)

            serve_path = (
                Path(remote_serve_dir)
                if remote_serve_dir is not None
                else (Path.home() / "scc_serve")
            )
            on_bastion = is_running_on_bastion(settings.bastion_hostname)
            if on_bastion:
                dest_path = serve_path / oemdrv_name
                dest_path.write_bytes(oemdrv_path.read_bytes())
            else:
                subprocess.run(
                    [
                        "scp",
                        "-q",
                        str(oemdrv_path),
                        f"{settings.bastion_ssh_host}:{serve_path}/{oemdrv_name}",
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
            chosen_img = image_source or iso_source
            if chosen_img is None:
                cached_golden = get_golden_image_dir() / "golden-rocky-base.qcow2"
                cached_cloud = get_image_cache_dir() / settings.cloud_image_name
                cached_iso = get_iso_cache_dir() / settings.iso_name
                if cached_golden.exists():
                    chosen_img = str(cached_golden)
                elif cached_cloud.exists():
                    chosen_img = str(cached_cloud)
                elif cached_iso.exists():
                    chosen_img = str(cached_iso)
                else:
                    chosen_img = settings.cloud_image_source

            if is_qcow2_image(chosen_img):
                cached_cloud_img = ensure_cached_cloud_image(
                    settings, image_source=chosen_img
                )
                prov_ok = libvirt_prov.provision_node(
                    node,
                    ks_cfg_path=ks_cfg_path,
                    pubkey=pubkey,
                    bios_profile=bios_profile.value,
                    image_path=cached_cloud_img,
                )
            else:
                cached_iso = ensure_cached_iso(settings, iso_source=chosen_img)
                prov_ok = libvirt_prov.provision_node(
                    node,
                    ks_cfg_path=ks_cfg_path,
                    pubkey=pubkey,
                    bios_profile=bios_profile.value,
                    iso_path=cached_iso,
                )

            if not prov_ok:
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
        if is_ssh_authenticated(
            node_ip,
            settings.node_username,
            bastion_ssh_host=prov.paths.bastion_ssh_host,
            key_path=privkey_path,
        ):
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

    if not run_ansible:
        progress.update(
            task_id,
            description=f"[bold green]✓ {hostname}[/bold green]: Image deployed & SSH ready ({node_ip}) [{elapsed_install // 60}m {elapsed_install % 60}s]",
            completed=100,
        )
        return True

    # Execute Node-Independent Ansible configuration
    progress.update(
        task_id,
        description=(
            f"[bold cyan]{hostname}[/bold cyan]: SSH online ({elapsed_install // 60}m {elapsed_install % 60}s). "
            "Running Ansible (node_independent)..."
        ),
    )

    ansible_ok = configure_command(
        settings,
        node=node,
        limit=hostname,
        tags="node_independent",
        exit_on_error=False,
        provider=prov.name,
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


def deploy_command(
    settings: ClusterSettings,
    node: int | list[int] | None = None,
    pubkey_path: Path | None = None,
    poll_timeout: int = 1800,
    bios_profile: BiosProfile = BiosProfile.HPC,
    no_timeout: bool = False,
    force_lock: bool = False,
    provider: str | None = None,
    image: str | None = None,
    iso: str | None = None,
    acquire_lock: bool = True,
    run_ansible: bool = False,
) -> bool:
    """Deploys the baremetal or virtualized OS image without running Ansible configuration."""
    ensure_db(settings)

    chosen_image = image or iso

    try:
        target_nodes = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return False

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
        return False

    pubkey = key_file.read_text(encoding="utf-8").strip()
    if key_file.name.endswith(".pub"):
        privkey_file = key_file.with_name(key_file.name[:-4])
    else:
        privkey_file = Path.home() / ".ssh" / "carla_scc_ed25519"

    template_engine = TemplateEngine()

    lock_ctx = (
        cluster_lock(
            settings,
            targets=target_nodes,
            operation="deploy",
            force=force_lock,
        )
        if acquire_lock
        else nullcontext()
    )

    try:
        with (
            lock_ctx,
            get_provider(settings, provider) as prov,
        ):
            active_prov_name = provider or settings.provider
            console.print(
                f"[cyan]Active Provider: [bold]{active_prov_name}[/bold][/cyan]"
            )

            remote_serve = prov.paths.remote_serve_dir or str(Path.home() / "scc_serve")
            local_staging_dir = prov.paths.staging_dir

            # BMC provider requires ephemeral HTTP server for virtual media
            match prov:
                case BMCProvider():
                    http_ctx = EphemeralRangeHTTPServer(
                        port=settings.bastion_http_port,
                        bind_ip=settings.bastion_http_ip,
                        bastion_ssh_host=settings.bastion_ssh_host,
                        bastion_hostname=settings.bastion_hostname,
                        template_engine=template_engine,
                        remote_serve_dir=remote_serve,
                    )
                    _ensure_bastion_iso(
                        settings,
                        template_engine=template_engine,
                        remote_serve_dir=remote_serve,
                        iso_source=chosen_image,
                    )
                case _:
                    http_ctx = nullcontext()

            with http_ctx:
                console.print(
                    f"[cyan]Initiating parallel baremetal deployment across {len(target_nodes)} node(s)...[/cyan]"
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
                                remote_serve_dir=remote_serve,
                                no_timeout=no_timeout,
                                image_source=chosen_image,
                                run_ansible=run_ansible,
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

                all_succeeded = all(results.get(n, False) for n in target_nodes)
                if all_succeeded and not run_ansible:
                    deployed_hostnames = [
                        settings.get_hostname(n) for n in target_nodes
                    ]
                    console.print(
                        f"\n[bold green]✓ Baremetal image deployment complete for {', '.join(deployed_hostnames)}.[/bold green]"
                    )
                    console.print(
                        "[dim]Next step: Run 'scc-carla configure' to execute Ansible configuration.[/dim]"
                    )
                return all_succeeded

    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force-lock to override or 'scc-carla lock list' to view active locks.[/dim]"
        )
        return False


from typing import Annotated

import typer


def deploy_cli(
    node: Annotated[
        list[int] | None,
        typer.Option(
            "--node",
            "-n",
            help="Node ID(s) to deploy (1, 2, or 3). Defaults to all nodes [1, 2, 3].",
        ),
    ] = None,
    pubkey: Annotated[
        Path | None, typer.Option("--pubkey", "-k", help="Path to SSH public key")
    ] = None,
    bios_profile: Annotated[
        BiosProfile,
        typer.Option(
            "--bios-profile",
            "-b",
            help="BIOS profile to configure (hpc, baseline, low_latency)",
        ),
    ] = BiosProfile.HPC,
    poll_timeout: Annotated[
        int,
        typer.Option(
            "--poll-timeout",
            "-t",
            help="Polling timeout in seconds for installation completion",
        ),
    ] = 1800,
    no_timeout: Annotated[
        bool,
        typer.Option(
            "--no-timeout",
            help="Disable polling timeout and wait indefinitely until installation completes",
        ),
    ] = False,
    force_lock: Annotated[
        bool,
        typer.Option(
            "--force-lock",
            "--force",
            "-f",
            help="Override and break any conflicting operational locks",
        ),
    ] = False,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            "-P",
            help="Node provider to use (libvirt, bmc, or chameleon)",
        ),
    ] = None,
    image: Annotated[
        str | None,
        typer.Option(
            "--image",
            "--iso",
            "-i",
            help="Path or URL to OS image (.qcow2 cloud image or .iso installer)",
        ),
    ] = None,
    cluster: Annotated[
        Path | None,
        typer.Option(
            "--cluster",
            "-c",
            help="Path to cluster manifest or values.yaml override file",
        ),
    ] = None,
) -> None:
    """Deploy cluster node OS image and wait for SSH without running Ansible."""
    from scc_core.manifest import load_manifest

    from scc_carla.config import get_settings

    settings = get_settings()

    manifest_file = cluster or (
        Path.cwd() / "values.yaml" if (Path.cwd() / "values.yaml").exists() else None
    )
    if manifest_file and manifest_file.exists():
        manifest = load_manifest(manifest_file)
        if provider is None:
            provider = manifest.provider
        if image is None and manifest.defaults.os.cloud_image:
            image = (
                manifest.defaults.os.cloud_image_source
                or manifest.defaults.os.cloud_image
            )

    deploy_command(
        settings,
        node=node,
        pubkey_path=pubkey,
        poll_timeout=poll_timeout,
        bios_profile=bios_profile,
        no_timeout=no_timeout,
        force_lock=force_lock,
        provider=provider,
        image=image,
    )
