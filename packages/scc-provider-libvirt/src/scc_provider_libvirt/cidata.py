import subprocess
from pathlib import Path

from scc_core.templating import TemplateEngine


def generate_cidata(
    user_data_path: Path,
    meta_data_path: Path,
    network_config_path: Path,
    output_path: Path,
    template_engine: TemplateEngine,
    size_mb: int = 4,
) -> Path:
    """Generates a CIDATA FAT image containing cloud-init configs using a templated bash script."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script_path = output_path.parent / f"make_{output_path.stem}.sh"

    context = {
        "output_img": str(output_path.resolve()),
        "user_data_path": str(user_data_path.resolve()),
        "meta_data_path": str(meta_data_path.resolve()),
        "network_config_path": str(network_config_path.resolve()),
        "size_mb": size_mb,
    }
    template_engine.render_to_file(
        "scripts/generate_cidata.sh.j2", context, script_path
    )
    script_path.chmod(0o755)

    subprocess.run(["bash", str(script_path)], check=True)
    try:
        output_path.chmod(0o666)
    except OSError:
        pass
    return output_path
