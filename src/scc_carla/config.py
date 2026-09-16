from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClusterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    team_id: int = 72
    bastion_ssh_host: str = "scc-bastion"
    bastion_hostname: str = "carlanga"
    bastion_http_ip: str = "200.16.29.171"
    bastion_http_port: int = 8072
    gateway_ip: str = "10.2.72.254"
    dns_ip: str = "10.2.72.254"
    node_network: str = "10.2.72.0/24"

    bmc_user: str = ""
    bmc_password: str = ""

    iso_name: str = "ubuntu-26.04.1-live-server-amd64.iso"
    iso_url: str = (
        "https://releases.ubuntu.com/26.04/ubuntu-26.04.1-live-server-amd64.iso"
    )

    db_path: Path = Field(default_factory=lambda: Path.cwd() / "scc_state.db")
    db_auth_token: str = ""
    node_username: str = "scct-2672"

    def get_node_ip(self, node_id: int) -> str:
        return f"10.2.{self.team_id}.{node_id}"

    def get_bmc_ip(self, node_id: int) -> str:
        return f"10.1.{self.team_id}.{node_id}"

    def get_hostname(self, node_id: int) -> str:
        return f"node{node_id}"


@lru_cache
def get_settings() -> ClusterSettings:
    return ClusterSettings()
