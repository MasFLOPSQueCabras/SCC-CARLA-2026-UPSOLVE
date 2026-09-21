"""Build cloud-init seed media suitable for a virtual optical drive."""

import subprocess
from pathlib import Path


def generate_cidata(
    user_data_path: Path,
    meta_data_path: Path,
    network_config_path: Path,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".partial.iso")
    subprocess.run(
        [
            "xorriso",
            "-as",
            "mkisofs",
            "-quiet",
            "-V",
            "CIDATA",
            "-J",
            "-r",
            "-o",
            str(temporary),
            "-graft-points",
            f"user-data={user_data_path}",
            f"meta-data={meta_data_path}",
            f"network-config={network_config_path}",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    temporary.chmod(0o644)
    temporary.replace(output_path)
    return output_path
