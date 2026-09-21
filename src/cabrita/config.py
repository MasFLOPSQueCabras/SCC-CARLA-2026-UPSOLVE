from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from cabrita.core.manifest import ClusterManifest


class ClusterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CABRITA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    manifest: ClusterManifest | None = None

    team_id: int = 72
    bastion_ssh_host: str = "cabrita-bastion"
    bastion_hostname: str = "carlanga"
    bastion_serve_dir: str = "~/cabrita_serve"
    bastion_http_ip: str = "10.7.12.101"
    bastion_http_port: int = 8072
    gateway_ip: str = "10.2.72.254"
    dns_ip: str = "10.2.72.254"
    node_network: str = "10.2.72.0/24"

    bmc_user: str = ""
    bmc_password: str = ""

    provider: str = "libvirt"
    libvirt_uri: str = "qemu:///system"
    libvirt_pool: str = "default"
    libvirt_network: str = "default"
    libvirt_domain_prefix: str = "cabrita-"

    iso_name: str = "Rocky-10.2-x86_64-minimal.iso"
    iso_source: str = "https://download.rockylinux.org/pub/rocky/10/isos/x86_64/Rocky-10.2-x86_64-minimal.iso"
    iso_url: str = "https://download.rockylinux.org/pub/rocky/10/isos/x86_64/Rocky-10.2-x86_64-minimal.iso"

    cloud_image_name: str = "Rocky-10-GenericCloud-Base.latest.x86_64.qcow2"
    cloud_image_source: str = "https://download.rockylinux.org/pub/rocky/10/images/x86_64/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2"

    bastion_state_db_path: Path = (
        Path.home() / ".config" / "cabrita" / "cabrita_state.db"
    )
    node_username: str = "scct-2672"

    def get_node_ip(self, node_id: int) -> str:
        if self.manifest is not None:
            for node in self.manifest.nodes:
                if node.id == node_id:
                    return node.ip
            raise ValueError(f"Undeclared node: {node_id}")
        return f"10.2.{self.team_id}.{node_id}"

    def get_bmc_ip(self, node_id: int) -> str:
        return f"10.1.{self.team_id}.{node_id}"

    def get_hostname(self, node_id: int) -> str:
        if self.manifest is not None:
            for node in self.manifest.nodes:
                if node.id == node_id:
                    return node.hostname
            raise ValueError(f"Undeclared node: {node_id}")
        return f"node{node_id}"


@lru_cache
def get_settings() -> ClusterSettings:
    return ClusterSettings()
