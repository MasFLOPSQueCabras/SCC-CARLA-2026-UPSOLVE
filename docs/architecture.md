# Architecture

`ResolvedCluster` loads a manifest, resolves paths relative to it, merges node
defaults, validates media capabilities, and resolves shared HPC settings once.
Targets come from declared nodes. Providers receive explicit settings and
manifests through a lazy registry; help and version do not import optional
provider libraries.

`LifecycleService` owns planning, state transitions, bounded worker execution,
resource locks, retries, and replacement protection. `ProviderBackend` connects
those operations to bootstrap preparation, provider actions, SSH verification,
and Ansible. Installation and configuration have separate checkpoints.

Bootstrap modules own immutable artifact caching, shared ISO construction,
bastion preparation, custom executable contracts, golden capture, and recovery.
Libvirt and Helvetios share bootstrap methods and packaged Ansible roles.
Providers own power, virtual media, and virtual-resource operations.

State lives beneath Cabrita's platform state directory; caches live beneath its
cache directory. Cluster names namespace managed resources. Locks identify the
libvirt URI and resource name, or the physical BMC endpoint. Artifact locks
protect shared immutable cache entries. Golden captures are independent of the
source VM's disk and cluster lifetime.

Resources ship inside the `cabrita` wheel. There is one canonical packaged
Ansible tree and one template tree per provider. User-authored files resolve
from the manifest and can override packaged inputs without editing the install.
