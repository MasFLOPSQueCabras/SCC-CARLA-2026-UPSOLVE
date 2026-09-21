from collections import defaultdict
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from cabrita.core.lifecycle.context import ClusterContext, NodeContext


class HookType(StrEnum):
    PRE_DEPLOY = "pre_deploy"
    POST_DEPLOY = "post_deploy"
    PRE_BOOT_WAIT = "pre_boot_wait"
    POST_BOOT_WAIT = "post_boot_wait"
    PRE_CONFIGURE = "pre_configure"
    POST_CONFIGURE = "post_configure"
    PRE_TEARDOWN = "pre_teardown"
    POST_TEARDOWN = "post_teardown"


HookCallback = Callable[[NodeContext | ClusterContext], Any]


class HookManager:
    """Manages lifecycle hook registration and execution across cluster phases."""

    def __init__(self) -> None:
        self._hooks: dict[HookType, list[HookCallback]] = defaultdict(list)

    def register(self, hook_type: HookType, callback: HookCallback) -> None:
        self._hooks[hook_type].append(callback)

    def run_hooks(
        self, hook_type: HookType, ctx: NodeContext | ClusterContext
    ) -> list[Any]:
        results = []
        for cb in self._hooks.get(hook_type, []):
            res = cb(ctx)
            results.append(res)
        return results
