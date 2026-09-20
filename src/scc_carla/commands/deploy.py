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

from scc_carla.bios import BiosProfile
from scc_carla.commands.configure import configure_command
from scc_carla.config import ClusterSettings
from scc_carla.db import (
    LockError,
    NodeLifecycle,
    ensure_db,
    update_node_state,
)
from scc_carla.nodes import resolve_target_nodes
from scc_carla.ops import cluster_lock
from scc_carla.providers.base import NodeProvider
from scc_carla.providers.factory import get_provider
from scc_carla.ssh import is_ssh_authenticated
from scc_carla.templating import TemplateEngine

console = Console()


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
    no_timeout: bool = False,
    image_source: str | None = None,
    iso_source: str | None = None,
    run_ansible: bool = False,
) -> bool:
    node_ip = prov.get_node_ip(node)
    hostname = settings.get_hostname(node)

    progress.update(
        task_id,
        description=f"[bold cyan]{hostname}[/bold cyan]: Initializing provisioning via {prov.name}...",
    )
    update_node_state(
        settings,
        node,
        NodeLifecycle.INSTALLING,
        pubkey=pubkey,
        bios_profile=bios_profile.value,
    )

    chosen_img = image_source or iso_source

    prov_ok = prov.provision_node(
        node_id=node,
        pubkey=pubkey,
        bios_profile=bios_profile.value,
        image_source=chosen_img,
        template_engine=template_engine,
        staging_dir=local_staging_dir,
        progress_callback=lambda desc: progress.update(
            task_id, description=f"[bold cyan]{hostname}[/bold cyan]: {desc}"
        ),
    )

    if not prov_ok:
        progress.update(
            task_id,
            description=f"[bold red]✗ {hostname}[/bold red]: Failed to provision via {prov.name}.",
            completed=100,
        )
        update_node_state(settings, node, NodeLifecycle.OFFLINE)
        return False

    start_time = time.time()
    ssh_ready = False
    timeout_suffix = " (no timeout)" if (no_timeout or poll_timeout <= 0) else ""

    progress.update(
        task_id,
        description=f"[bold cyan]{hostname}[/bold cyan]: Booting OS & waiting for SSH{timeout_suffix}...",
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

    # Post-provision hook
    prov.post_provision(node)

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

            local_staging_dir = prov.paths.staging_dir

            with prov.deployment_session():
                console.print(
                    f"[cyan]Initiating parallel deployment across {len(target_nodes)} node(s)...[/cyan]"
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
