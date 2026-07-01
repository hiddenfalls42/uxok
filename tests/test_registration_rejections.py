import pytest

from tests.helpers import create_plugin_with_capabilities
from uxok import Core
from uxok.errors import MissingCapabilityError, PluginError


@pytest.mark.asyncio
async def test_missing_capability_rejects_without_side_effects(started_core: Core):
    plugin = create_plugin_with_capabilities(
        started_core, requires={"storage"}, name="consumer_plugin"
    )

    with pytest.raises(MissingCapabilityError):
        await started_core.register_plugin(plugin)

    registry = await started_core._registry.all()
    capabilities = await started_core._capability_system.list_capabilities()

    assert registry == {}
    assert capabilities == []


@pytest.mark.asyncio
async def test_capability_collision_rejects_and_keeps_existing_provider():
    core = Core(capability_collision="error_on_conflict")

    try:
        await core.start()
        provider_a = create_plugin_with_capabilities(core, provides={"alpha"}, name="provider_a")
        provider_b = create_plugin_with_capabilities(core, provides={"alpha"}, name="provider_b")

        assert await core.register_plugin(provider_a)

        with pytest.raises(PluginError):
            await core.register_plugin(provider_b)

        registry = await core._registry.all()
        capabilities = await core._capability_system.list_capabilities()

        assert {p.metadata.name for p in registry.values()} == {"provider_a"}
        assert set(capabilities) == {"alpha"}
    finally:
        if core.state.name == "RUNNING":
            await core.stop()


@pytest.mark.asyncio
async def test_unregister_rejects_when_dependents_present(started_core: Core):
    provider = create_plugin_with_capabilities(
        started_core, provides={"storage"}, name="provider_plugin"
    )
    consumer = create_plugin_with_capabilities(
        started_core, requires={"storage"}, name="consumer_plugin"
    )

    assert await started_core.register_plugin(provider)
    assert await started_core.register_plugin(consumer)

    with pytest.raises(PluginError):
        await started_core.unregister_plugin(provider.metadata.id)

    registry = await started_core._registry.all()
    capabilities = await started_core._capability_system.list_capabilities()

    assert {p.metadata.name for p in registry.values()} == {"provider_plugin", "consumer_plugin"}
    assert set(capabilities) == {"storage"}
