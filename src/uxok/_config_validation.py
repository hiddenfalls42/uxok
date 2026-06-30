"""Configuration validation functions for CoreConfig."""

from __future__ import annotations

from typing import TYPE_CHECKING

from uxok.utils import validate_enum_value, validate_identifier, validate_positive_number

# Standard Python validation - no custom error classes needed

if TYPE_CHECKING:
    from uxok.protocols.config import CoreConfig


def _validate_basic_values(config: CoreConfig) -> None:
    """Validate basic value constraints for core fields."""
    validate_positive_number(config.max_plugins, "max_plugins")


def _validate_hook_config(config: CoreConfig) -> None:
    """Validate hook system configuration fields."""
    # Validate hook precaching strategy
    validate_enum_value(
        validate_identifier(config.hook_precaching, "hook_precaching"),
        {"disabled", "on_core_start"},
        "hook_precaching",
    )


def _validate_capability_config(config: CoreConfig) -> None:
    """Validate capability system configuration fields."""
    validate_enum_value(
        validate_identifier(config.capability_collision, "capability_collision"),
        {"error_on_conflict", "first_wins", "last_wins_with_warning"},
        "capability_collision",
    )
    validate_enum_value(
        validate_identifier(config.capability_selection, "capability_selection"),
        {"first_registered", "last_registered"},
        "capability_selection",
    )
    validate_enum_value(
        validate_identifier(config.capability_missing, "capability_missing"),
        {"raise", "return_none"},
        "capability_missing",
    )
    validate_enum_value(
        validate_identifier(config.capability_access, "capability_access"),
        {"open", "declared", "sealed"},
        "capability_access",
    )


def _validate_timing_config(config: CoreConfig) -> None:
    """Validate tick system configuration."""
    validate_positive_number(config.tick_rate, "tick_rate")
    if config.tick_rate > 10_000:
        raise ValueError("tick_rate cannot exceed 10000 Hz")

    validate_positive_number(config.tick_slip_threshold, "tick_slip_threshold")

    validate_enum_value(
        config.tick_precision,
        {"sleep", "hybrid"},
        "tick_precision",
    )

    validate_positive_number(config.tick_busy_wait_us, "tick_busy_wait_us")
    if config.tick_busy_wait_us > 1_000_000:
        raise ValueError("tick_busy_wait_us cannot exceed 1000000 (1 second)")

    validate_enum_value(
        config.tick_catchup,
        {"skip", "burst"},
        "tick_catchup",
    )


def validate_core_config(config: CoreConfig) -> None:
    """Validate all aspects of the core configuration.

    This function orchestrates all validation functions to ensure
    the configuration is valid and safe to use.

    Args:
        config: The core configuration to validate

    Raises:
        ValueError: If any validation fails
    """
    _validate_basic_values(config)
    _validate_hook_config(config)
    _validate_capability_config(config)
    _validate_timing_config(config)
