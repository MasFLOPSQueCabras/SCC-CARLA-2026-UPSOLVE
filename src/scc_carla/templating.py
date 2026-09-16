from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined


class TemplateEngine:
    def __init__(self, templates_dir: Path | None = None) -> None:
        self.templates_dir = (
            templates_dir if templates_dir is not None else (Path.cwd() / "templates")
        )
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, template_name: str, context: dict[str, Any]) -> str:
        template = self.env.get_template(template_name)
        return template.render(context)

    def render_to_file(
        self, template_name: str, context: dict[str, Any], output_path: Path
    ) -> Path:
        content = self.render(template_name, context)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return output_path
