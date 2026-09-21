"""Provider operations used by the lifecycle service."""

import json
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from importlib.resources import files
from pathlib import Path

from cabrita.bootstrap.artifacts import ArtifactCache
from cabrita.bootstrap.http_server import EphemeralRangeHTTPServer
from cabrita.bootstrap.media import prepare_media
from cabrita.bootstrap.remote import BastionMedia
from cabrita.core.bootstrap import BootstrapMethod, CustomPreparer
from cabrita.core.lifecycle.service import Observation, StateStore
from cabrita.core.manifest import NodeSpec
from cabrita.core.providers.base import NodeProvider, PowerState
from cabrita.core.resolved import ResolvedCluster
from cabrita.core.templating import TemplateEngine
from cabrita.ssh import is_ssh_authenticated


class ProviderBackend:
    def __init__(
        self,
        cluster: ResolvedCluster,
        provider: NodeProvider,
        state: StateStore,
        work_dir: Path,
        public_key: Path,
        private_key: Path,
        timeout: int,
        artifact_cache: ArtifactCache,
    ) -> None:
        self.cluster = cluster
        self.provider = provider
        self.state = state
        self.work_dir = work_dir
        self.public_key = public_key
        self.private_key = private_key
        self.timeout = timeout
        self.artifact_cache = artifact_cache
        template_paths = [
            Path(str(files("cabrita.providers.libvirt_backend").joinpath("templates"))),
            Path(str(files("cabrita.providers.helvetios").joinpath("templates"))),
        ]
        if cluster.manifest.bootstrap.templates:
            template_paths.insert(0, cluster.manifest.bootstrap.templates)
        self.templates = TemplateEngine(template_paths)

    @contextmanager
    def installation_session(self) -> Iterator[None]:
        with ExitStack() as stack:
            stack.enter_context(self.provider.deployment_session())
            if (
                self.provider.name == "libvirt"
                and self.cluster.manifest.bootstrap.method
                == BootstrapMethod.GOLDEN_RESTORE
            ):
                host, port = self.cluster.recovery_endpoint()
                stack.enter_context(
                    EphemeralRangeHTTPServer(
                        port=port,
                        bind_ip=host,
                        bastion_ssh_host="",
                        bastion_hostname=socket.gethostname(),
                        remote_serve_dir=self.artifact_cache.directory,
                    )
                )
            yield

    def _reachable(self, node: NodeSpec) -> bool:
        return is_ssh_authenticated(
            node.ip,
            self.cluster.manifest.defaults.os.username,
            bastion_ssh_host=self.provider.paths.bastion_ssh_host,
            key_path=self.private_key,
            timeout=3,
        )

    def observe(self, node: NodeSpec) -> Observation:
        power = self.provider.get_power_status(node.id)
        if power == PowerState.UNKNOWN:
            raise RuntimeError(f"Cannot determine power state for {node.hostname}")
        reachable = self._reachable(node) if power == PowerState.ON else False
        exists = self.provider.node_exists(node.id)
        if self.provider.name == "helvetios":
            exists = reachable or self.state.read(node.id).phase != "new"
        return Observation(exists, power == PowerState.ON, reachable)

    def deploy(self, node: NodeSpec, *, reinstall: bool) -> None:
        bootstrap = self.cluster.manifest.bootstrap
        node_dir = self.work_dir / f"node-{node.id}"
        node_dir.mkdir(parents=True, exist_ok=True)
        if (
            self.provider.name == "helvetios"
            and bootstrap.method != BootstrapMethod.CUSTOM
        ):
            source, auxiliary = BastionMedia(self.cluster, self.templates).prepare(
                node, self.public_key.read_text().strip(), node_dir, self.artifact_cache
            )
            if not self.provider.provision_node(
                node.id,
                self.public_key.read_text().strip(),
                image_source=source,
                oemdrv_path=auxiliary,
            ):
                raise RuntimeError(f"Provisioning failed for {node.hostname}")
            return
        match bootstrap.method:
            case BootstrapMethod.CUSTOM:
                assert bootstrap.prepare is not None
                result = CustomPreparer().prepare(
                    bootstrap.prepare,
                    {
                        "cluster": self.cluster.manifest.model_dump(mode="json"),
                        "node": node.model_dump(mode="json"),
                    },
                    node_dir,
                    bastion=self.cluster.manifest.bastion.ssh_host,
                    remote_dir=f"/tmp/cabrita-{self.cluster.identity}-node-{node.id}",
                )
                source = str(result.path)
            case (
                BootstrapMethod.CLOUD_INIT
                | BootstrapMethod.OEMDRV
                | BootstrapMethod.EMBEDDED_KICKSTART
                | BootstrapMethod.GOLDEN_RESTORE
            ):
                media = prepare_media(
                    self.cluster,
                    node,
                    self.artifact_cache,
                    self.templates,
                    self.public_key.read_text().strip(),
                    node_dir,
                )
                source = str(media.source)
            case _:
                raise NotImplementedError(
                    f"Media preparation for {bootstrap.method} is not implemented yet"
                )
        auxiliary = (
            str(media.auxiliary)
            if bootstrap.method != BootstrapMethod.CUSTOM and media.auxiliary
            else None
        )
        succeeded = self.provider.provision_node(
            node.id,
            self.public_key.read_text().strip(),
            image_source=source,
            template_engine=self.templates,
            staging_dir=node_dir,
            reinstall=reinstall,
            bootstrap_method=bootstrap.method.value,
            oemdrv_path=auxiliary,
            user_data=bootstrap.user_data,
            network_config=bootstrap.network_config,
        )
        if not succeeded:
            raise RuntimeError(f"Provisioning failed for {node.hostname}")

    def start(self, node: NodeSpec) -> None:
        if not self.provider.power_on(node.id):
            raise RuntimeError(f"Failed to start {node.hostname}")

    def verify(self, node: NodeSpec) -> None:
        deadline = time.monotonic() + self.timeout
        installation = self.state.read(node.id).phase == "installing"
        finalized = not installation
        while time.monotonic() < deadline:
            reachable = self._reachable(node)
            if not finalized and (
                reachable or self.provider.get_power_status(node.id) == PowerState.OFF
            ):
                if (
                    reachable
                    and self.cluster.manifest.bootstrap.method
                    == BootstrapMethod.CLOUD_INIT
                ):
                    command = [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "StrictHostKeyChecking=no",
                        "-o",
                        "UserKnownHostsFile=/dev/null",
                        "-o",
                        "ConnectTimeout=10",
                        "-i",
                        str(self.private_key),
                        f"{self.cluster.manifest.defaults.os.username}@{node.ip}",
                        "sudo cloud-init status --wait --long",
                    ]
                    with (self.work_dir / f"node-{node.id}" / "cloud-init.log").open(
                        "w"
                    ) as log:
                        subprocess.run(
                            command,
                            check=True,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            timeout=max(1, deadline - time.monotonic()),
                        )
                self.provider.post_provision(node.id)
                finalized = True
            elif reachable:
                if (
                    self.cluster.manifest.bootstrap.method
                    == BootstrapMethod.GOLDEN_RESTORE
                ):
                    expected = json.loads(
                        (self.work_dir / f"node-{node.id}" / "restore.json").read_text()
                    )["identity"]
                    command = [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "StrictHostKeyChecking=no",
                        "-o",
                        "UserKnownHostsFile=/dev/null",
                        "-o",
                        "ConnectTimeout=10",
                        "-i",
                        str(self.private_key),
                    ]
                    if self.provider.paths.bastion_ssh_host:
                        command += ["-J", self.provider.paths.bastion_ssh_host]
                    command += [
                        f"{self.cluster.manifest.defaults.os.username}@{node.ip}",
                        "cat /var/lib/cabrita/restored.json",
                    ]
                    result = subprocess.run(
                        command, check=True, capture_output=True, text=True, timeout=30
                    )
                    if json.loads(result.stdout) != expected:
                        raise ValueError(
                            f"Recovery completion marker mismatch for {node.hostname}"
                        )
                return
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        raise TimeoutError(f"SSH verification timed out for {node.hostname}")

    def configure(self, nodes: tuple[NodeSpec, ...]) -> None:
        configuration = self.cluster.manifest.configuration
        if configuration.profile == "none":
            return
        playbook = configuration.playbook
        if playbook is None:
            playbook = Path(
                str(files("cabrita").joinpath("ansible/playbooks/site.yaml"))
            )
        self.work_dir.mkdir(parents=True, exist_ok=True)
        inventory = self.work_dir / "inventory.json"
        host_vars = {
            node.hostname: {
                "ansible_host": node.ip,
                "ansible_user": self.cluster.manifest.defaults.os.username,
                "ansible_ssh_private_key_file": str(self.private_key),
                "ansible_ssh_common_args": "-o StrictHostKeyChecking=accept-new"
                + (
                    f" -J {self.cluster.manifest.bastion.ssh_host}"
                    if self.provider.name == "helvetios"
                    else ""
                ),
            }
            for node in nodes
        }
        inventory.write_text(
            json.dumps(
                {"all": {"hosts": host_vars, "vars": configuration.inputs}}, indent=2
            )
        )
        with (self.work_dir / "configure.log").open("w") as log:
            subprocess.run(
                ["ansible-playbook", "-i", str(inventory), str(playbook)],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=self.timeout,
            )

    def stop(self, node: NodeSpec) -> None:
        if not self.provider.power_off(node.id):
            raise RuntimeError(f"Failed to stop {node.hostname}")
        deadline = time.monotonic() + min(self.timeout, 120)
        while time.monotonic() < deadline:
            if self.provider.get_power_status(node.id) == PowerState.OFF:
                return
            time.sleep(1)
        raise TimeoutError(f"Shutdown timed out for {node.hostname}")

    def destroy(self, node: NodeSpec) -> None:
        if not self.provider.teardown_node(node.id):
            raise RuntimeError(f"Failed to destroy {node.hostname}")
