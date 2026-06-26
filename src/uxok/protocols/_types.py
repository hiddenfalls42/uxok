"""Internal type definitions for protocols package."""

from __future__ import annotations

from enum import Enum
from uuid import UUID

PluginId = UUID
HookName = str
EventName = str


class CoreState(Enum):
    """Core system states.

    Plugin-level failures are signals (core.plugin_error / core.hook_error),
    not core states — supervision policy lives in plugins. FAILED is reached
    only when teardown itself fails.
    """

    INITIALIZED = "initialized"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
