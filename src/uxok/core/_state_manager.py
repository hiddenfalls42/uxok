"""Simplified state management for Core system."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from uxok.errors import CoreError
from uxok.protocols import CoreState
from uxok.protocols.events import EventBus
from uxok.protocols.hooks import HookSystem

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)

# Constitutional transition graph:
# INITIALIZED → RUNNING | STOPPED
# RUNNING → STOPPING
# STOPPING → STOPPED | FAILED
# STOPPED → INITIALIZED
# FAILED → INITIALIZED
#
# STOPPING is the drain phase (teardown runs inside it); FAILED means the
# teardown itself failed. Plugin-level failures are events, not core states.
VALID_TRANSITIONS = {
    CoreState.INITIALIZED: {CoreState.RUNNING, CoreState.STOPPED},
    CoreState.RUNNING: {CoreState.STOPPING},
    CoreState.STOPPING: {CoreState.STOPPED, CoreState.FAILED},
    CoreState.STOPPED: {CoreState.INITIALIZED},
    CoreState.FAILED: {CoreState.INITIALIZED},
}


class StateManager:
    """Manages core state transitions and lifecycle."""

    def __init__(self, core_id: UUID, event_bus: EventBus, hook_system: HookSystem) -> None:
        """Initialize state manager.

        Args:
            core_id: Unique core instance ID
            event_bus: Event bus for state change notifications
            hook_system: Hook system for state change notifications
        """
        self._core_id = core_id
        self._event_bus = event_bus
        self._hook_system = hook_system

        # Plain state value with narrow lock for atomic transitions
        self._state: CoreState = CoreState.INITIALIZED
        self._state_lock: asyncio.Lock = asyncio.Lock()

    @property
    def state(self) -> CoreState:
        """Get current core state.

        Returns:
            Current state (direct attribute access, thread-safe in cooperative asyncio)
        """
        return self._state

    def _is_valid_transition(self, from_state: CoreState, to_state: CoreState) -> bool:
        """Check if transition is valid."""
        if from_state not in VALID_TRANSITIONS:
            return False
        return to_state in VALID_TRANSITIONS[from_state]

    async def transition(self, target: CoreState) -> None:
        """Atomically transition to target state with validation.

        Args:
            target: Target state to transition to

        Raises:
            CoreError: If transition is invalid
        """
        if not isinstance(target, CoreState):
            raise ValueError(f"Invalid state: {target}. Must be CoreState enum.")

        # Hold lock across full check-validate-write sequence
        async with self._state_lock:
            old_state = self._state

            # Validate transition
            if not self._is_valid_transition(old_state, target):
                # State might have changed since we entered the lock
                current = self._state
                raise CoreError(f"Invalid transition from {current.value} to {target.value}")

            # Atomically update state
            self._state = target

        # Fire hook OUTSIDE lock (prevents deadlock)
        await self._hook_system.execute("core.state.changed", old_state, target)

    async def start(self) -> None:
        """Start the core system.

        Transitions from INITIALIZED to RUNNING.
        Handles restart from STOPPED/FAILED: STOPPED|FAILED → INITIALIZED → RUNNING.

        Raises:
            CoreError: If not in INITIALIZED, STOPPED, or FAILED state
        """
        try:
            current_state = self.state

            if current_state in {CoreState.STOPPED, CoreState.FAILED}:
                await self.transition(CoreState.INITIALIZED)
                logger.info(
                    "Core reinitialized for restart",
                    extra={"core_id": str(self._core_id), "from": current_state.value},
                )

            if self.state != CoreState.INITIALIZED:
                raise CoreError(
                    f"Cannot start core from {self.state.value}; "
                    "expected initialized/restartable state"
                )

            await self.transition(CoreState.RUNNING)

            logger.info("Core started", extra={"core_id": str(self._core_id)})
        except Exception as e:
            logger.warning(
                f"State manager start failed: {e}",
                extra={"core_id": str(self._core_id)},
            )
            raise

    async def begin_stop(self) -> bool:
        """Enter the STOPPING drain phase.

        Returns:
            True when the caller should run teardown and then finish_stop().
            False when there is nothing to tear down (already stopped, or
            stopped straight from INITIALIZED).

        Raises:
            CoreError: If the current state cannot transition toward STOPPED
        """
        current_state = self.state
        if current_state in {CoreState.STOPPED, CoreState.FAILED}:
            return False
        if current_state == CoreState.INITIALIZED:
            await self.transition(CoreState.STOPPED)
            return False
        if current_state == CoreState.STOPPING:
            # Re-entrant stop: teardown is idempotent, let the caller proceed.
            return True

        await self.transition(CoreState.STOPPING)
        return True

    async def finish_stop(self) -> None:
        """Complete the drain phase: STOPPING → STOPPED."""
        await self.transition(CoreState.STOPPED)
        logger.info("Core stopped", extra={"core_id": str(self._core_id)})

    async def fail(self) -> None:
        """Mark teardown as failed: STOPPING → FAILED."""
        await self.transition(CoreState.FAILED)
        logger.error("Core teardown failed", extra={"core_id": str(self._core_id)})
