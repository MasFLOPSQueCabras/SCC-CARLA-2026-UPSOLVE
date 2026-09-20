from pathlib import Path
from typing import Any

import jinja2


class TemplateEngine:
    """Jinja2 template engine supporting multi-directory search paths."""

    def __init__(self, search_paths: list[Path | str] | None = None) -> None:
        default_paths: list[Path] = [Path.cwd() / "templates"]
        if search_paths:
            resolved_paths = [Path(p).expanduser().resolve() for p in search_paths]
            for p in default_paths:
                if p not in resolved_paths:
                    resolved_paths.append(p)
        else:
            resolved_paths = default_paths

        # Filter to existing directories or create if missing
        loaders = [
            jinja2.FileSystemLoader(str(p)) for p in resolved_paths if p.is_dir()
        ]
        # Always add a fallback loader for relative paths
        if not loaders:
            loaders = [jinja2.FileSystemLoader(str(Path.cwd()))]

        self.env = jinja2.Environment(
            loader=jinja2.ChoiceLoader(loaders),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=jinja2.StrictUndefined,
        )

    def add_search_path(self, path: Path | str) -> None:
        p = Path(path).expanduser().resolve()
        if p.is_dir():
            current_loaders = (
                self.env.loader.loaders
                if isinstance(self.env.loader, jinja2.ChoiceLoader)
                else [self.env.loader]
            )
            new_loader = jinja2.FileSystemLoader(str(p))
            valid_loaders = [l for l in current_loaders if l is not None]
            self.env.loader = jinja2.ChoiceLoader([new_loader, *valid_loaders])

    def render(self, template_name: str, context: dict[str, Any]) -> str:
        template = self.env.get_template(template_name)
        return template.render(**context)

    def render_string(self, source: str, context: dict[str, Any]) -> str:
        template = self.env.from_string(source)
        return template.render(**context)

    def render_to_file(
        self, template_name: str, context: dict[str, Any], output_path: Path | str
    ) -> Path:
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        content = self.render(template_name, context)
        out.write_text(content, encoding="utf-8")
        return out
