from __future__ import annotations

import contextlib
import importlib.resources as ir
from collections.abc import Generator
from pathlib import Path

from scc_carla.config import ClusterSettings
from scc_carla.providers.base import NodeProvider, ProviderType

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

    # 2. Development source tree fallback
    repo_root = Path(__file__).parents[3]
    dev_path = (
        repo_root
        / "packages"
        / pkg_name.replace("_", "-")
        / "src"
        / pkg_name
        / "templates"
    )
    if dev_path.is_dir():
        yield dev_path
    else:
        yield None


def get_provider(
    settings: ClusterSettings,
    provider: ProviderType | str | None = None,
) -> NodeProvider:
    """Factory function returning a configured NodeProvider instance using pattern matching."""
    selected = provider or settings.provider

    match selected:
        case ProviderType.LIBVIRT | "libvirt" | "vm":
            from scc_carla.providers.libvirt import LibvirtProvider

            return LibvirtProvider(settings)

        case ProviderType.BMC | "bmc" | "helvetios":
            from scc_carla.providers.bmc import BMCProvider

            return BMCProvider(settings)

        case ProviderType.CHAMELEON | "chameleon":
            from scc_carla.providers.chameleon import ChameleonProvider

            return ChameleonProvider(settings)

        case invalid:
            raise ValueError(
                f"Unknown provider '{invalid}'. Valid providers: 'libvirt', 'bmc', 'chameleon'."
            )
