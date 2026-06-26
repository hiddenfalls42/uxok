"""Plugin configuration field declarations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class _RequiredType:
    """Sentinel: this field has no default and must be supplied."""

    def __repr__(self) -> str:
        return "REQUIRED"


REQUIRED = _RequiredType()


@dataclass
class ConfigField:
    """Declared configuration field for a plugin.

    Args:
        type: Expected Python type. Used for validation at start().
        default: Default value, or REQUIRED if the caller must supply it.
        description: Human-readable description for error messages and docs.

    Example:
        ```python
        config_schema = {
            "db_url": ConfigField(str, REQUIRED, "Database connection URL"),
            "timeout": ConfigField(int, 30, "Connection timeout in seconds"),
            "debug": ConfigField(bool, False),
        }
        ```
    """

    type: type
    default: Any = REQUIRED
    description: str = ""
