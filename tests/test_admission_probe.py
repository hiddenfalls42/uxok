"""RFC 0003 v2 / spec 0005 §A — admission probe + atomic at-commit admission.

`Core.check_plugin` is the advisory, side-effect-free probe; `register_plugin`
runs the *same* admission (`Core._admit`) under the lifecycle lock at commit.
These tests pin both the per-fault verdicts and the drift-freedom guarantee that
the probe and the commit can never disagree (they share one routine).
"""

from typing import Protocol, runtime_checkable

import pytest

from uxok import Core, MissingCapabilityError, Plugin, PluginError
from uxok.protocols import AdmissionResult

# ---- candidates ---------------------------------------------------------------


class Provider(Plugin):
    def __init__(self, name="provider", cap="storage", **kw):
        super().__init__(name=name, provides={cap}, **kw)


class Consumer(Plugin):
    def __init__(self, **kw):
        super().__init__(name="consumer", requires={"storage"}, **kw)


class ResolverOnly(Plugin):
    """Declares a runtime grant for an absent capability — no load-order edge."""

    def __init__(self, **kw):
        super().__init__(name="resolver_only", resolves={"absent_cap"}, **kw)


class LifecycleConsumer(Plugin):
    def __init__(self, **kw):
        super().__init__(name="lifecycle_consumer", requires={"kernel.lifecycle"}, **kw)


@runtime_checkable
class Greeting(Protocol):
    async def hello(self, name: str = "World") -> str: ...
    async def goodbye(self, name: str = "World") -> str: ...


class IncompleteGreeter(Plugin):
    """Declares the Greeting protocol but omits goodbye()."""

    def __init__(self, **kw):
        super().__init__(name="incomplete_greeter", provides={Greeting}, **kw)

    async def hello(self, name: str = "World") -> str:
        return f"hi:{name}"


# ---- per-fault verdicts -------------------------------------------------------


class TestAdmissionVerdicts:
    @pytest.mark.asyncio
    async def test_clean_candidate_admits(self, clean_core: Core):
        result = await clean_core.check_plugin(Provider())
        assert result == AdmissionResult()
        assert result.ok

    @pytest.mark.asyncio
    async def test_missing_requires_fault(self, clean_core: Core):
        result = await clean_core.check_plugin(Consumer())
        assert not result.ok
        assert result.missing_requires == frozenset({"storage"})

    @pytest.mark.asyncio
    async def test_id_conflict_fault(self, started_core: Core):
        provider = Provider()
        assert await started_core.register_plugin(provider)
        result = await started_core.check_plugin(provider)
        assert not result.ok
        assert result.id_conflict

    @pytest.mark.asyncio
    async def test_provides_conflict_fault(self):
        core = Core(capability_collision="error_on_conflict")
        await core.start()
        try:
            assert await core.register_plugin(Provider(name="a", cap="alpha"))
            result = await core.check_plugin(Provider(name="b", cap="alpha"))
            assert not result.ok
            assert result.provides_conflicts == frozenset({"alpha"})
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_contract_failure_fault(self, clean_core: Core):
        result = await clean_core.check_plugin(IncompleteGreeter())
        assert not result.ok
        # capability name derived from the Greeting protocol
        assert result.contract_failures
        assert all("greeting" in c.lower() for c in result.contract_failures)

    @pytest.mark.asyncio
    async def test_resolves_only_with_absent_provider_admits(self, clean_core: Core):
        """RFC 0002: `resolves` is not load-validated, so an absent provider admits."""
        result = await clean_core.check_plugin(ResolverOnly())
        assert result.ok
        assert result.missing_requires == frozenset()

    @pytest.mark.asyncio
    async def test_reserved_grant_never_in_missing_requires(self, clean_core: Core):
        result = await clean_core.check_plugin(LifecycleConsumer())
        assert result.ok
        assert "kernel.lifecycle" not in result.missing_requires


# ---- side-effect freedom ------------------------------------------------------


class TestProbeHasNoSideEffects:
    @pytest.mark.asyncio
    async def test_probe_does_not_mutate_graph_or_fire(self, clean_core: Core):
        events: list[str] = []
        hooks: list[object] = []
        await clean_core.events.subscribe("plugin.registered", lambda e: events.append(e.name))
        await clean_core.events.subscribe("core.plugin_error", lambda e: events.append(e.name))
        await clean_core.hooks.register("plugin.registered", lambda p: hooks.append(p))

        # Probe a clean candidate AND a faulted one — neither may touch the graph.
        assert (await clean_core.check_plugin(Provider())).ok
        assert not (await clean_core.check_plugin(Consumer())).ok

        assert await clean_core._registry.all() == {}
        assert await clean_core._capability_system.list_capabilities() == []
        assert events == []
        assert hooks == []


# ---- TOCTOU + drift-freedom ---------------------------------------------------


class TestAtomicAdmission:
    @pytest.mark.asyncio
    async def test_probe_passes_but_commit_rejects_under_drift(self):
        """A probe can pass and a later register still reject when the graph drifts."""
        core = Core(capability_collision="error_on_conflict")
        await core.start()
        try:
            candidate = Provider(name="late", cap="beta")
            assert (await core.check_plugin(candidate)).ok  # nothing provides beta yet

            # graph drifts: another beta provider commits in between
            assert await core.register_plugin(Provider(name="first", cap="beta"))

            with pytest.raises(PluginError):
                await core.register_plugin(candidate)  # re-admission at commit rejects
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_drift_freedom_probe_matches_commit(self, started_core: Core):
        """The same candidate yields the same verdict via probe and via register."""
        consumer = Consumer()
        probe = await started_core.check_plugin(consumer)
        assert probe.missing_requires == frozenset({"storage"})

        with pytest.raises(MissingCapabilityError):
            await started_core.register_plugin(consumer)

        # the probe still reports the same fault afterwards (no mutation occurred)
        assert await started_core.check_plugin(consumer) == probe
