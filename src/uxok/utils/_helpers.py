"""Generic utility helpers for validation, sanitization, and async tasks."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Coroutine, Iterable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


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
