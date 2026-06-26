"""Positive blocked-registration contract through the public Core API.

The constitutional contract (Core.register_plugin docstring, API.md): a
blocked plugin is REFUSED with a `False` return — registration does NOT
raise. The refusal happens in PluginRegistryImpl.add()
(src/uxok/registry/impl.py lines ~78-86), which checks both the
plugin name and the stringified plugin ID against the blocked set.

tests/test_registry_block_race.py covers race timing between block/unblock
and add; tests/test_registry.py covers the registry-level add() refusal in
isolation. This file adds the missing deterministic end-to-end contract on
a STARTED core: blocked -> register_plugin returns False and nothing is
registered or started; other plugins are unaffected; unblock restores
registrability.
"""

import pytest

from tests.helpers import StubPlugin
from uxok import Plugin


class TestBlockedRegistrationContract:
    """Blocking refuses registration with False, never an exception."""

    @pytest.mark.asyncio
    async def test_blocked_name_refuses_registration_with_false(self, started_core):
        """register_plugin on a blocked name returns False; plugin untouched."""
        core = started_core
        await core._registry.block("blocked_plugin")

        started_flags = []

        class TrackedPlugin(Plugin):
            async def on_start(self) -> None:
                started_flags.append(True)

        plugin = TrackedPlugin(name="blocked_plugin")

        # Contract: refusal is a False return, NOT an exception.
        result = await core.register_plugin(plugin)
        assert result is False

        # Nothing was registered and the plugin lifecycle never began.
        assert await core._registry.contains(plugin.metadata.id) is False
        assert (await core.list()).by_name("blocked_plugin") is None
        assert started_flags == []

    @pytest.mark.asyncio
    async def test_blocked_id_refuses_registration_with_false(self, started_core):
        """Blocking by stringified plugin ID also refuses registration."""
        core = started_core
        plugin = StubPlugin(name="id_blocked_plugin")
        await core._registry.block(str(plugin.metadata.id))

        assert await core.register_plugin(plugin) is False
        assert await core._registry.contains(plugin.metadata.id) is False

    @pytest.mark.asyncio
    async def test_non_blocked_plugin_registers_after_blocked_refusal(self, started_core):
        """A blocked refusal does not affect other registrations."""
        core = started_core
        await core._registry.block("blocked_plugin")

        blocked = StubPlugin(name="blocked_plugin")
        assert await core.register_plugin(blocked) is False

        allowed = StubPlugin(name="allowed_plugin")
        assert await core.register_plugin(allowed) is True
        assert await core._registry.contains(allowed.metadata.id) is True
        assert (await core.list()).by_name("allowed_plugin") is not None

    @pytest.mark.asyncio
    async def test_unblock_restores_registrability(self, started_core):
        """After unblock(), the same name registers successfully."""
        core = started_core
        await core._registry.block("recyclable_plugin")

        first_attempt = StubPlugin(name="recyclable_plugin")
        assert await core.register_plugin(first_attempt) is False

        assert await core._registry.unblock("recyclable_plugin") is True
        assert core._registry.is_blocked("recyclable_plugin") is False

        # Plugin instances are one-shot: use a fresh instance for the retry.
        retry = StubPlugin(name="recyclable_plugin")
        assert await core.register_plugin(retry) is True
        assert await core._registry.contains(retry.metadata.id) is True
