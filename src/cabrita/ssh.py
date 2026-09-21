import subprocess
from pathlib import Path


def is_ssh_authenticated(
    target_ip: str,
    username: str,
    bastion_ssh_host: str | None = None,
    key_path: Path | None = None,
    timeout: int = 5,
) -> bool:
    """Verifies successful SSH public-key authentication into target node.

    Uses StrictHostKeyChecking=no and UserKnownHostsFile=/dev/null to avoid
    connection rejections when bare-metal nodes are re-imaged with new host keys.
    """
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        f"ConnectTimeout={timeout}",
    ]
    if key_path is not None and key_path.exists():
        cmd.extend(["-i", str(key_path)])
    if bastion_ssh_host is not None:
        cmd.extend(["-J", bastion_ssh_host])
    cmd.extend([f"{username}@{target_ip}", "true"])
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 5,
            check=False,
        )
        return res.returncode == 0
    except subprocess.TimeoutExpired:
        return False
