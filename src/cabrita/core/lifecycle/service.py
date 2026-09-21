"""Lifecycle orchestration with explicit dependencies and durable checkpoints."""

import fcntl
import hashlib
import json
import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol

from cabrita.core.manifest import NodeSpec
from cabrita.core.resolved import ResolvedCluster

type Operation = Literal["up", "deploy", "configure", "down", "destroy"]
type Phase = Literal["new", "installing", "bootstrapped", "ready"]


@dataclass(frozen=True, slots=True)
class Observation:
    exists: bool
    running: bool
    reachable: bool


@dataclass(slots=True)
class Checkpoint:
    phase: Phase = "new"
    fingerprint: str = ""
    error: str | None = None
    configuration_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class PlannedNode:
    id: int
    hostname: str
    action: str
    observed: Observation


class LifecycleBackend(Protocol):
    def installation_session(self) -> AbstractContextManager[None]: ...
    def observe(self, node: NodeSpec) -> Observation: ...
    def deploy(self, node: NodeSpec, *, reinstall: bool) -> None: ...
    def start(self, node: NodeSpec) -> None: ...
    def configure(self, nodes: tuple[NodeSpec, ...]) -> None: ...
    def verify(self, node: NodeSpec) -> None: ...
    def stop(self, node: NodeSpec) -> None: ...
    def destroy(self, node: NodeSpec) -> None: ...


class StateStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def read(self, node: int) -> Checkpoint:
        path = self.directory / f"node-{node}.json"
        if not path.exists():
            return Checkpoint()
        return Checkpoint(**json.loads(path.read_text()))

    def write(self, node: int, checkpoint: Checkpoint) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"node-{node}.json"
        temporary = path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(asdict(checkpoint), stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)


class LockManager(Protocol):
    def acquire(self, keys: list[str]) -> AbstractContextManager[None]: ...


class ResourceLocks:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @contextmanager
    def acquire(self, keys: list[str]) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            for key in sorted(set(keys)):
                path = self.directory / f"{key}.lock"
                stream = stack.enter_context(path.open("a+"))
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError(f"Resource is locked: {key}") from exc
            yield


class LifecycleService[BackendT: LifecycleBackend]:
    def __init__(
        self,
        cluster: ResolvedCluster,
        backend: BackendT,
        state: StateStore,
        locks: LockManager,
        *,
        max_workers: int = 4,
        libvirt_uri: str = "qemu:///system",
    ) -> None:
        if not 1 <= max_workers <= 8:
            raise ValueError("Concurrency must be between 1 and 8")
        self.cluster = cluster
        self.backend = backend
        self.state = state
        self.locks = locks
        self.max_workers = max_workers
        self.libvirt_uri = libvirt_uri
        self.fingerprint = hashlib.sha256(
            cluster.manifest.model_dump_json(
                exclude={"configuration", "access"}
            ).encode()
        ).hexdigest()

    def _configuration_fingerprint(self) -> str:
        return hashlib.sha256(
            self.cluster.manifest.configuration.model_dump_json().encode()
        ).hexdigest()

    def _action(
        self,
        node: NodeSpec,
        observed: Observation,
        operation: Operation,
        reinstall: bool,
    ) -> str:
        checkpoint = self.state.read(node.id)
        if operation in ("down", "destroy"):
            if operation == "destroy" and checkpoint.phase == "new":
                return "unmanaged"
            return operation if observed.exists else "noop"
        if (
            checkpoint.phase != "new"
            and checkpoint.fingerprint != self.fingerprint
            and not reinstall
        ):
            return "replacement-required"
        if operation == "configure":
            return "configure" if observed.reachable else "unreachable"
        if reinstall:
            return "reinstall"
        if checkpoint.phase == "new":
            return "unmanaged" if observed.exists or observed.reachable else "deploy"
        if not observed.exists:
            return (
                "replacement-required"
                if checkpoint.phase in ("ready", "bootstrapped")
                else "deploy"
            )
        if checkpoint.phase == "installing":
            return "resume"
        return (
            "verify"
            if operation == "deploy" or checkpoint.phase == "ready"
            else "configure"
        )

    def plan(
        self,
        operation: Operation = "up",
        targets: list[int] | None = None,
        *,
        reinstall: bool = False,
    ) -> tuple[PlannedNode, ...]:
        planned = []
        for node in self.cluster.nodes(targets):
            observed = self.backend.observe(node)
            planned.append(
                PlannedNode(
                    node.id,
                    node.hostname,
                    self._action(node, observed, operation, reinstall),
                    observed,
                )
            )
        return tuple(planned)

    def _parallel(
        self, nodes: tuple[NodeSpec, ...], task: Callable[[NodeSpec], None]
    ) -> None:
        def guarded(node: NodeSpec) -> Exception | None:
            try:
                task(node)
                return None
            except Exception as exc:  # noqa: BLE001 — retain worker failures before aggregation
                checkpoint = self.state.read(node.id)
                checkpoint.error = str(exc)
                self.state.write(node.id, checkpoint)
                return exc

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            failures = [
                error for error in executor.map(guarded, nodes) if error is not None
            ]
        if failures:
            raise ExceptionGroup("Node operations failed", failures)

    def execute(
        self,
        operation: Operation,
        targets: list[int] | None = None,
        *,
        reinstall: bool = False,
    ) -> tuple[PlannedNode, ...]:
        nodes = self.cluster.nodes(targets)
        keys = [self.cluster.lock_key(node, self.libvirt_uri) for node in nodes]
        if self.cluster.manifest.provider in ("helvetios", "bmc") and operation in (
            "up",
            "deploy",
        ):
            bastion = self.cluster.manifest.bastion
            keys.append(
                hashlib.sha256(
                    f"http://{bastion.http_bind_ip}:{bastion.http_port}".encode()
                ).hexdigest()
            )
        with self.locks.acquire(keys), ExitStack() as stack:
            plan = self.plan(
                operation, [node.id for node in nodes], reinstall=reinstall
            )
            blocked = [
                entry
                for entry in plan
                if entry.action in ("unmanaged", "replacement-required", "unreachable")
            ]
            if blocked:
                raise RuntimeError(
                    f"Cannot execute: {[(entry.id, entry.action) for entry in blocked]}; inspect state or explicitly use --reinstall"
                )
            if any(entry.action in ("deploy", "reinstall", "resume") for entry in plan):
                stack.enter_context(self.backend.installation_session())
            actions = {entry.id: entry.action for entry in plan}

            def apply(node: NodeSpec) -> None:
                checkpoint = self.state.read(node.id)
                action = actions[node.id]
                match action:
                    case "noop":
                        return
                    case "down":
                        self.backend.stop(node)
                        return
                    case "destroy":
                        self.backend.destroy(node)
                        self.state.write(node.id, Checkpoint())
                        return
                    case "deploy" | "reinstall":
                        checkpoint = Checkpoint("installing", self.fingerprint)
                        self.state.write(node.id, checkpoint)
                        self.backend.deploy(node, reinstall=reinstall)
                    case _:
                        if not next(
                            entry for entry in plan if entry.id == node.id
                        ).observed.running:
                            self.backend.start(node)
                if operation in ("up", "deploy", "configure"):
                    self.backend.verify(node)
                    if checkpoint.phase in ("new", "installing"):
                        checkpoint.phase = "bootstrapped"
                    checkpoint.fingerprint = self.fingerprint
                    checkpoint.error = None
                    self.state.write(node.id, checkpoint)

            self._parallel(nodes, apply)
            if operation in ("up", "configure"):
                needs_configuration = operation == "configure" or any(
                    self.state.read(node.id).phase != "ready"
                    or self.state.read(node.id).configuration_fingerprint
                    != self._configuration_fingerprint()
                    for node in nodes
                )
                if needs_configuration:
                    for node in nodes:
                        checkpoint = self.state.read(node.id)
                        checkpoint.phase = "bootstrapped"
                        self.state.write(node.id, checkpoint)
                    try:
                        self.backend.configure(nodes)
                        self._parallel(nodes, self.backend.verify)
                    except Exception as exc:
                        for node in nodes:
                            checkpoint = self.state.read(node.id)
                            checkpoint.error = str(exc)
                            self.state.write(node.id, checkpoint)
                        raise
                    for node in nodes:
                        self.state.write(
                            node.id,
                            Checkpoint(
                                "ready",
                                self.fingerprint,
                                configuration_fingerprint=self._configuration_fingerprint(),
                            ),
                        )
            return plan
