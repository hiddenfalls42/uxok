"""Core configuration dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CoreConfig:
    """Core system configuration.

    All configuration is validated on creation.
    """

    # Plugin limits (enforced by the registry at registration time)
    max_plugins: int = 100

    # Plugin blocking
    blocked_plugins: frozenset[str] = frozenset()

    # Hook system configuration
    hook_precaching: str = "on_core_start"  # "disabled", "on_core_start"

    # Capability system policy (config-driven)
    capability_collision: str = (
        "last_wins_with_warning"  # "error_on_conflict", "first_wins", "last_wins_with_warning"
    )
    # Paired with last_wins_with_warning so the most recently registered provider
    # is the one selected ("last wins" is consistent end to end).
    capability_selection: str = "last_registered"  # "first_registered", "last_registered"
    capability_missing: str = "raise"  # "raise", "return_none"
    # Secure capabilities (RFC 0001). "open": today's behavior — any plugin resolves
    # any capability, raw provider returned. "declared": a plugin may resolve only
    # capabilities in its `requires`, and its view of the kernel is attenuated to a
    # CoreFacet. "sealed": as declared, plus typed resolutions return a protocol facet.
    capability_access: str = "open"  # "open", "declared", "sealed"

    # Tick system
    tick_rate: int = 1000  # ticks per second (1000 Hz = 1ms precision)
    tick_slip_threshold: int = 5  # emit core.tick_slip event if slip >= this
    tick_precision: str = "sleep"  # "sleep" (pure asyncio), "hybrid" (sleep + busy-wait)
    tick_busy_wait_us: int = 200  # microseconds of busy-wait in hybrid mode
    # After a stall: "skip" jumps to the current boundary (recurring jobs fire
    # once, slip event emitted) — right for live robots. "burst" replays every
    # missed tick back-to-back — for simulation/replay.
    tick_catchup: str = "skip"  # "skip", "burst"

    # Per-plugin configuration namespaces
    plugin_configs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate configuration using separate validator module."""
        from .._config_validation import validate_core_config

        validate_core_config(self)
