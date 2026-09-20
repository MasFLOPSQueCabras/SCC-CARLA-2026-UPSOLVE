from scc_carla.config import ClusterSettings
from scc_carla.providers.base import NodeProvider, ProviderType


def get_provider(
    settings: ClusterSettings,
    provider: ProviderType | str | None = None,
) -> NodeProvider:
    """Factory function returning a configured NodeProvider instance using pattern matching."""
    selected = provider or settings.provider

    match selected:
        case ProviderType.LIBVIRT | "libvirt":
            from scc_carla.providers.libvirt import LibvirtProvider

            return LibvirtProvider(settings)

        case ProviderType.BMC | "bmc":
            from scc_carla.providers.bmc import BMCProvider

            return BMCProvider(settings)

        case ProviderType.CHAMELEON | "chameleon":
            from scc_carla.providers.chameleon import ChameleonProvider

            return ChameleonProvider(settings)

        case invalid:
            raise ValueError(
                f"Unknown provider '{invalid}'. Valid providers: 'libvirt', 'bmc', 'chameleon'."
            )
