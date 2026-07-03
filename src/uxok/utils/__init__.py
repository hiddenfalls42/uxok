"""Utilities for async-safe operations."""

from __future__ import annotations

from ._capability_utils import (
    derive_capability_name,
    get_instance_methods,
    get_protocol_methods,
    normalize_capability_set,
)
from ._formatting import (
    build_plugin_error_event,
    format_capability_error,
    format_plugin_error,
    log_op,
)
from ._helpers import (
    AsyncTaskManager,
    camel_to_snake,
    log_context,
    safe_str,
    sanitize_identifier,
    validate_enum_value,
    validate_identifier,
    validate_positive_number,
)
from .async_primitives import _AsyncSafeSet

__all__ = [
    "AsyncTaskManager",
    "_AsyncSafeSet",
    "build_plugin_error_event",
    "camel_to_snake",
    "derive_capability_name",
    "format_capability_error",
    "format_plugin_error",
    "get_instance_methods",
    "get_protocol_methods",
    "log_context",
    "log_op",
    "normalize_capability_set",
    "safe_str",
    "sanitize_identifier",
    "validate_enum_value",
    "validate_identifier",
    "validate_positive_number",
]
