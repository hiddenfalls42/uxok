"""uxok framework - Plugin-driven architecture."""

from uxok.core import Core
from uxok.errors import (
    CapabilityAccessError,
    CapabilityError,
    CoreError,
    MissingCapabilityError,
    PluginError,
    StalePluginError,
)
from uxok.plugin import REQUIRED, ConfigField, Plugin, event, hook

__all__ = [
    "REQUIRED",
    "CapabilityAccessError",
    "CapabilityError",
    "ConfigField",
    "Core",
    "CoreError",
    "MissingCapabilityError",
    "Plugin",
    "PluginError",
    "StalePluginError",
    "event",
    "hook",
]
