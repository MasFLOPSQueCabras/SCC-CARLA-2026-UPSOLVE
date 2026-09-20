import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from scc_carla.config import ClusterSettings
from scc_carla.db import ClusterLock


def normalize_resources(
    targets: list[int] | list[str] | int | str | None = None,
) -> list[str]:
    """Normalizes node integers, resource names, or lists into canonical lock resource names.

    Examples:
        None -> ["cluster"]
        [1, 2, 3] -> ["node-1", "node-2", "node-3"]
        1 -> ["node-1"]
        "node-1" -> ["node-1"]
    """
    match targets:
        case None | []:
            return ["cluster"]
        case int(n):
            return [f"node-{n}"]
        case str(s):
            return [s if s.startswith("node-") or s == "cluster" else f"node-{s}"]
        case list() as items:
            normalized: list[str] = []
            for item in items:
                match item:
                    case int(n):
                        normalized.append(f"node-{n}")
                    case str(s):
                        normalized.append(
                            s
                            if s.startswith("node-") or s == "cluster"
                            else f"node-{s}"
                        )
                    case _:
                        raise ValueError(f"Invalid resource target item: {item}")
            return sorted(set(normalized))
        case invalid:
            raise ValueError(f"Cannot normalize lock resources from: {invalid}")


@contextmanager
def cluster_lock(
    settings: ClusterSettings,
    targets: list[int] | list[str] | int | str | None = None,
    operation: str = "operation",
    force: bool = False,
    timeout_sec: int = 1800,
) -> Generator[ClusterLock]:
    """Context manager for acquiring and releasing cluster operational locks."""
    resources = normalize_resources(targets)
    with ClusterLock(
        settings=settings,
        resources=resources,
        operation=operation,
        force=force,
        timeout_sec=timeout_sec,
    ) as lock:
        yield lock


def wait_for_power_state(
    get_state_fn: Callable[[], Any],
    expected_state: Any,
    timeout_sec: int = 60,
    poll_interval: float = 2.0,
) -> bool:
    """Polls a power state function until the expected state is reached or timeout expires."""
    start_time = time.time()
    expected_str = str(expected_state).upper()
    while time.time() - start_time < timeout_sec:
        state = get_state_fn()
        state_str = str(state.value if hasattr(state, "value") else state).upper()
        if state_str == expected_str:
            return True
        time.sleep(poll_interval)
    return False
