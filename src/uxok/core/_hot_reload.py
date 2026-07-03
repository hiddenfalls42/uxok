"""Hot-reload swap machinery: zero-downtime plugin instance replacement."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from uxok.errors import CoreError, PluginError
from uxok.protocols import CoreState
from uxok.protocols.events import Event
from uxok.utils import build_plugin_error_event, log_op

if TYPE_CHECKING:
    from uxok.core._core import Core
    from uxok.protocols import PluginProtocol

logger = logging.getLogger(__name__)


async def reload_plugin_now(
    core: Core, old_plugin: PluginProtocol, new_plugin: PluginProtocol
) -> None:
    """Reload logic, runs under the lifecycle lock and the operation guard.

    Mirrors ``_register_plugin_now``: a hot-reload is a lifecycle operation and
    must be serialized through the lifecycle lock and protected by the same
    per-plugin operation guard as registration/unregistration.
    """
    if core.state is not CoreState.RUNNING:
        raise CoreError("Core must be started before reloading plugins")

    plugin_id = old_plugin.metadata.id

    if not await core._active_operations.add(plugin_id):
        raise PluginError(f"Plugin {plugin_id} already has an active operation")
    try:
        await swap_plugin(core, old_plugin, new_plugin)
    finally:
        await core._active_operations.remove(plugin_id)


async def swap_plugin(core: Core, old_plugin: PluginProtocol, new_plugin: PluginProtocol) -> None:
    """Atomically swap a plugin instance with zero downtime.

    This is a kernel primitive for hot reload (internal use only).
    The swap:
    1. Starts the new plugin (registers hooks and events)
    2. Atomically swaps instance in registry (preserves ID, deps, dependents)
    3. Reconciles capability providers (replace/add/remove, de-duplicated)
    4. Drains old plugin's hooks, events, and background tasks

    During the brief window between steps 1 and 4, both instances exist,
    but registry lookups return only the new instance. Capabilities remain
    available throughout with zero interruption.

    Args:
        core: The kernel core instance.
        old_plugin: Existing registered plugin instance to replace.
        new_plugin: New plugin instance. Must have the same name as old_plugin.

    Raises:
        PluginError: If old_plugin is not found, or names do not match.
    """
    old_id = old_plugin.metadata.id

    # 1. Fail fast if the new version's requirements aren't satisfiable,
    #    and compute its fresh dependency edges (declared + capability).
    cap_deps = await core._capability_system.validate_requirements(new_plugin)
    new_deps = set(new_plugin.metadata.dependencies) | (cap_deps or set())
    new_deps.discard(old_id)  # a plugin never depends on itself
    old_deps = await core._registry.dependencies(old_id)

    # 2. State handoff: capture from the old instance before any mutation.
    #    A get_state() failure aborts the reload with nothing to roll back.
    state = await old_plugin.get_state()

    registry_swapped = False
    try:
        # 3. Attach the core, then start the new plugin (registers hooks and
        #    events). Attach must precede start: on_start uses self.core.
        core._attach_core_to(new_plugin)
        await new_plugin.start()

        # 4. Swap in registry (atomic - preserves ID and dependents,
        #    replaces dependency edges with the new version's)
        await core._registry.swap_instance(old_id, new_plugin, dependencies=new_deps)
        registry_swapped = True

        # 5. Reconcile capability providers (in-place replace, de-duplicated)
        rebound = await core._capability_system.swap_provider(old_plugin, new_plugin)

        # 5a. Announce rebinds. The capability mutation above already
        #     completed synchronously, so this event-bus await is outside
        #     the critical section (lock-free invariant). A publish failure
        #     must never fail the reload.
        for capability, old_provider_id, new_provider_id in rebound:
            with suppress(Exception):
                await core._event_bus.publish(
                    Event(
                        "core.capability.rebound",
                        {
                            "capability": capability,
                            "old_provider_id": old_provider_id,
                            "new_provider_id": new_provider_id,
                        },
                    )
                )

        # 6. Hand the captured state to the new instance
        await new_plugin.restore_state(state)
    except Exception:
        # Roll back so the old version keeps running. Both instances share
        # the plugin ID, so the half-started new instance is drained by
        # INSTANCE identity — the old instance's registrations are never
        # touched.
        if registry_swapped:
            with suppress(Exception):
                await core._registry.swap_instance(old_id, old_plugin, dependencies=old_deps)
        with suppress(Exception):
            await drain_instance(core, new_plugin)
        raise

    # 7. Call the old instance's on_stop so it can release external resources.
    #    on_stop is NOT part of PluginProtocol (protocols are immutable), so
    #    use a getattr guard. A raising on_stop must never fail the reload.
    on_stop = getattr(old_plugin, "on_stop", None)
    if on_stop is not None:
        try:
            await on_stop()
        except Exception as e:
            logger.warning(
                "Error in plugin on_stop during hot reload",
                extra={
                    "plugin_id": str(old_plugin.metadata.id),
                    "plugin_name": old_plugin.metadata.name,
                    "error": str(e),
                },
            )
            with suppress(Exception):
                await core._event_bus.publish(
                    build_plugin_error_event(
                        str(old_plugin.metadata.id),
                        old_plugin.metadata.name,
                        "lifecycle",
                        e,
                        phase="on_stop",
                    )
                )

    # Mark the old instance shut down so a stray later stop() on a retained
    # reference is a no-op: Plugin.stop() guards on _shutdown (not
    # name-mangled), so setting it here prevents a double on_stop call if
    # the caller holds a reference to the old instance after reload.
    if hasattr(old_plugin, "_shutdown"):
        old_plugin._shutdown = True  # type: ignore[union-attr]

    # 8. Drain the old instance — by instance identity, for the same
    #    shared-ID reason: an ID-wide drain would also destroy the new
    #    instance's just-registered hooks and subscriptions.
    await drain_instance(core, old_plugin)

    logger.debug(
        "Swapped plugin instance with zero downtime",
        extra=log_op(
            "swap_plugin",
            plugin_name=old_plugin.metadata.name,
            plugin_id=str(old_id),
        ),
    )


async def drain_instance(core: Core, plugin: Any) -> None:
    """Drain one plugin INSTANCE's resources, leaving same-ID siblings intact.

    Hot-reload companion to drain_plugin_resources: during a swap the old
    and new instances share a plugin ID, so cleanup must be scoped by
    instance identity. Hooks/subscriptions registered with closures that
    carry no instance ownership are left for the ID-wide drain at
    unregistration.
    """
    with suppress(Exception):
        await core._event_bus.unsubscribe_owner(plugin)
    with suppress(Exception):
        await core._hook_system.unregister_owner_hooks(plugin)
    with suppress(Exception):
        core._tick_scheduler.unschedule_owner(plugin)
    if hasattr(plugin, "_task_manager"):
        with suppress(Exception):
            await plugin._task_manager.cancel_all()
