from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from cabritactl.core.manifest import ClusterManifest


class ClusterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CABRITA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    manifest: ClusterManifest | None = None

    bastion_ssh_host: str = "cabrita-bastion"
    bastion_hostname: str = "carlanga"
    bastion_serve_dir: str = "~/cabrita_serve"
    bastion_http_ip: str = "10.7.12.101"
    bastion_http_port: int = 8072
    gateway_ip: str = "10.2.72.254"
    dns_ip: str = "10.2.72.254"

    bmc_user: str = ""
    bmc_password: str = ""

    provider: str = "libvirt"
    libvirt_uri: str = "qemu:///system"

    bastion_state_db_path: Path = (
        Path.home() / ".config" / "cabrita" / "cabrita_state.db"
    )
