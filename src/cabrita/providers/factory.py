import contextlib
import importlib.resources as ir
from collections.abc import Generator
from pathlib import Path

from cabrita.config import ClusterSettings
from cabrita.core.di import create_registry
from cabrita.core.providers.base import NodeProvider, ProviderType

PROVIDER_PACKAGE_MAP: dict[str, str] = {
    "vm": "cabrita.providers.libvirt_backend",
    "libvirt": "cabrita.providers.libvirt_backend",
    "helvetios": "cabrita.providers.helvetios",
    "bmc": "cabrita.providers.helvetios",
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
    settings: ClusterSettings,
    provider: ProviderType | str | None = None,
) -> NodeProvider:
    """Construct a provider from the settings resolved by the caller."""
    selected = settings.provider if provider is None else str(provider)
    return create_registry().get(
        selected, settings=settings, manifest=settings.manifest
    )
