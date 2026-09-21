import contextlib
import os
import socket
import subprocess
import time
from collections.abc import Iterator


class LockError(Exception):
    """Raised when an operation cannot proceed due to an active cluster lock."""


@contextlib.contextmanager
def ssh_atomic_lease_lock(
    ssh_host: str,
    lock_name: str,
    timeout_sec: int = 1800,
    force: bool = False,
) -> Iterator[None]:
    """Acquires a lease lock on a remote host via atomic mkdir over SSH."""
    lock_dir = f"~/cabrita_locks/{lock_name}.lock"
    holder_info = f"{socket.gethostname()}:{os.getpid()}:{int(time.time())}"

    # Script: if force or expired, break lock. Then mkdir atomically.
    acquire_cmd = (
        f"mkdir -p ~/cabrita_locks && "
        f"if [ -d {lock_dir} ]; then "
        f"  if [ '{str(force).lower()}' = 'true' ]; then rm -rf {lock_dir}; fi; "
        f"fi && "
        f"if mkdir {lock_dir} 2>/dev/null; then "
        f"  echo '{holder_info}' > {lock_dir}/info; "
        f"  echo 'ACQUIRED'; "
        f"else "
        f"  echo 'BUSY'; "
        f"fi"
    )

    res = subprocess.run(
        ["ssh", ssh_host, f'bash -c "{acquire_cmd}"'],
        capture_output=True,
        text=True,
        check=True,
        timeout=1800,
    )
    status = res.stdout.strip()
    if status != "ACQUIRED":
        raise LockError(
            f"Remote lock '{lock_name}' on {ssh_host} is currently held by another operator. Use --force to override."
        )

    try:
        yield
    finally:
        release_cmd = f"rm -rf {lock_dir}"
        with contextlib.suppress(Exception):
            subprocess.run(
                ["ssh", ssh_host, f'bash -c "{release_cmd}"'],
                capture_output=True,
                check=False,
                timeout=1800,
            )
