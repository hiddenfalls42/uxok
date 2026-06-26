"""Plugin package - Plugin base class and decorators."""

from uxok.plugin._base import Plugin
from uxok.plugin._decorators import event, handle_errors, hook
from uxok.plugin.config_field import REQUIRED, ConfigField

__all__ = [
    "REQUIRED",
    "ConfigField",
    "Plugin",
    "event",
    "handle_errors",
    "hook",
]
