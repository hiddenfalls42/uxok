"""Tests for Core.load_plugins() batch loading (RFC 0008).

Red-first: load_plugins is implemented concurrently elsewhere. These tests
exercise the real public API (no monkeypatching) and are expected to fail
with AttributeError / collection-time import success but runtime failure
until the feature lands.

Source-string helpers mirror the inline-``Plugin``-subclass idiom used in
tests/test_load_plugin.py. The canonical five-plugin graph is RFC 0008 §4.3's
worked example: storage <- index <- search, auth, {search, auth} <- api.
"""

import random
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from uxok import BatchLoadError, Core
from uxok.errors import CoreError, MissingCapabilityError, PluginError
from uxok.protocols import CoreState


def _class_name(name: str) -> str:
    """Derive a valid PascalCase class name from a snake_case plugin name."""
    return "".join(part.capitalize() for part in name.split("_")) + "Plugin"


def _source(
    name: str,
    *,
    provides: frozenset[str] = frozenset(),
    requires: frozenset[str] = frozenset(),
    body: str = "",
) -> str:
    """Build inline Plugin subclass source, matching test_load_plugin.py's idiom."""
    class_name = _class_name(name)
    return f"""
class {class_name}(Plugin):
    def __init__(self, **kw):
        super().__init__(
            name="{name}",
            provides={set(provides)!r},
            requires={set(requires)!r},
            **kw,
        )
{body}"""


# --- The RFC §4.3 worked-example graph --------------------------------------
# storage -> index -> search -> api
# storage ----------> search
# auth --------------------> api

STORAGE_SRC = _source("storage", provides=frozenset({"storage"}))
INDEX_SRC = _source("index", provides=frozenset({"index"}), requires=frozenset({"storage"}))
SEARCH_SRC = _source(
    "search", provides=frozenset({"search"}), requires=frozenset({"storage", "index"})
)
AUTH_SRC = _source("auth", provides=frozenset({"auth"}))
API_SRC = _source("api", requires=frozenset({"search", "auth"}))

GRAPH_SOURCES = {
    "storage": STORAGE_SRC,
    "index": INDEX_SRC,
    "search": SEARCH_SRC,
    "auth": AUTH_SRC,
    "api": API_SRC,
}
GRAPH_PROVIDES = {
    "storage": {"storage"},
    "index": {"index"},
    "search": {"search"},
    "auth": {"auth"},
    "api": set(),
}
GRAPH_REQUIRES = {
    "storage": set(),
    "index": {"storage"},
    "search": {"storage", "index"},
    "auth": set(),
    "api": {"search", "auth"},
}
GRAPH_NAMES = tuple(GRAPH_SOURCES)


def shuffled_graph_sources(seed: int) -> list[tuple[str, None]]:
    """The canonical graph's sources, deterministically shuffled by seed."""
    names = list(GRAPH_NAMES)
    random.Random(seed).shuffle(names)
    return [(GRAPH_SOURCES[name], None) for name in names]


def assert_topological_order(
    order: tuple[str, ...],
    provides: dict[str, set[str]] = GRAPH_PROVIDES,
    requires: dict[str, set[str]] = GRAPH_REQUIRES,
) -> None:
    """Every consumer must appear after every in-batch provider it depends on.

    Deliberately does not check for one hardcoded order — multiple valid
    topological orders exist for this graph (e.g. auth may land anywhere
    relative to storage/index).
    """
    position = {name: i for i, name in enumerate(order)}
    for consumer, needed in requires.items():
        if consumer not in position:
            continue
        for capability in needed:
            for provider, provided in provides.items():
                if capability in provided and provider in position:
                    assert position[provider] < position[consumer], (
                        f"'{provider}' (provides '{capability}') must precede "
                        f"'{consumer}' in {order}"
                    )


async def _live_names(core: Core) -> set[str]:
    return {plugin.metadata.name for plugin in (await core._registry.all()).values()}


BOOM_ON_START_BODY = """
    async def on_start(self):
        raise RuntimeError("boom on start")
"""
BOOM_SRC = _source("boom", requires=frozenset({"index"}), body=BOOM_ON_START_BODY)
BOOM_SOURCES = [(STORAGE_SRC, None), (INDEX_SRC, None), (BOOM_SRC, None)]

BROKEN_SYNTAX_SRC = "def broken(: syntax error here"

# A well-formed Plugin subclass whose __init__ raises a *non*-PluginError. The
# constructor runs during materialize (cls()), so this exercises the envelope
# for arbitrary developer bugs in __init__, not just compile/discovery faults.
CONSTRUCTOR_RAISES_SRC = """
class ExplodingPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="exploding", **kw)
        raise ValueError("boom from constructor")
"""

# A provider that declares a typed capability (a Protocol) but never implements
# its method. contract_failures() is a pure function of this candidate alone —
# zero TOCTOU — so the plan phase can reject it before any commit (H-002).
CONTRACT_FAILURE_SRC = """
from typing import Protocol, runtime_checkable

@runtime_checkable
class Greeter(Protocol):
    def greet(self) -> str: ...

class BadGreeterPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="bad_greeter", provides={Greeter}, **kw)
"""


@pytest_asyncio.fixture
async def conflict_core() -> AsyncGenerator[Core, None]:
    """Started core configured to error on capability-provider collisions."""
    core = Core(capability_collision="error_on_conflict")
    await core.start()
    try:
        yield core
    finally:
        if core.state is CoreState.RUNNING:
            await core.stop()


class TestSuccessfulBatchLoad:
    """RFC 0008 §10 bullet 1: a mutually-dependent graph boots in valid topo order."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("seed", [1, 2, 3])
    async def test_shuffled_graph_boots_in_valid_topological_order(
        self, started_core: Core, seed: int
    ):
        """Arbitrary source order still yields a valid dependency-respecting boot."""
        result = await started_core.load_plugins(shuffled_graph_sources(seed))

        assert isinstance(result, tuple)
        assert set(result) == set(GRAPH_NAMES)
        assert_topological_order(result)

    @pytest.mark.asyncio
    async def test_empty_sources_returns_empty_tuple_and_registry_unchanged(
        self, started_core: Core
    ):
        result = await started_core.load_plugins([])

        assert result == ()
        assert isinstance(result, tuple)
        assert await started_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_batch_consumer_satisfied_by_already_live_provider(self, started_core: Core):
        """A candidate requiring a capability satisfied by a LIVE (not in-batch) provider boots."""
        await started_core.load_plugin(STORAGE_SRC)

        result = await started_core.load_plugins([(INDEX_SRC, None)])

        assert result == ("index",)
        assert await started_core.get_plugin("index") is not None

    @pytest.mark.asyncio
    async def test_requires_reserved_capability_boots_without_provider(self, started_core: Core):
        """RESERVED_CAPABILITIES (e.g. kernel.lifecycle) need no provider, in-batch or live."""
        code = _source("needs_kernel_cap", requires=frozenset({"kernel.lifecycle"}))

        result = await started_core.load_plugins([(code, None)])

        assert result == ("needs_kernel_cap",)

    @pytest.mark.asyncio
    async def test_duplicate_providers_boot_under_default_collision_policy(
        self, started_core: Core
    ):
        """Two in-batch providers of one capability are legal under the default policy."""
        provider_a = _source("dup_provider_a", provides=frozenset({"dup_cap"}))
        provider_b = _source("dup_provider_b", provides=frozenset({"dup_cap"}))

        result = await started_core.load_plugins([(provider_a, None), (provider_b, None)])

        assert set(result) == {"dup_provider_a", "dup_provider_b"}


class TestPlanPhaseErrors:
    """RFC 0008 §10 bullet 2: graph-shape faults fail in "plan", zero partial state."""

    @pytest.mark.asyncio
    async def test_missing_external_capability_raises_plan_error(self, started_core: Core):
        code = _source("needs_ghost", requires=frozenset({"ghost_cap"}))

        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins([(code, None)])

        err = excinfo.value
        assert err.phase == "plan"
        assert err.installed == ()
        assert err.failed == "needs_ghost"
        assert isinstance(err.__cause__, MissingCapabilityError)
        assert "ghost_cap" in str(err.__cause__)
        assert await started_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_dependency_cycle_raises_plan_error(self, started_core: Core):
        cyc_a = _source(
            "cyc_a", provides=frozenset({"alpha_cap"}), requires=frozenset({"beta_cap"})
        )
        cyc_b = _source(
            "cyc_b", provides=frozenset({"beta_cap"}), requires=frozenset({"alpha_cap"})
        )

        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins([(cyc_a, None), (cyc_b, None)])

        err = excinfo.value
        assert err.phase == "plan"
        assert err.installed == ()
        assert err.failed is None
        assert isinstance(err.__cause__, CoreError)
        cause_message = str(err.__cause__)
        assert "cyc_a" in cause_message
        assert "cyc_b" in cause_message
        assert await started_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_duplicate_name_within_batch_raises_plan_error(self, started_core: Core):
        first = _source("dup_name", provides=frozenset({"dup_name_cap_a"}))
        second = _source("dup_name", provides=frozenset({"dup_name_cap_b"}))

        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins([(first, None), (second, None)])

        err = excinfo.value
        assert err.phase == "plan"
        assert err.installed == ()
        assert err.failed == "dup_name"
        assert isinstance(err.__cause__, PluginError)
        assert await started_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_candidate_name_collides_with_live_plugin_raises_plan_error(
        self, started_core: Core
    ):
        live_src = _source("live_name", provides=frozenset({"live_cap"}))
        candidate_src = _source("live_name", provides=frozenset({"other_cap"}))

        await started_core.load_plugin(live_src)
        live_before = await started_core.get_plugin("live_name")

        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins([(candidate_src, None)])

        err = excinfo.value
        assert err.phase == "plan"
        assert err.installed == ()
        assert err.failed == "live_name"
        assert isinstance(err.__cause__, PluginError)

        # The live plugin is untouched — no partial state leaked in.
        assert await started_core.get_plugin("live_name") is live_before
        assert len(await started_core._registry.all()) == 1

    @pytest.mark.asyncio
    async def test_error_on_conflict_candidate_vs_candidate_raises_plan_error(
        self, conflict_core: Core
    ):
        clash_a = _source("clash_a", provides=frozenset({"clash_cap"}))
        clash_b = _source("clash_b", provides=frozenset({"clash_cap"}))

        with pytest.raises(BatchLoadError) as excinfo:
            await conflict_core.load_plugins([(clash_a, None), (clash_b, None)])

        err = excinfo.value
        assert err.phase == "plan"
        assert err.installed == ()
        assert err.failed is None
        assert isinstance(err.__cause__, PluginError)
        assert await conflict_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_error_on_conflict_candidate_vs_live_raises_plan_error(self, conflict_core: Core):
        live_provider = _source("live_provider", provides=frozenset({"clash_cap"}))
        new_provider = _source("new_provider", provides=frozenset({"clash_cap"}))

        await conflict_core.load_plugin(live_provider)

        with pytest.raises(BatchLoadError) as excinfo:
            await conflict_core.load_plugins([(new_provider, None)])

        err = excinfo.value
        assert err.phase == "plan"
        assert err.installed == ()
        assert err.failed == "new_provider"
        assert isinstance(err.__cause__, PluginError)
        assert len(await conflict_core._registry.all()) == 1

    @pytest.mark.asyncio
    async def test_materialize_failure_with_origin_names_failed_origin(self, started_core: Core):
        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins([(BROKEN_SYNTAX_SRC, "/tmp/broken_plugin.py")])

        err = excinfo.value
        assert err.phase == "plan"
        assert err.installed == ()
        assert err.failed == "/tmp/broken_plugin.py"
        assert isinstance(err.__cause__, PluginError)
        assert "compile" in str(err.__cause__).lower()
        assert await started_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_materialize_failure_without_origin_uses_positional_sentinel(
        self, started_core: Core
    ):
        """An anonymous source gets a ``sources[N]`` handle, not an ambiguous None."""
        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins([(STORAGE_SRC, None), (BROKEN_SYNTAX_SRC, None)])

        err = excinfo.value
        assert err.phase == "plan"
        assert err.installed == ()
        assert err.failed == "sources[1]"
        assert await started_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_constructor_raising_non_plugin_error_is_enveloped(self, started_core: Core):
        """A plugin __init__ raising e.g. ValueError is a plan-phase fault, not a raw leak."""
        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins([(CONSTRUCTOR_RAISES_SRC, "/tmp/exploding.py")])

        err = excinfo.value
        assert err.phase == "plan"
        assert err.installed == ()
        assert err.failed == "/tmp/exploding.py"
        assert isinstance(err.__cause__, ValueError)
        assert "boom from constructor" in str(err.__cause__)
        assert await started_core._registry.all() == {}


class TestCommitPhaseErrors:
    """RFC 0008 §10 bullet 3: an on_start() failure mid-batch reports the live prefix."""

    @pytest.mark.asyncio
    async def test_commit_failure_reports_installed_prefix_and_failed_name(
        self, started_core: Core
    ):
        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins(BOOM_SOURCES)

        err = excinfo.value
        assert err.phase == "commit"
        assert err.installed == ("storage", "index")
        assert err.failed == "boom"
        assert isinstance(err.__cause__, RuntimeError)
        assert str(err.__cause__) == "boom on start"

        assert await started_core.get_plugin("boom") is None
        assert await started_core.get_plugin("storage") is not None
        assert await started_core.get_plugin("index") is not None


class TestHostRollbackRecipes:
    """RFC 0008 §4.8: rollback-or-keep is host policy, built entirely on public API."""

    @pytest.mark.asyncio
    async def test_keep_whole_graph_or_nothing_leaves_core_empty(self, started_core: Core):
        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins(BOOM_SOURCES)

        err = excinfo.value
        for name in reversed(err.installed):
            await started_core.unregister_plugin(name)

        assert await started_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_keep_prefix_leaves_installed_plugins_live(self, started_core: Core):
        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins(BOOM_SOURCES)

        err = excinfo.value
        assert err.phase == "commit"

        # "Boot whatever resolves, keep the prefix" — nothing to unwind.
        assert set(err.installed) == {"storage", "index"}
        assert await started_core.get_plugin("storage") is not None
        assert await started_core.get_plugin("index") is not None


class TestBatchVsSequentialEquivalence:
    """RFC 0008 §10 bullet 5: batch loading is faithful to single-load semantics."""

    @pytest.mark.asyncio
    async def test_batch_load_equivalent_to_sequential_load_in_returned_order(
        self, started_core: Core
    ):
        order = await started_core.load_plugins(shuffled_graph_sources(seed=7))

        core_b = Core()
        await core_b.start()
        try:
            for name in order:
                await core_b.load_plugin(GRAPH_SOURCES[name])

            names_a = await _live_names(started_core)
            names_b = await _live_names(core_b)
            assert names_a == names_b == set(GRAPH_NAMES)

            caps_a = set(started_core._capability_system._capabilities)
            caps_b = set(core_b._capability_system._capabilities)
            assert caps_a == caps_b
        finally:
            if core_b.state is CoreState.RUNNING:
                await core_b.stop()


class TestNonRunningCoreRejection:
    """load_plugins requires RUNNING, exactly like load_plugin."""

    @pytest.mark.asyncio
    async def test_load_plugins_on_initialized_core_raises_core_error(self, clean_core: Core):
        assert clean_core.state is CoreState.INITIALIZED

        with pytest.raises(CoreError) as excinfo:
            await clean_core.load_plugins([(STORAGE_SRC, None)])

        assert not isinstance(excinfo.value, BatchLoadError)
        assert clean_core.state is CoreState.INITIALIZED
        assert await clean_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_load_plugins_on_stopped_core_raises_core_error(self, clean_core: Core):
        await clean_core.start()
        await clean_core.stop()
        assert clean_core.state is CoreState.STOPPED

        with pytest.raises(CoreError) as excinfo:
            await clean_core.load_plugins([(STORAGE_SRC, None)])

        assert not isinstance(excinfo.value, BatchLoadError)
        assert await clean_core._registry.all() == {}


class TestBatchLoadErrorShape:
    """Unit-level round trip of the BatchLoadError envelope itself (RFC 0008 §4.6)."""

    def test_message_without_installed_or_failed(self):
        cause = ValueError("underlying fault")
        err = BatchLoadError(phase="plan", cause=cause)

        assert err.phase == "plan"
        assert err.cause is cause
        assert err.installed == ()
        assert err.failed is None
        assert str(err) == "Batch load failed during plan: underlying fault"

    def test_message_with_installed_and_failed(self):
        cause = RuntimeError("boom")
        err = BatchLoadError(phase="commit", cause=cause, installed=("a", "b"), failed="c")

        assert str(err) == "Batch load failed during commit at 'c': boom (installed so far: a, b)"

    def test_is_catchable_as_plugin_error_and_core_error(self):
        err = BatchLoadError(phase="plan", cause=ValueError("x"))

        assert isinstance(err, PluginError)
        assert isinstance(err, CoreError)
        with pytest.raises(PluginError):
            raise err

    def test_raise_from_cause_chains_cause_attribute_to_dunder_cause(self):
        cause = ValueError("underlying")
        err = BatchLoadError(phase="commit", cause=cause, installed=("a",), failed="b")

        with pytest.raises(BatchLoadError) as excinfo:
            raise err from cause

        assert excinfo.value.__cause__ is excinfo.value.cause
        assert excinfo.value.__cause__ is cause


class TestStaticFaultsFailInPlanPhase:
    """RFC 0008 §4.8: every statically-decidable fault is front-loaded to plan.

    Contract failures (H-002) and max_plugins overflow (M-001) are knowable
    without committing anything, so they must fail in "plan" with an empty
    registry — not partway through the commit loop with a partial install.
    """

    @pytest.mark.asyncio
    async def test_contract_failure_candidate_fails_in_plan_phase(self, started_core: Core):
        """A provider missing its declared protocol method plan-fails; nothing commits."""
        with pytest.raises(BatchLoadError) as excinfo:
            await started_core.load_plugins([(STORAGE_SRC, None), (CONTRACT_FAILURE_SRC, None)])

        err = excinfo.value
        assert err.phase == "plan"
        assert err.installed == ()
        assert err.failed == "bad_greeter"
        assert isinstance(err.__cause__, PluginError)
        assert "does not implement" in str(err.__cause__)
        # Zero partial state: the well-formed sibling never committed either.
        assert await started_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_max_plugins_overflow_fails_in_plan_phase(self):
        """A batch that would exceed max_plugins plan-fails, not mid-commit."""
        core = Core(max_plugins=1)
        await core.start()
        try:
            with pytest.raises(BatchLoadError) as excinfo:
                await core.load_plugins([(STORAGE_SRC, None), (AUTH_SRC, None)])

            err = excinfo.value
            assert err.phase == "plan"
            assert err.installed == ()
            assert err.failed is None
            assert isinstance(err.__cause__, PluginError)
            assert "max_plugins" in str(err.__cause__)
            assert await core._registry.all() == {}
        finally:
            if core.state is CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    async def test_max_plugins_overflow_counts_already_live_plugins(self):
        """The ceiling check sums live + in-batch, so one live + one batch overflows a cap of 1."""
        core = Core(max_plugins=1)
        await core.start()
        try:
            await core.load_plugin(STORAGE_SRC)

            with pytest.raises(BatchLoadError) as excinfo:
                await core.load_plugins([(AUTH_SRC, None)])

            assert excinfo.value.phase == "plan"
            assert await core.get_plugin("storage") is not None
            assert await core.get_plugin("auth") is None
        finally:
            if core.state is CoreState.RUNNING:
                await core.stop()


class TestLiveAndInBatchProviderOverlap:
    """L-002: a capability satisfied by BOTH a live and an in-batch provider is fine.

    missing_requirements() skips live-satisfied capabilities, so no ordering
    edge is drawn to the in-batch provider and no spurious cycle appears — the
    consumer admits because the live provider already satisfies it.
    """

    @pytest.mark.asyncio
    async def test_consumer_admits_when_capability_is_both_live_and_in_batch(
        self, started_core: Core
    ):
        await started_core.load_plugin(STORAGE_SRC)  # live provider of "storage"

        storage_dup = _source("storage_dup", provides=frozenset({"storage"}))
        consumer = _source("index", provides=frozenset({"index"}), requires=frozenset({"storage"}))

        result = await started_core.load_plugins([(consumer, None), (storage_dup, None)])

        assert set(result) == {"index", "storage_dup"}
        assert await started_core.get_plugin("index") is not None
        assert await started_core.get_plugin("storage_dup") is not None


class TestCommitOrderDeterminism:
    """H-001: the committed order is a pure function of the sources, not PYTHONHASHSEED."""

    @pytest.mark.asyncio
    async def test_same_source_order_yields_identical_commit_order(self):
        """Loading the same source order into fresh cores gives byte-identical results."""
        sources = shuffled_graph_sources(seed=4)

        orders = []
        for _ in range(3):
            core = Core()
            await core.start()
            try:
                orders.append(await core.load_plugins(sources))
            finally:
                await core.stop()

        assert orders[0] == orders[1] == orders[2]
        assert_topological_order(orders[0])
