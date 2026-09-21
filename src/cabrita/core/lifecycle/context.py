from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from cabrita.core.manifest.models import ClusterManifest, NodeSpec
from cabrita.core.templating import TemplateEngine


@dataclass
class ClusterContext:
    manifest: ClusterManifest
    staging_dir: Path
    template_engine: TemplateEngine
    console: Console = field(default_factory=Console)
    extra_vars: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeContext:
    cluster: ClusterContext
    node: NodeSpec
    task_id: Any | None = None
    extra_vars: dict[str, Any] = field(default_factory=dict)

    @property
    def node_id(self) -> int:
        return self.node.id

    @property
    def hostname(self) -> str:
        return self.node.hostname

    @property
    def ip(self) -> str:
        return self.node.ip
