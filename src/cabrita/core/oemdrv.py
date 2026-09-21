import subprocess
from pathlib import Path

from cabrita.core.templating import TemplateEngine


def generate_oemdrv(
    ks_cfg_path: Path,
    output_path: Path,
    template_engine: TemplateEngine,
    size_mb: int = 4,
) -> Path:
    """Generates an OEMDRV FAT image containing the kickstart file using a templated bash script."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script_path = output_path.parent / f"make_{output_path.stem}.sh"

    context = {
        "output_img": str(output_path.resolve()),
        "ks_cfg_path": str(ks_cfg_path.resolve()),
        "size_mb": size_mb,
    }
    template_engine.render_to_file(
        "scripts/generate_oemdrv.sh.j2", context, script_path
    )
    script_path.chmod(0o755)

    subprocess.run(["bash", str(script_path)], check=True)
    try:
        output_path.chmod(0o666)
    except OSError:
        pass
    return output_path
