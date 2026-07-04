"""Protocols package - public API."""

from uxok.protocols._types import CoreState
from uxok.protocols.config import CoreConfig
from uxok.protocols.core import (
    AdmissionResult,
    BatchLoadReport,
    Core,
    SkippedSource,
)
from uxok.protocols.events import Event
from uxok.protocols.hooks import Hook
from uxok.protocols.plugin import PluginMetadata, PluginProtocol

# Intentionally NOT re-exported: the `EventName`/`HookName`/`PluginId` aliases
# (plain `str`/`UUID`, no contract value) and the `EventBus`/`HookSystem`
# protocols (reached via `core.events`/`core.hooks`, never named by authors).
# They stay importable from their definition modules for internal kernel use.
__all__ = [
    "AdmissionResult",
    "BatchLoadReport",
    "Core",
    "CoreConfig",
    "CoreState",
    "Event",
    "Hook",
    "PluginMetadata",
    "PluginProtocol",
    "SkippedSource",
]
