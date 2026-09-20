from abc import ABC, abstractmethod
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


class NodeProvider(ABC):
    """Abstract base class for bare-metal, virtualized, or cloud node providers."""

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
