"""Resolve shared HPC configuration independently of the deployment provider."""

from ipaddress import ip_address
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from cabritactl.core.manifest import ClusterManifest, NodeSpec


class HPCSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["tcp", "ucx"] = "tcp"
    ranks_per_node: int = Field(default=1, ge=1)
    hpl_n: int = Field(default=4096, ge=1)
    hpl_nb: int = Field(default=128, ge=1)
    hpl_p: int = Field(default=1, ge=1)
    hpl_q: int | None = Field(default=None, ge=1)
    hpl_binary: str | None = None
    mpi_launcher: str = "/usr/lib64/openmpi/bin/mpirun"
    mpi_library_path: str = "/usr/lib64/openmpi/lib"
    mpi_args: list[str] = Field(default_factory=list)
    omp_threads: int = Field(default=1, ge=1)
    nfs_server: str | None = None
    nfs_export_dir: str = Field(default="/opt/cabrita/shared", pattern=r"^/[\w/.-]+$")
    nfs_mount_dir: str = Field(default="/shared", pattern=r"^/[\w/.-]+$")
    nfs_mount_options: str = "rw,hard,vers=4.2,proto=tcp"
    ib_addresses: dict[str, str] = Field(default_factory=dict)
    ib_interface: str = "ib0"
    ib_prefix: int = Field(default=24, ge=1, le=32)
    ucx_device: str = "mlx5_0:1"
    tuning: bool = False
    tuned_profile: str = "throughput-performance"
    sysctl: dict[str, int] = Field(
        default_factory=lambda: {"vm.swappiness": 10, "kernel.numa_balancing": 0}
    )
    software: Literal["system", "spack"] = "system"
    spack_mirror: str | None = None
    build_jobs: int = Field(default=2, ge=1, le=128)

    @classmethod
    def resolve(cls, manifest: ClusterManifest) -> HPCSettings:
        defaults: dict[str, Any] = {}
        if manifest.configuration.profile == "scc-carla-2026":
            defaults = {"transport": "ucx", "tuning": True, "software": "spack"}
        settings = cls.model_validate(defaults | manifest.configuration.inputs)
        nodes = manifest.nodes
        if not nodes:
            raise ValueError(
                "Shared HPC configuration requires at least one declared node"
            )
        total = len(nodes) * settings.ranks_per_node
        if settings.hpl_q is None:
            settings.hpl_q, remainder = divmod(total, settings.hpl_p)
            if remainder:
                raise ValueError("HPL P must divide the declared MPI rank count")
        if settings.hpl_p * settings.hpl_q != total:
            raise ValueError("HPL P × Q must equal node count × ranks_per_node")
        if settings.hpl_n < settings.hpl_nb:
            raise ValueError("HPL matrix size must be at least the block size")
        if settings.nfs_export_dir == settings.nfs_mount_dir:
            raise ValueError("NFS export and mount paths must differ")
        if settings.nfs_server is None:
            settings.nfs_server = next(
                (node.hostname for node in nodes if node.role == "headnode"),
                nodes[0].hostname,
            )
        names = {node.hostname for node in nodes}
        if settings.nfs_server not in names:
            raise ValueError("NFS server must be a declared node")
        if settings.transport == "ucx":
            if settings.ib_addresses.keys() != names:
                raise ValueError(
                    "UCX requires an ib_addresses entry for every declared node"
                )
            for address in settings.ib_addresses.values():
                if ip_address(address).version != 4:
                    raise ValueError(
                        "The shared HPC profile requires IPv4 IB addresses"
                    )
        if settings.software == "spack":
            if "mpi_launcher" not in settings.model_fields_set:
                settings.mpi_launcher = (
                    settings.nfs_mount_dir + "/environment/view/bin/mpirun"
                )
            if "mpi_library_path" not in settings.model_fields_set:
                settings.mpi_library_path = (
                    settings.nfs_mount_dir + "/environment/view/lib"
                )
        return settings

    def variables(self, manifest: ClusterManifest) -> dict[str, Any]:
        assert self.nfs_server is not None
        addresses = {node.hostname: self.address(node) for node in manifest.nodes}
        return self.model_dump() | {
            "cluster_user": manifest.defaults.os.username,
            "cluster_user_home": f"/home/{manifest.defaults.os.username}",
            "nfs_server_host": self.nfs_server,
            "nfs_server_address": addresses[self.nfs_server],
            "cluster_addresses": addresses,
            "cluster_subnet": manifest.network.subnet
            if self.transport == "tcp"
            else manifest.network.infiniband_subnet,
            "hpl_ranks": len(manifest.nodes) * self.ranks_per_node,
        }

    def address(self, node: NodeSpec) -> str:
        return self.ib_addresses[node.hostname] if self.transport == "ucx" else node.ip
