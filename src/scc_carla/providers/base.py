from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Self


class ProviderType(StrEnum):
    LIBVIRT = "libvirt"
    BMC = "bmc"
    CHAMELEON = "chameleon"


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
        """String identifier for provider (e.g. 'libvirt', 'bmc', 'chameleon')."""

    @property
    @abstractmethod
    def paths(self) -> ProviderPaths:
        """Declared filesystem and storage paths for this provider."""

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
        """Retrieves live power telemetry (Watts, etc.) if supported by the provider."""

    @abstractmethod
    def provision_node(
        self,
        node_id: int,
        ks_cfg_path: Path,
        pubkey: str,
        bios_profile: str = "hpc",
        **kwargs: Any,
    ) -> bool:
        """Bootstraps/provisions the target node."""

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
