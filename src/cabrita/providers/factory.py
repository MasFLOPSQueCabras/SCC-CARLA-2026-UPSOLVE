from cabrita.config import ClusterSettings
from cabrita.core.di import create_registry
from cabrita.core.providers.base import NodeProvider, ProviderType


def get_provider(
    settings: ClusterSettings,
    provider: ProviderType | str | None = None,
) -> NodeProvider:
    """Construct a provider from the settings resolved by the caller."""
    selected = settings.provider if provider is None else str(provider)
    return create_registry().get(
        selected, settings=settings, manifest=settings.manifest
    )
