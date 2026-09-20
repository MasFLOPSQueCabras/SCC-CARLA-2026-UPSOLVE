from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Self


class ProviderType(StrEnum):
    HELVETIOS = "helvetios"
    LIBVIRT = "libvirt"
    CHAMELEON = "chameleon"

    @classmethod
    def from_string(cls, val: str) -> ProviderType:
        normalized = val.lower().strip()
        if normalized in ("bmc", "helvetios"):
            return cls.HELVETIOS
        if normalized in ("vm", "libvirt"):
            return cls.LIBVIRT
        if normalized in ("chameleon", "chi"):
            return cls.CHAMELEON
        raise ValueError(
            f"Unknown provider '{val}'. Valid options: 'helvetios', 'libvirt', 'chameleon'."
        )


class PowerState(StrEnum):
    ON = "ON"
    OFF = "OFF"
    RESTARTING = "RESTARTING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ProviderPaths:
    """Declared filesystem, storage, and networking parameters for a provider."""

    staging_dir: Path
    iso_cache_dir: Path
    state_db_path: Path | str
    gateway_ip: str = "10.2.72.254"
    dns_ip: str = "10.2.72.254"
    storage_dir: Path | None = None
    remote_serve_dir: str | None = None
    bastion_ssh_host: str | None = None


class NodeProvider(ABC):
    """Abstract base class for bare-metal, virtualized, or cloud node providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier for provider (e.g. 'helvetios', 'libvirt', 'chameleon')."""

    @property
    @abstractmethod
    def paths(self) -> ProviderPaths:
        """Declared filesystem and storage paths for this provider."""

    @contextmanager
    def deployment_session(self) -> Generator[None]:
        """Context manager managing provider-specific deployment infrastructure.

        Default is a no-op context manager. Override to manage ephemeral media
        servers, stage remote files, or prepare bridges.
        """
        yield

    @abstractmethod
    def get_node_ip(self, node_id: int) -> str:
        """Returns the OS IP address for a given node ID."""

    @abstractmethod
    def power_on(self, node_id: int) -> bool:
        """Powers on the target node."""

    @abstractmethod
    def power_off(self, node_id: int, graceful: bool = True) -> bool:
        """Powers off the target node gracefully or forcefully."""

    @abstractmethod
    def power_reset(self, node_id: int, graceful: bool = True) -> bool:
        """Reboots/resets the target node."""

    @abstractmethod
    def get_power_status(self, node_id: int) -> PowerState:
        """Retrieves the current power state of the target node."""

    @abstractmethod
    def get_power_metrics(self, node_id: int) -> dict[str, Any] | None:
        """Retrieves live power telemetry (Watts, etc.) if supported by provider."""

    @abstractmethod
    def provision_node(
        self,
        node_id: int,
        pubkey: str,
        bios_profile: str = "hpc",
        image_source: str | None = None,
        template_engine: Any | None = None,
        staging_dir: Path | None = None,
        progress_callback: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> bool:
        """Bootstraps/provisions the target node."""

    def post_provision(self, node_id: int) -> None:
        """Lifecycle hook invoked after node completes SSH bootstrap."""

    @abstractmethod
    def teardown_node(self, node_id: int) -> bool:
        """Decommissions or tears down the target node."""

    def close(self) -> None:
        """Closes any underlying connections or client sessions."""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()
