"""Lightweight Dependency Injection (DI) and Lazy Provider Registry."""

import importlib
import importlib.metadata
from collections.abc import Callable
from typing import Any

from cabrita.core.providers.base import NodeProvider, ProviderType


class ProviderNotInstalledError(RuntimeError):
    """Raised when a requested provider is not installed or missing dependencies."""

    def __init__(self, provider: str, reason: str | None = None) -> None:
        canonical = provider.lower()
        hint = (
            f"uv tool install 'cabrita[{canonical}]'"
            if canonical in ("libvirt", "helvetios")
            else "check package dependencies"
        )
        msg = (
            f"Provider '{provider}' is not available or its dependencies are missing.\n"
            f"Details: {reason or 'Package not installed'}\n"
            f"Hint: {hint}"
        )
        super().__init__(msg)
        self.provider = provider
        self.reason = reason


class ProviderRegistry:
    """Registry managing NodeProvider implementations with lazy loading and plugin discovery."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., NodeProvider]] = {}
        self._lazy_loaders: dict[str, tuple[str, str]] = {}
        self._discovered_plugins: bool = False

    def register(
        self,
        name: str,
        factory: Callable[..., NodeProvider],
    ) -> None:
        """Register a provider factory directly."""
        key = name.lower().strip()
        self._factories[key] = factory

    def register_lazy(
        self,
        name: str,
        module_path: str,
        class_name: str,
    ) -> None:
        """Register a provider by module path and class name to be loaded on demand."""
        key = name.lower().strip()
        self._lazy_loaders[key] = (module_path, class_name)

    def _discover_entry_points(self) -> None:
        if self._discovered_plugins:
            return
        self._discovered_plugins = True
        for entry in importlib.metadata.entry_points(group="cabrita.providers"):
            name = entry.name.lower().strip()
            if name not in self._factories and name not in self._lazy_loaders:
                self._factories[name] = entry.load()

    def is_registered(self, name: str) -> bool:
        self._discover_entry_points()
        canon = name.lower().strip()
        return canon in self._factories or canon in self._lazy_loaders

    def get(self, name: str, **kwargs: Any) -> NodeProvider:
        """Resolves and instantiates a NodeProvider by name."""
        self._discover_entry_points()
        canon = name.lower().strip()

        if canon in self._factories:
            factory = self._factories[canon]
            return factory(**kwargs)

        if canon in self._lazy_loaders:
            mod_path, cls_name = self._lazy_loaders[canon]
            try:
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, cls_name)
                return cls(**kwargs)
            except ImportError as e:
                raise ProviderNotInstalledError(canon, str(e)) from e

        valid = sorted({*self._factories.keys(), *self._lazy_loaders.keys()})
        raise ValueError(
            f"Unknown provider '{name}'. Registered providers: {', '.join(valid)}."
        )

    def get_provider_class(self, name: str) -> type[NodeProvider]:
        """Resolves the NodeProvider class without instantiating it."""
        self._discover_entry_points()
        canon = name.lower().strip()

        if canon in self._factories:
            factory = self._factories[canon]
            match factory:
                case type() if issubclass(factory, NodeProvider):
                    return factory

        if canon in self._lazy_loaders:
            mod_path, cls_name = self._lazy_loaders[canon]
            try:
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, cls_name)
                match cls:
                    case type() if issubclass(cls, NodeProvider):
                        return cls
            except ImportError as e:
                raise ProviderNotInstalledError(canon, str(e)) from e

        valid = sorted({*self._factories.keys(), *self._lazy_loaders.keys()})
        raise ValueError(
            f"Unknown provider '{name}'. Registered providers: {', '.join(valid)}."
        )

    def list_providers(self) -> list[str]:
        self._discover_entry_points()
        return sorted({*self._factories.keys(), *self._lazy_loaders.keys()})


def create_registry() -> ProviderRegistry:
    """Create an independent registry at the application boundary."""
    registry = ProviderRegistry()
    registry.register_lazy(
        name=ProviderType.LIBVIRT.value,
        module_path="cabrita.providers.libvirt_backend.provider",
        class_name="LibvirtProvider",
    )
    registry.register_lazy(
        name=ProviderType.HELVETIOS.value,
        module_path="cabrita.providers.helvetios.provider",
        class_name="HelvetiosProvider",
    )
    return registry
