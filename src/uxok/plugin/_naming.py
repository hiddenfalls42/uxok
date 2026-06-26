"""PluginProtocol name auto-detection utilities."""

from __future__ import annotations

import re

from uxok.utils import camel_to_snake, sanitize_identifier, validate_identifier


def detect_plugin_name(class_name: str) -> str:
    """Auto-detect plugin name from class name and converts it from CamelCase to snake_case.

    Args:
        class_name: PluginProtocol class name

    Returns:
        Snake-case plugin name
    """
    return camel_to_snake(class_name)


def validate_plugin_name(name: str) -> None:
    """Validate plugin name format. Checks if exists and if snake_case

    Args:
        name: PluginProtocol name to validate

    Raises:
        ValueError: If name is invalid
    """
    validated = validate_identifier(name, "PluginProtocol name")
    sanitized = sanitize_identifier(validated, "PluginProtocol name")
    if not re.match(r"^[a-z][a-z0-9_]*$", sanitized):
        raise ValueError(f"PluginProtocol name must be snake_case (a-z, 0-9, _): got '{name}'")
