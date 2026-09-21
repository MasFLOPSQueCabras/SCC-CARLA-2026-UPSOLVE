import os
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class OSSpec(BaseModel):
    iso_name: str = "Rocky-10.2-x86_64-minimal.iso"
    iso_source: str = "https://download.rockylinux.org/pub/rocky/10/isos/x86_64/Rocky-10.2-x86_64-minimal.iso"
    cloud_image: str = "Rocky-10-GenericCloud-Base.latest.x86_64.qcow2"
    cloud_image_source: str = "https://download.rockylinux.org/pub/rocky/10/images/x86_64/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2"
    username: str = "scct-2672"


class DiskSpec(BaseModel):
    size_gb: int = 40
    bus: Literal["virtio", "scsi", "sata"] = "virtio"
    controller: Literal["virtio-scsi", "default"] = "default"
    queues: int = 2
    io: Literal["io_uring", "threads", "native"] = "threads"
    cache: Literal["writeback", "none", "writethrough"] = "writeback"
    discard: Literal["unmap", "ignore"] = "unmap"


class VMSpec(BaseModel):
    vcpus: int = 4
    memory_mb: int = 8192
    disk: DiskSpec = Field(default_factory=DiskSpec)
    machine_type: str = "q35"
    firmware: Literal["efi", "bios"] = "bios"
    cpu_mode: Literal["host-passthrough", "host-model", "qemu64"] = "host-model"
    iothreads: int | None = None
    graphics: Literal["spice", "none"] = "spice"
    uefi: bool = False

    @field_validator("disk", mode="before")
    @classmethod
    def _coerce_disk(cls, v: Any) -> Any:
        match v:
            case int():
                return {"size_gb": v}
            case _:
                return v


class BMCSpec(BaseModel):
    ip: str
    user: str = ""
    password: str = ""
    port: int = 443

    @field_validator("user", "password", mode="before")
    @classmethod
    def _interpolate_env(cls, v: Any) -> Any:
        match v:
            case str() if v.startswith("${") and v.endswith("}"):
                return os.environ.get(v[2:-1], "")
            case _:
                return v


class HardwareSpec(BaseModel):
    bios_profile: str = "hpc"
    target_disk: str = "/dev/sda"
    interface_name: str = "eno1"
    infiniband_interface: str = "ib0"


class NetworkSpec(BaseModel):
    bridge: str = "virbr0"
    network_name: str = "default"
    subnet: str = "192.168.122.0/24"
    gateway: str = "192.168.122.1"
    dns: str = "192.168.122.1"
    domain: str = "cabrita.local"
    infiniband_subnet: str = "10.148.0.0/16"
    model: str = "virtio"
    vhost: bool = True
    queues: int = 2


class BastionSpec(BaseModel):
    ssh_host: str = "cabrita-bastion"
    ssh_user: str = "scct-2672"
    remote_serve_dir: str = "~/cabrita_serve"
    http_bind_ip: str = "10.7.12.101"
    http_port: int = 8072
    proxy_mode: Literal["socks5", "direct"] = "socks5"


class ClusterDefaults(BaseModel):
    os: OSSpec = Field(default_factory=OSSpec)
    vm: VMSpec = Field(default_factory=VMSpec)
    hardware: HardwareSpec = Field(default_factory=HardwareSpec)


class NodeSpec(BaseModel):
    id: int
    hostname: str
    role: Literal["headnode", "computenode", "storage", "worker"] = "computenode"
    ip: str
    mac: str
    vm: VMSpec | None = None
    bmc: BMCSpec | None = None
    hardware: HardwareSpec | None = None


class ClusterManifest(BaseModel):
    schema_version: int = 1
    name: str
    provider: Literal["libvirt", "helvetios", "bmc", "chameleon"] = "libvirt"
    description: str = ""
    network: NetworkSpec = Field(default_factory=NetworkSpec)
    bastion: BastionSpec = Field(default_factory=BastionSpec)
    defaults: ClusterDefaults = Field(default_factory=ClusterDefaults)
    nodes: list[NodeSpec] = Field(default_factory=list)

    @field_validator("nodes")
    @classmethod
    def _validate_unique_nodes(cls, nodes: list[NodeSpec]) -> list[NodeSpec]:
        seen_ids = set()
        seen_hosts = set()
        seen_ips = set()
        for node in nodes:
            if node.id in seen_ids:
                raise ValueError(f"Duplicate node id: {node.id}")
            seen_ids.add(node.id)
            if node.hostname in seen_hosts:
                raise ValueError(f"Duplicate hostname: {node.hostname}")
            seen_hosts.add(node.hostname)
            if node.ip in seen_ips:
                raise ValueError(f"Duplicate IP: {node.ip}")
            seen_ips.add(node.ip)
        return nodes
