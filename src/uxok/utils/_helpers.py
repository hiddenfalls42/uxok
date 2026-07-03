"""Generic utility helpers for validation, sanitization, locking, and async tasks."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import AsyncIterator, Coroutine, Hashable, Iterable, Mapping
from collections.abc import Set as AbstractSet
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@asynccontextmanager
async def locked(lock: asyncio.Lock | asyncio.Semaphore) -> AsyncIterator[None]:
    """Async context manager that acquires and releases the given lock."""
    await lock.acquire()
    try:
        yield
    finally:
        with suppress(Exception):
            lock.release()


def validate_identifier(value: str, field_name: str) -> str:
    """Validate an identifier-like string and return the sanitized value."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{field_name} cannot be empty")
    if not re.match(r"^[A-Za-z0-9_.-]+$", trimmed):
        raise ValueError(
            f"{field_name} must contain only letters, numbers, _, ., or -: got '{value}'"
        )
    return trimmed


def validate_positive_number(value: int | float, field_name: str) -> float:
    """Ensure a number is positive and finite."""
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError(f"{field_name} must be finite")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return float(value)


def validate_enum_value(value: str, valid_values: Iterable[str], field_name: str) -> str:
    """Validate that a value is within an allowed set."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    values = set(valid_values)
    if value not in values:
        raise ValueError(f"{field_name} must be one of {sorted(values)}: got '{value}'")
    return value


def sanitize_identifier(value: str, field_name: str) -> str:
    """Sanitize a user-provided identifier by trimming and normalizing."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    trimmed = value.strip()
    # Replace any disallowed character with underscore
    sanitized = re.sub(r"[^A-Za-z0-9_.-]", "_", trimmed)
    if not sanitized:
        raise ValueError(f"{field_name} cannot be empty after sanitization")
    return sanitized


def camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case (handles acronyms correctly).

    Examples:
        >>> camel_to_snake("Greeting")
        'greeting'
        >>> camel_to_snake("FileStorage")
        'file_storage'
        >>> camel_to_snake("XMLParser")
        'xml_parser'
    """
    # Step 1: Insert underscore before uppercase after lowercase/digit
    s1 = re.sub("([a-z0-9])([A-Z])", r"\1_\2", name)
    # Step 2: Insert underscore before last capital in acronym sequences
    s2 = re.sub("([A-Z]+)([A-Z][a-z])", r"\1_\2", s1)
    return s2.lower()


def safe_str(value: Any) -> str:
    """Best-effort string conversion that never raises."""
    try:
        return str(value)
    except Exception:
        return "<unprintable>"


def log_context(**kwargs: Any) -> dict[str, Any]:
    """Create a shallow copy of provided log context fields."""
    return dict(kwargs)


def topo_sort[T: Hashable](
    nodes: Iterable[T],
    dependencies: Mapping[T, AbstractSet[T]],
) -> tuple[list[T], set[T]]:
    """Kahn topological sort over an explicit node set.

    Args:
        nodes: The nodes to order.
        dependencies: Maps a node to the nodes it depends on. Dependencies that
            fall outside `nodes` are ignored, so callers may pass a mapping
            with a wider domain than the node set being sorted.

    Returns:
        A `(ordered, unresolved)` tuple. `ordered` is a valid topological order
        of `nodes` (dependencies before dependents). `unresolved` is the set of
        nodes that could not be placed because they participate in, or depend
        on, a cycle; it is nonempty exactly when `nodes` contains a cycle. This
        function never raises on a cycle — the caller decides how to report it.
    """
    node_set = set(nodes)
    in_degree = dict.fromkeys(node_set, 0)
    graph: dict[T, set[T]] = {node: set() for node in node_set}

    for node in node_set:
        deps = dependencies.get(node, set()) & node_set
        for dep in deps:
            graph[dep].add(node)
            in_degree[node] += 1

    ordered: list[T] = []
    queue = [node for node, degree in in_degree.items() if degree == 0]

    while queue:
        current = queue.pop(0)
        ordered.append(current)
        for dependent in graph[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    unresolved = node_set - set(ordered)
    return ordered, unresolved


class AsyncTaskManager:
    """Lightweight async task tracker for create/cancel/cleanup patterns."""

    def __init__(self, error_reporter: Callable[[asyncio.Task[Any]], None] | None = None) -> None:
        """Args:
        error_reporter: Called with a task that finished with an exception
            (not cancellation). Plugin wires this to emit core.plugin_error;
            without a reporter, failures are logged only.
        """
        self._tasks: set[asyncio.Task[Any]] = set()
        self._error_reporter = error_reporter

    async def create_task(
        self, coro: Coroutine[Any, Any, Any], name: str | None = None
    ) -> asyncio.Task[Any]:
        """Create and track a background task."""
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        if self._error_reporter is not None:
            with suppress(Exception):
                self._error_reporter(task)
                return
        # No reporter (or reporter failed): at least leave a trace.
        try:
            raise exc
        except Exception:
            logging.getLogger(__name__).exception("Background task %r failed", task.get_name())

    async def cancel_all(self, timeout: float = 5.0) -> None:  # noqa: ASYNC109 — bounded cleanup API
        """Cancel all tracked tasks and await completion."""
        if not self._tasks:
            return
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        await self._await_all(timeout=timeout)

    async def cleanup_task(self, task: asyncio.Task[Any]) -> None:
        """Await a single task safely and remove it from tracking."""
        with suppress(asyncio.CancelledError):
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._tasks.discard(task)

    async def _await_all(self, timeout: float) -> None:  # noqa: ASYNC109 — bounded cleanup API
        """Internal helper to await all tracked tasks."""
        tasks_snapshot = list(self._tasks)
        if not tasks_snapshot:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks_snapshot, return_exceptions=True), timeout=timeout
            )
        except TimeoutError:
            for task in tasks_snapshot:
                if not task.done():
                    task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks_snapshot, return_exceptions=True)
        finally:
            for task in tasks_snapshot:
                self._tasks.discard(task)
