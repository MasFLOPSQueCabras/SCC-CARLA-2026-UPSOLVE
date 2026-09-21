import contextlib
import importlib.resources as ir
from collections.abc import Generator
from pathlib import Path
from typing import Any

from scc_core.di import container
from scc_core.providers.base import NodeProvider, ProviderType

PROVIDER_PACKAGE_MAP: dict[str, str] = {
    "vm": "scc_provider_libvirt",
    "libvirt": "scc_provider_libvirt",
    "helvetios": "scc_provider_helvetios",
    "bmc": "scc_provider_helvetios",
}


@contextlib.contextmanager
def get_provider_templates_dir(provider: str) -> Generator[Path | None]:
    """Resolves and yields the template directory for a provider using importlib.resources."""
    pkg_name = PROVIDER_PACKAGE_MAP.get(provider.lower())
    if not pkg_name:
        yield None
        return

    # 1. Standard package resource lookup via importlib.resources
    try:
        ref = ir.files(pkg_name).joinpath("templates")
        with ir.as_file(ref) as p:
            if p.is_dir():
                yield p
                return
    except ModuleNotFoundError, TypeError, FileNotFoundError:
        pass

    yield None


def get_provider(
    settings: Any,
    provider: ProviderType | str | None = None,
) -> NodeProvider:
    """Factory function resolving a NodeProvider from the DI container."""
    raw = provider or getattr(settings, "provider", "libvirt")
    selected_str = str(raw.value if hasattr(raw, "value") else raw)

    return container.providers.get(selected_str, settings=settings)
