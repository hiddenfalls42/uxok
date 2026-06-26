"""
Test helper functions for uxok framework testing.

This module provides factory functions, canonical plugin stubs, and async
utilities shared across the test suite to reduce code duplication.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from uxok import Plugin
from uxok.protocols import Event


def attach_core(plugin: Plugin, core: Any) -> Plugin:
    """Attach a core to a plugin for unit tests that exercise convenience
    methods (emit/hook/config/tick) without going through full registration.

    Coreless construction (RFC 0001 §3.2.3) means the kernel attaches the core
    at register time; a unit test that calls these methods on an unregistered
    instance must attach the core itself. Returns the plugin for chaining.
    """
    plugin._attach_core(core)
    return plugin


async def wait_until(
    predicate: Callable[[], bool | Awaitable[bool]],
    timeout: float = 1.0,
    interval: float = 0.005,
) -> None:
    """Poll a predicate until it is truthy, instead of sleeping a fixed time.

    Args:
        predicate: Sync or async zero-arg callable; truthy result ends the wait.
        timeout: Seconds before giving up.
        interval: Seconds between polls.

    Raises:
        TimeoutError: If the predicate is still falsy after ``timeout`` seconds.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        if loop.time() >= deadline:
            raise TimeoutError(f"wait_until timed out after {timeout}s: {predicate!r}")
        await asyncio.sleep(interval)


class StubPlugin(Plugin):
    """Minimal no-op plugin for tests that just need a registrable plugin."""

    def __init__(self, name: str | None = None, **kwargs: Any) -> None:
        super().__init__(name=name or f"stub_{uuid4().hex[:8]}", **kwargs)

    async def on_start(self) -> None:
        pass

    async def on_stop(self) -> None:
        pass


class EventCollectingPlugin(Plugin):
    """Plugin that collects events (and hook executions) for verification.

    Pass ``subscribe_to`` to auto-subscribe ``_collect_event`` on start
    (e.g. ``"*"`` for all events); leave it None when the test subscribes
    manually.
    """

    def __init__(self, name: str | None = None, subscribe_to: str | None = None) -> None:
        super().__init__(name=name)
        self.events_received: list[Event] = []
        self.hooks_executed: list[dict[str, Any]] = []
        self._subscribe_to = subscribe_to

    async def on_start(self) -> None:
        if self._subscribe_to is not None:
            await self.core.events.subscribe(
                self._subscribe_to, self._collect_event, self.metadata.id
            )

    async def _collect_event(self, event: Event) -> None:
        """Collect events for later verification."""
        self.events_received.append(event)

    async def collect_hook_execution(self, **kwargs: Any) -> dict[str, Any]:
        """Hook that records its execution."""
        self.hooks_executed.append(kwargs)
        return {"processed": True, **kwargs}


class CapabilityTestPlugin(Plugin):
    """Plugin with declared capabilities and per-capability service tracking."""

    def __init__(
        self,
        name: str | None = None,
        provides: set[str] | None = None,
        requires: set[str] | None = None,
    ) -> None:
        super().__init__(
            name=name or f"cap_plugin_{uuid4().hex[:8]}",
            version="1.0.0",
            description=f"Plugin providing {provides} and requiring {requires}",
            author="Test Suite",
            provides=provides or set(),
            requires=requires or set(),
        )

        # Track capability access for testing
        self.capability_access_count = 0
        self.provided_services: dict[str, Any] = {}

    async def on_start(self) -> None:
        """Initialize provided services."""
        for capability in self.metadata.provides:
            self.provided_services[capability] = f"service_{capability}"

    async def on_stop(self) -> None:
        """Cleanup provided services."""
        self.provided_services.clear()

    def get_service(self, capability: str) -> Any:
        """Get service for a provided capability."""
        if capability in self.provided_services:
            self.capability_access_count += 1
            return self.provided_services[capability]
        raise ValueError(f"Service for {capability} not available")


def create_plugin_with_capabilities(
    core,
    provides: set[str] | None = None,
    requires: set[str] | None = None,
    name: str | None = None,
) -> Plugin:
    """Create a plugin with specific capabilities for testing.

    Args:
        core: Core instance
        provides: Set of capabilities this plugin provides
        requires: Set of capabilities this plugin requires
        name: Plugin name (auto-generated if None)

    Returns:
        Plugin instance with specified capabilities
    """
    return CapabilityTestPlugin(name=name, provides=provides, requires=requires)
