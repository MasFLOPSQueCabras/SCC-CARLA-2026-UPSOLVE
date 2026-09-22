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

    @classmethod
    def from_string(cls, val: str) -> ProviderType:
        return cls(val.lower().strip())


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
        """Identifier for provider (e.g. 'helvetios', 'libvirt')."""

    @property
    @abstractmethod
    def paths(self) -> ProviderPaths:
        """Declared filesystem and storage paths for this provider."""

    @classmethod
    def list_presets(cls) -> list[str]:
        """Lists available preset profile names for this provider."""
        return ["standard"]

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

    def get_bios_settings(self, node_id: int) -> dict[str, Any]:
        raise NotImplementedError("Provider does not support BIOS settings")

    def set_bios_settings(self, node_id: int, attributes: dict[str, Any]) -> bool:
        raise NotImplementedError("Provider does not support BIOS settings")

    def node_exists(self, node_id: int) -> bool:
        raise NotImplementedError("Provider must implement resource discovery")

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
