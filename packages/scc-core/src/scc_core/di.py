"""Lightweight Dependency Injection (DI) and Lazy Provider Registry."""

import importlib
import importlib.metadata
import inspect
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from scc_core.providers.base import NodeProvider, ProviderType

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ProviderNotInstalledError(RuntimeError):
    """Raised when a requested provider is not installed or missing dependencies."""

    def __init__(self, provider: str, reason: str | None = None) -> None:
        canonical = provider.lower()
        hint = (
            f"uv sync --extra {canonical}"
            if canonical in ("libvirt", "helvetios")
            else "check package dependencies"
        )
        msg = (
            f"Provider '{provider}' is not available or its dependencies are missing.\n"
            f"Details: {reason or 'Package not installed'}\n"
            f"Hint: Run '{hint}' or 'scc init --provider {canonical} --install-deps' to install."
        )
        super().__init__(msg)
        self.provider = provider
        self.reason = reason


class ProviderRegistry:
    """Registry managing NodeProvider implementations with lazy loading and plugin discovery."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., NodeProvider]] = {}
        self._aliases: dict[str, str] = {}
        self._lazy_loaders: dict[str, tuple[str, str]] = {}
        self._discovered_plugins: bool = False

    def register(
        self,
        name: str,
        factory: Callable[..., NodeProvider],
        aliases: list[str] | None = None,
    ) -> None:
        """Register a provider factory directly."""
        key = name.lower().strip()
        self._factories[key] = factory
        if aliases:
            for a in aliases:
                self._aliases[a.lower().strip()] = key

    def register_lazy(
        self,
        name: str,
        module_path: str,
        class_name: str,
        aliases: list[str] | None = None,
    ) -> None:
        """Register a provider by module path and class name to be loaded on demand."""
        key = name.lower().strip()
        self._lazy_loaders[key] = (module_path, class_name)
        if aliases:
            for a in aliases:
                self._aliases[a.lower().strip()] = key

    def _discover_entry_points(self) -> None:
        if self._discovered_plugins:
            return
        self._discovered_plugins = True
        try:
            eps = importlib.metadata.entry_points(group="scc.providers")
            for ep in eps:
                name = ep.name.lower().strip()
                if name not in self._factories and name not in self._lazy_loaders:
                    self._factories[name] = ep.load()
        except (ImportError, AttributeError, KeyError) as exc:
            logger.debug("Provider entry point discovery skipped: %s", exc)

    def canonical_name(self, name: str) -> str:
        k = name.lower().strip()
        return self._aliases.get(k, k)

    def is_registered(self, name: str) -> bool:
        self._discover_entry_points()
        canon = self.canonical_name(name)
        return canon in self._factories or canon in self._lazy_loaders

    def get(self, name: str, **kwargs: Any) -> NodeProvider:
        """Resolves and instantiates a NodeProvider by name."""
        self._discover_entry_points()
        canon = self.canonical_name(name)

        if canon in self._factories:
            factory = self._factories[canon]
            return self._invoke_factory(factory, **kwargs)

        if canon in self._lazy_loaders:
            mod_path, cls_name = self._lazy_loaders[canon]
            try:
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, cls_name)
                return self._invoke_factory(cls, **kwargs)
            except (ImportError, ModuleNotFoundError) as e:
                raise ProviderNotInstalledError(canon, str(e)) from e

        valid = sorted({*self._factories.keys(), *self._lazy_loaders.keys()})
        raise ValueError(
            f"Unknown provider '{name}'. Registered providers: {', '.join(valid)}."
        )

    def _invoke_factory(self, factory: Any, **kwargs: Any) -> NodeProvider:
        sig = inspect.signature(factory)
        accepted = {}
        for param in sig.parameters.values():
            if param.name in kwargs:
                accepted[param.name] = kwargs[param.name]
            elif param.kind == inspect.Parameter.VAR_KEYWORD:
                accepted = kwargs
                break
        return factory(**accepted)

    def list_providers(self) -> list[str]:
        self._discover_entry_points()
        return sorted({*self._factories.keys(), *self._lazy_loaders.keys()})


class Container:
    """Lightweight Dependency Injection Container."""

    def __init__(self) -> None:
        self._services: dict[Any, Any] = {}
        self.providers = ProviderRegistry()

    def register(self, key: Any, value_or_factory: Any) -> None:
        """Registers a service singleton or factory."""
        self._services[key] = value_or_factory

    def resolve(self, key: type[T] | Any, default: Any = None) -> T:
        """Resolves a service from the container."""
        if key in self._services:
            val = self._services[key]
            if callable(val) and not isinstance(val, type):
                return val(self)
            return val
        if default is not None:
            return default
        raise KeyError(f"Service '{key}' not registered in container.")

    def has(self, key: Any) -> bool:
        return key in self._services


# Global default container instance
container = Container()

# Pre-register default lazy providers
container.providers.register_lazy(
    name=ProviderType.LIBVIRT.value,
    module_path="scc_provider_libvirt.provider",
    class_name="LibvirtProvider",
    aliases=["vm"],
)
container.providers.register_lazy(
    name=ProviderType.HELVETIOS.value,
    module_path="scc_provider_helvetios.provider",
    class_name="HelvetiosProvider",
    aliases=["bmc"],
)
container.providers.register_lazy(
    name=ProviderType.CHAMELEON.value,
    module_path="scc_carla.providers.chameleon",
    class_name="ChameleonProvider",
    aliases=["chi"],
)
