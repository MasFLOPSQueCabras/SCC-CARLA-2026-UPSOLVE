from cabritactl.config import ClusterSettings
from cabritactl.core.di import create_registry
from cabritactl.core.providers.base import NodeProvider, ProviderType


def get_provider(
    settings: ClusterSettings,
    provider: ProviderType | str | None = None,
) -> NodeProvider:
    """Construct a provider from the settings resolved by the caller."""
    selected = settings.provider if provider is None else str(provider)
    return create_registry().get(
        selected, settings=settings, manifest=settings.manifest
    )
