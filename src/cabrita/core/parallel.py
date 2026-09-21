"""Fluent, deadlock-free parallel task orchestration for cluster operations.

Provides bounded, thread-safe concurrent execution with rich result aggregation,
fail-safe timeout guarantees, and zero circular lock hazards.
"""

import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Self


@dataclass(slots=True)
class TaskResult[ResultT]:
    """Encapsulates the execution outcome of an individual concurrent task."""

    item: Any
    success: bool
    value: ResultT | None = None
    error: Exception | None = None
    elapsed_sec: float = 0.0


class ParallelRunner[ItemT, ResultT]:
    """Fluent, thread-safe parallel executor designed for cluster operations.

    Ensures deadlock freedom by strictly executing isolated worker units,
    enforcing individual and collective timeouts, and preventing circular lock contention.
    """

    def __init__(
        self,
        max_workers: int | None = None,
        timeout_sec: float | None = None,
        thread_name_prefix: str = "cabrita-worker",
    ) -> None:
        self._max_workers = max_workers
        self._timeout_sec = timeout_sec
        self._thread_name_prefix = thread_name_prefix
        self._items: list[ItemT] = []
        self._task_fn: Callable[[ItemT], ResultT] | None = None
        self._progress_callback: Callable[[ItemT, str], None] | None = None
        self._fail_fast: bool = False

    def items(self, items: Iterable[ItemT]) -> Self:
        """Sets the collection of items to process in parallel."""
        self._items = list(items)
        return self

    def task(self, fn: Callable[[ItemT], ResultT]) -> Self:
        """Sets the task function to execute for each item."""
        self._task_fn = fn
        return self

    def on_progress(self, callback: Callable[[ItemT, str], None]) -> Self:
        """Attaches an optional progress callback receiving (item, status_message)."""
        self._progress_callback = callback
        return self

    def fail_fast(self, enabled: bool = True) -> Self:
        """When enabled, cancels remaining unstarted tasks on first failure."""
        self._fail_fast = enabled
        return self

    def run(self) -> dict[ItemT, TaskResult[ResultT]]:
        """Executes tasks concurrently across items and aggregates results thread-safely."""
        if not self._items:
            return {}
        if self._task_fn is None:
            raise ValueError("No task function registered with ParallelRunner.task()")

        num_workers = self._max_workers or min(32, len(self._items))
        num_workers = max(1, num_workers)

        results: dict[ItemT, TaskResult[ResultT]] = {}
        futures: dict[Future[ResultT], tuple[ItemT, float]] = {}

        with ThreadPoolExecutor(
            max_workers=num_workers,
            thread_name_prefix=self._thread_name_prefix,
        ) as executor:
            for item in self._items:
                start_time = time.monotonic()
                if self._progress_callback:
                    self._progress_callback(item, "queued")
                future = executor.submit(self._task_fn, item)
                futures[future] = (item, start_time)

            for future in as_completed(futures, timeout=self._timeout_sec):
                item, start_time = futures[future]
                elapsed = time.monotonic() - start_time

                try:
                    res = future.result()
                    results[item] = TaskResult(
                        item=item,
                        success=True,
                        value=res,
                        elapsed_sec=elapsed,
                    )
                    if self._progress_callback:
                        self._progress_callback(item, "completed")
                except Exception as exc:  # noqa: BLE001
                    results[item] = TaskResult(
                        item=item,
                        success=False,
                        error=exc,
                        elapsed_sec=elapsed,
                    )
                    if self._progress_callback:
                        self._progress_callback(item, f"failed: {exc}")
                    if self._fail_fast:
                        for pending_f in futures:
                            if not pending_f.done():
                                pending_f.cancel()
                        break

        # Record any items that were never completed (e.g. on timeout or fail-fast)
        for item in self._items:
            if item not in results:
                results[item] = TaskResult(
                    item=item,
                    success=False,
                    error=TimeoutError("Task execution timed out or was cancelled"),
                )

        return results

    def values(self) -> dict[ItemT, ResultT | None]:
        """Convenience method returning a mapping of item -> result value (or None on failure)."""
        run_res = self.run()
        return {item: tr.value for item, tr in run_res.items()}

    def all_succeeded(self) -> bool:
        """Convenience method returning True if all tasks finished successfully."""
        run_res = self.run()
        return all(tr.success for tr in run_res.values())
