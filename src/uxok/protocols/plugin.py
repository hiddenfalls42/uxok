"""PluginProtocol protocol and metadata definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from uxok.protocols._types import PluginId


@dataclass(frozen=True)
class PluginMetadata:
    """PluginProtocol metadata - immutable after creation."""

    id: PluginId
    name: str
    version: str
    description: str = ""
    author: str = ""
    dependencies: frozenset[PluginId] = field(default_factory=frozenset)
    requires: frozenset[str] = field(default_factory=frozenset)
    resolves: frozenset[str] = field(default_factory=frozenset)
    provides: frozenset[str] = field(default_factory=frozenset)
    hooks_consumed: frozenset[str] = field(default_factory=frozenset)
    events_published: frozenset[str] = field(default_factory=frozenset)
    tags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Validate metadata after initialization."""
        if not self.name:
            raise ValueError("Plugin name cannot be empty")
        if not self.version:
            raise ValueError("Plugin version cannot be empty")


@runtime_checkable
class PluginProtocol(Protocol):
    """Minimal plugin interface.

    All plugins must implement this protocol.
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Get plugin metadata.

        Returns:
            PluginProtocol metadata
        """
        ...

    async def start(self) -> None:
        """Initialize the plugin.

        Called after the plugin is registered and dependencies are loaded.
        """
        ...

    async def stop(self) -> None:
        """Shutdown the plugin.

        Called before the plugin is unregistered.
        """
        ...

    async def get_state(self) -> dict:
        """Serialize durable state for hot reload / supervised restart.

        Called on the OLD instance before a swap (constitutional contract,
        see API.md). Return a plain serializable dict; {} when no state
        carries over.
        """
        ...

    async def restore_state(self, state: dict) -> None:
        """Ingest state from the previous instance during hot reload.

        Called on the NEW instance after start() and before the old instance
        is drained. The dict is whatever the old version's get_state()
        returned; ignore it when no state applies.
        """
        ...
