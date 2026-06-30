"""uxok framework - Plugin-driven architecture."""

from importlib.metadata import PackageNotFoundError, version

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

try:
    __version__ = version("uxok")
except PackageNotFoundError:  # running from a source tree without install metadata
    __version__ = "0.0.0.dev0"

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
