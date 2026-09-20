import getpass
import json
import logging
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

import turso

from scc_carla.config import ClusterSettings
from scc_carla.http_server import is_running_on_bastion
from scc_carla.paths import get_local_db_path
from scc_carla.state_worker import execute_action, get_file_hash

logger = logging.getLogger(__name__)

_WORKER_SYNCED = False
_DB_THREAD_LOCK = threading.RLock()


class NodeLifecycle(StrEnum):
    UNPROVISIONED = "UNPROVISIONED"
    INSTALLING = "INSTALLING"
    BOOTSTRAPPED = "BOOTSTRAPPED"
    READY = "READY"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True)
class NodeState:
    node_id: int
    hostname: str
    os_ip: str
    bmc_ip: str
    state: NodeLifecycle
    pubkey: str | None = None
    bios_profile: str | None = None
    last_updated: str | None = None


@dataclass(frozen=True)
class LockInfo:
    resource: str
    holder: str
    operation: str
    acquired_at: str
    timeout_sec: int
    elapsed_sec: int
    is_expired: bool


class LockError(Exception):
    """Raised when an operational lock cannot be acquired due to a conflict."""


def _ensure_worker_synced(settings: ClusterSettings) -> None:
    """Verifies that the bastion state_worker.py is present and matches the local SHA-256 hash."""
    global _WORKER_SYNCED
    if _WORKER_SYNCED:
        return

    with _DB_THREAD_LOCK:
        if _WORKER_SYNCED:
            return

        local_worker_path = Path(__file__).with_name("state_worker.py")
        local_hash = get_file_hash(local_worker_path)

        remote_dir = Path.home() / ".config" / "scc_carla"
        remote_hash_file = remote_dir / "state_worker.hash"
        remote_worker_file = remote_dir / "state_worker.py"

        check_cmd = [
            "ssh",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPath=/tmp/scc-carla-ssh-%r@%h:%p",
            "-o",
            "ControlPersist=60s",
            settings.bastion_ssh_host,
            f"cat {remote_hash_file} 2>/dev/null || true",
        ]
        res = subprocess.run(check_cmd, capture_output=True, text=True, check=False)
        remote_hash = res.stdout.strip()

        if remote_hash == local_hash:
            _WORKER_SYNCED = True
            return

        logger.info("Syncing state_worker.py to bastion (hash: %s)...", local_hash[:8])
        mkdir_cmd = [
            "ssh",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPath=/tmp/scc-carla-ssh-%r@%h:%p",
            "-o",
            "ControlPersist=60s",
            settings.bastion_ssh_host,
            f"mkdir -p {remote_dir}",
        ]
        subprocess.run(mkdir_cmd, check=True, capture_output=True)

        scp_cmd = [
            "scp",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPath=/tmp/scc-carla-ssh-%r@%h:%p",
            "-o",
            "ControlPersist=60s",
            "-q",
            str(local_worker_path),
            f"{settings.bastion_ssh_host}:{remote_worker_file}",
        ]
        subprocess.run(scp_cmd, check=True)

        write_hash_cmd = [
            "ssh",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPath=/tmp/scc-carla-ssh-%r@%h:%p",
            "-o",
            "ControlPersist=60s",
            settings.bastion_ssh_host,
            f"echo '{local_hash}' > {remote_hash_file}",
        ]
        subprocess.run(write_hash_cmd, check=True, capture_output=True)
        _WORKER_SYNCED = True


def _run_bastion_ssh_action(
    settings: ClusterSettings, action: str, args: dict[str, Any]
) -> Any:
    """Executes a database action on the bastion host via SSH using state_worker.py."""
    _ensure_worker_synced(settings)

    payload = json.dumps(
        {
            "db_path": settings.bastion_state_db_path,
            "action": action,
            "args": args,
        }
    )
    cmd = [
        "ssh",
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPath=/tmp/scc-carla-ssh-%r@%h:%p",
        "-o",
        "ControlPersist=60s",
        settings.bastion_ssh_host,
        "python3 ~/.config/scc_carla/state_worker.py",
    ]

    last_err = ""
    for attempt in range(3):
        res = subprocess.run(
            cmd,
            input=payload,
            text=True,
            capture_output=True,
            check=False,
        )
        if res.returncode == 0:
            try:
                data = json.loads(res.stdout)
                if data.get("status") == "ok":
                    return data.get("result")
                raise RuntimeError(
                    f"Bastion DB error: {data.get('error', 'unknown error')}"
                )
            except json.JSONDecodeError:
                last_err = f"Invalid JSON response: {res.stdout}"
        else:
            last_err = (
                res.stderr.strip() or f"SSH process exited with code {res.returncode}"
            )
            # If control socket error, clean up socket
            if (
                "ControlSocket" in last_err
                or "mux" in last_err.lower()
                or "closed" in last_err.lower()
            ):
                subprocess.run(
                    [
                        "ssh",
                        "-O",
                        "exit",
                        "-o",
                        "ControlPath=/tmp/scc-carla-ssh-%r@%h:%p",
                        settings.bastion_ssh_host,
                    ],
                    capture_output=True,
                    check=False,
                )
        time.sleep(0.3 * (attempt + 1))

    raise RuntimeError(f"Failed to execute Turso DB action on bastion: {last_err}")


def _dispatch_db_action(
    settings: ClusterSettings, action: str, args: dict[str, Any]
) -> Any:
    """Dispatches database action to bastion Turso instance directly, locally, or over SSH."""
    with _DB_THREAD_LOCK:
        if (
            is_running_on_bastion(settings.bastion_hostname)
            or settings.provider == "libvirt"
            or os.environ.get("SCC_LOCAL_DB") == "1"
        ):
            local_db_path = (
                os.path.expanduser(settings.bastion_state_db_path)
                if is_running_on_bastion(settings.bastion_hostname)
                else str(get_local_db_path().resolve())
            )
            os.makedirs(os.path.dirname(local_db_path), exist_ok=True)
            db_is_new = (
                not os.path.exists(local_db_path) or os.path.getsize(local_db_path) == 0
            )
            conn = turso.connect(local_db_path)
            try:
                if db_is_new and action != "init_db":
                    execute_action(conn, "init_db", {"team_id": settings.team_id})
                return execute_action(conn, action, args)
            finally:
                conn.close()

        return _run_bastion_ssh_action(settings, action, args)


def init_db(settings: ClusterSettings) -> None:
    _dispatch_db_action(settings, "init_db", {"team_id": settings.team_id})


def ensure_db(settings: ClusterSettings) -> None:
    init_db(settings)


def get_all_nodes(settings: ClusterSettings) -> list[NodeState]:
    raw_nodes = _dispatch_db_action(
        settings, "get_all_nodes", {"team_id": settings.team_id}
    )
    return [
        NodeState(
            node_id=r["node_id"],
            hostname=r["hostname"],
            os_ip=r["os_ip"],
            bmc_ip=r["bmc_ip"],
            state=(
                NodeLifecycle(r["state"])
                if r["state"] in NodeLifecycle._value2member_map_
                else NodeLifecycle.UNPROVISIONED
            ),
            pubkey=r["pubkey"],
            bios_profile=r["bios_profile"],
            last_updated=r["last_updated"],
        )
        for r in raw_nodes
    ]


def update_node_state(
    settings: ClusterSettings,
    node_id: int,
    state: NodeLifecycle | None = None,
    pubkey: str | None = None,
    bios_profile: str | None = None,
) -> None:
    args: dict[str, Any] = {"node_id": node_id}
    if state is not None:
        args["state"] = state.value
    if pubkey is not None:
        args["pubkey"] = pubkey
    if bios_profile is not None:
        args["bios_profile"] = bios_profile
    _dispatch_db_action(settings, "update_node_state", args)


def reset_cluster_state(settings: ClusterSettings) -> None:
    _dispatch_db_action(settings, "reset_cluster_state", {})


def acquire_locks(
    settings: ClusterSettings,
    resources: list[str],
    holder: str,
    operation: str,
    timeout_sec: int = 1800,
    force: bool = False,
) -> tuple[bool, str | None]:
    result = _dispatch_db_action(
        settings,
        "acquire_locks",
        {
            "resources": resources,
            "holder": holder,
            "operation": operation,
            "timeout_sec": timeout_sec,
            "force": force,
        },
    )
    return bool(result.get("acquired")), result.get("conflict")


def acquire_lock(
    settings: ClusterSettings,
    resource: str,
    holder: str,
    operation: str,
    timeout_sec: int = 1800,
    force: bool = False,
) -> tuple[bool, str | None]:
    return acquire_locks(
        settings,
        [resource],
        holder=holder,
        operation=operation,
        timeout_sec=timeout_sec,
        force=force,
    )


def release_locks(
    settings: ClusterSettings,
    resources: list[str],
    holder: str | None = None,
    force: bool = False,
) -> bool:
    return bool(
        _dispatch_db_action(
            settings,
            "release_locks",
            {
                "resources": resources,
                "holder": holder,
                "force": force,
            },
        )
    )


def release_lock(
    settings: ClusterSettings,
    resource: str,
    holder: str | None = None,
    force: bool = False,
) -> bool:
    return release_locks(settings, [resource], holder=holder, force=force)


def get_active_locks(settings: ClusterSettings) -> list[LockInfo]:
    raw_locks = _dispatch_db_action(settings, "get_active_locks", {})
    return [
        LockInfo(
            resource=r["resource"],
            holder=r["holder"],
            operation=r["operation"],
            acquired_at=r["acquired_at"],
            timeout_sec=r["timeout_sec"],
            elapsed_sec=r["elapsed_sec"],
            is_expired=r["is_expired"],
        )
        for r in raw_locks
    ]


def break_lock(settings: ClusterSettings, resource: str) -> bool:
    return bool(_dispatch_db_action(settings, "break_lock", {"resource": resource}))


class ClusterLock:
    """Context manager for distributed operational locking on shared Turso database.

    Acquires resource locks on entrance, rolling back and raising LockError on conflict,
    and reliably releases held locks on exit.
    """

    def __init__(
        self,
        settings: ClusterSettings,
        resources: list[str],
        operation: str,
        timeout_sec: int = 1800,
        force: bool = False,
        holder: str | None = None,
    ) -> None:
        self.settings = settings
        self.resources = resources
        self.operation = operation
        self.timeout_sec = timeout_sec
        self.force = force
        self.holder = holder or f"{getpass.getuser()}@{socket.gethostname()}"
        self._acquired: list[str] = []

    def __enter__(self) -> Self:
        if not self.resources:
            return self
        success, conflict_msg = acquire_locks(
            self.settings,
            self.resources,
            self.holder,
            self.operation,
            timeout_sec=self.timeout_sec,
            force=self.force,
        )
        if not success:
            raise LockError(
                conflict_msg
                or f"Failed to acquire lock for resources: {self.resources}"
            )
        self._acquired = list(self.resources)
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self._acquired:
            try:
                release_locks(
                    self.settings,
                    self._acquired,
                    holder=self.holder,
                    force=self.force,
                )
            except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                logger.warning("Failed to release operational locks: %s", e)
            finally:
                self._acquired = []
