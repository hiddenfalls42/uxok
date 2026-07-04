"""Tests for Core.try_load_plugins() best-effort batch loading (RFC 0010).

The best-effort sibling of load_plugins: instead of refusing the whole batch on
the first statically decidable fault, it commits the maximal loadable subgraph
and returns a BatchLoadReport describing every committed and skipped candidate.
It never raises BatchLoadError and never unregisters an already-live plugin.

Source-string helpers and the canonical five-plugin graph are shared with
tests/test_load_plugins.py (RFC 0008), which stays byte-untouched — the two
verbs are backed by one planner, so the atomic suite is the equivalence anchor.
"""

import pytest

from tests.test_load_plugins import (
    BROKEN_SYNTAX_SRC,
    CONSTRUCTOR_RAISES_SRC,
    CONTRACT_FAILURE_SRC,
    GRAPH_NAMES,
    GRAPH_SOURCES,
    INDEX_SRC,
    STORAGE_SRC,
    _source,
    assert_topological_order,
    conflict_core,  # noqa: F401 — re-exported so this module can request the fixture
    shuffled_graph_sources,
)
from uxok import Core
from uxok.errors import CoreError
from uxok.protocols import BatchLoadReport, CoreState, SkippedSource


async def _live_names(core: Core) -> set[str]:
    return {plugin.metadata.name for plugin in (await core._registry.all()).values()}


def _by_name(report: BatchLoadReport) -> dict[str, SkippedSource]:
    return {s.name: s for s in report.skipped}


def _reasons(report: BatchLoadReport) -> dict[str | None, str]:
    return {s.name: s.reason for s in report.skipped}


class TestEquivalenceWithAtomicVerb:
    """RFC 0010 §9: a fault-free batch loads exactly what load_plugins would."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("seed", [1, 2, 3, 7])
    async def test_clean_batch_matches_load_plugins(self, seed: int) -> None:
        sources = shuffled_graph_sources(seed)

        atomic = Core()
        await atomic.start()
        try:
            atomic_names = await atomic.load_plugins(sources)
        finally:
            await atomic.stop()

        best = Core()
        await best.start()
        try:
            report = await best.try_load_plugins(sources)
        finally:
            await best.stop()

        assert report.skipped == ()
        assert tuple(name for name, _ in report.loaded) == atomic_names

    @pytest.mark.asyncio
    async def test_clean_batch_commits_in_topological_order(self, started_core: Core) -> None:
        report = await started_core.try_load_plugins(shuffled_graph_sources(5))
        order = tuple(name for name, _ in report.loaded)
        assert set(order) == set(GRAPH_NAMES)
        assert_topological_order(order)
        assert await _live_names(started_core) == set(GRAPH_NAMES)

    @pytest.mark.asyncio
    async def test_origins_round_trip(self, started_core: Core) -> None:
        sources = [(GRAPH_SOURCES[name], f"{name}.py") for name in GRAPH_NAMES]
        report = await started_core.try_load_plugins(sources)
        loaded = dict(report.loaded)
        for name in GRAPH_NAMES:
            assert loaded[name] == f"{name}.py"

    @pytest.mark.asyncio
    async def test_empty_sources(self, started_core: Core) -> None:
        report = await started_core.try_load_plugins([])
        assert report == BatchLoadReport((), ())
        assert await _live_names(started_core) == set()


class TestMissingCapabilityPruning:
    """A ghost capability prunes its requirer and everything downstream; the rest live."""

    @pytest.mark.asyncio
    async def test_ghost_capability_skips_only_the_chain(self, started_core: Core) -> None:
        ghost = _source("needs_ghost", requires=frozenset({"ghost_cap"}))
        lonely = _source("lonely")
        report = await started_core.try_load_plugins([(ghost, "ghost.py"), (lonely, "lonely.py")])
        assert _reasons(report) == {"needs_ghost": "missing_capability"}
        assert [name for name, _ in report.loaded] == ["lonely"]
        assert await _live_names(started_core) == {"lonely"}

    @pytest.mark.asyncio
    async def test_transitive_dependents_are_pruned(self, started_core: Core) -> None:
        # a -> b -> c chain, where 'a' needs a ghost capability.
        a = _source("a", provides=frozenset({"cap_a"}), requires=frozenset({"ghost"}))
        b = _source("b", provides=frozenset({"cap_b"}), requires=frozenset({"cap_a"}))
        c = _source("c", requires=frozenset({"cap_b"}))
        independent = _source("independent")
        report = await started_core.try_load_plugins(
            [(a, "a"), (b, "b"), (c, "c"), (independent, "i")]
        )
        assert _reasons(report) == {
            "a": "missing_capability",
            "b": "dependent_of_skipped",
            "c": "dependent_of_skipped",
        }
        assert [name for name, _ in report.loaded] == ["independent"]

    @pytest.mark.asyncio
    async def test_prune_cause_names_the_blockers(self, started_core: Core) -> None:
        a = _source("prov", provides=frozenset({"cap_a"}), requires=frozenset({"ghost"}))
        b = _source("consumer", requires=frozenset({"cap_a"}))
        report = await started_core.try_load_plugins([(a, "a"), (b, "b")])
        consumer_skip = _by_name(report)["consumer"]
        assert consumer_skip.reason == "dependent_of_skipped"
        assert "prov" in str(consumer_skip.cause)


class TestCyclePruning:
    """Cycle members skip cycle_member; a dependent outside the cycle prunes."""

    @pytest.mark.asyncio
    async def test_cycle_members_and_outside_dependent(self, started_core: Core) -> None:
        cyc_a = _source("cyc_a", provides=frozenset({"ca"}), requires=frozenset({"cb"}))
        cyc_b = _source("cyc_b", provides=frozenset({"cb"}), requires=frozenset({"ca"}))
        outside = _source("outside", requires=frozenset({"ca"}))
        survivor = _source("survivor")
        report = await started_core.try_load_plugins(
            [(cyc_a, "a"), (cyc_b, "b"), (outside, "o"), (survivor, "s")]
        )
        assert _reasons(report) == {
            "cyc_a": "cycle_member",
            "cyc_b": "cycle_member",
            "outside": "dependent_of_skipped",
        }
        assert [name for name, _ in report.loaded] == ["survivor"]


class TestDuplicateNameClaimants:
    """Both claimants of a duplicate name skip; a consumer of their cap prunes."""

    @pytest.mark.asyncio
    async def test_both_claimants_skip_and_consumer_prunes(self, started_core: Core) -> None:
        first = _source("dup", provides=frozenset({"dup_cap_a"}))
        second = _source("dup", provides=frozenset({"dup_cap_a"}))
        consumer = _source("consumer", requires=frozenset({"dup_cap_a"}))
        report = await started_core.try_load_plugins(
            [(first, "first"), (second, "second"), (consumer, "c")]
        )
        reasons = [(s.name, s.reason, s.origin) for s in report.skipped]
        assert ("dup", "duplicate_name", "first") in reasons
        assert ("dup", "duplicate_name", "second") in reasons
        assert ("consumer", "dependent_of_skipped", "c") in reasons
        assert report.loaded == ()
        assert await _live_names(started_core) == set()


class TestDuplicateProvider:
    """Under error_on_conflict all in-batch claimants of a colliding cap skip."""

    @pytest.mark.asyncio
    async def test_in_batch_collision_skips_all_claimants(
        self,
        conflict_core: Core,  # noqa: F811
    ) -> None:
        clash_a = _source("clash_a", provides=frozenset({"clash_cap"}))
        clash_b = _source("clash_b", provides=frozenset({"clash_cap"}))
        report = await conflict_core.try_load_plugins([(clash_a, "a"), (clash_b, "b")])
        assert _reasons(report) == {
            "clash_a": "duplicate_provider",
            "clash_b": "duplicate_provider",
        }
        assert report.loaded == ()

    @pytest.mark.asyncio
    async def test_live_collision_skips_candidate_only(
        self,
        conflict_core: Core,  # noqa: F811
    ) -> None:
        live_provider = _source("live_provider", provides=frozenset({"clash_cap"}))
        await conflict_core.load_plugin(live_provider)
        new_provider = _source("new_provider", provides=frozenset({"clash_cap"}))
        report = await conflict_core.try_load_plugins([(new_provider, "new.py")])
        assert _reasons(report) == {"new_provider": "duplicate_provider"}
        assert "live_provider" in await _live_names(conflict_core)

    @pytest.mark.asyncio
    async def test_default_policy_admits_duplicate_providers(self, started_core: Core) -> None:
        # Default collision policy tolerates multi-providers — nothing is skipped.
        a = _source("prov_a", provides=frozenset({"shared"}))
        b = _source("prov_b", provides=frozenset({"shared"}))
        report = await started_core.try_load_plugins([(a, "a"), (b, "b")])
        assert report.skipped == ()
        assert {name for name, _ in report.loaded} == {"prov_a", "prov_b"}


class TestMaxPluginsTailCut:
    """Overflow trims the topological tail; live plugins count toward the ceiling."""

    @pytest.mark.asyncio
    async def test_tail_beyond_capacity_is_skipped(self) -> None:
        core = Core(max_plugins=2)
        await core.start()
        try:
            report = await core.try_load_plugins(
                [(STORAGE_SRC, "s"), (INDEX_SRC, "i"), (_source("extra"), "e")]
            )
            assert len(report.loaded) == 2
            assert len(report.skipped) == 1
            assert report.skipped[0].reason == "max_plugins"
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_capacity_counts_live_plugins(self) -> None:
        core = Core(max_plugins=2)
        await core.start()
        try:
            await core.load_plugin(_source("already_live"))
            report = await core.try_load_plugins([(STORAGE_SRC, "s"), (INDEX_SRC, "i")])
            assert len(report.loaded) == 1  # only one slot left
            assert [s.reason for s in report.skipped] == ["max_plugins"]
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_dependent_of_cut_provider_does_not_commit(self) -> None:
        core = Core(max_plugins=1)
        await core.start()
        try:
            # storage must precede index; capacity 1 means index (the consumer)
            # cannot outrank a cut provider.
            report = await core.try_load_plugins([(STORAGE_SRC, "s"), (INDEX_SRC, "i")])
            assert len(report.loaded) == 1
            loaded_names = {name for name, _ in report.loaded}
            # index requires storage, so index can never be the sole commit.
            assert "index" not in loaded_names or "storage" in loaded_names
        finally:
            await core.stop()


class TestCommitPhaseOnStartError:
    """A candidate whose on_start raises is on_start_error; its dependents prune."""

    @pytest.mark.asyncio
    async def test_on_start_error_and_dependent_pruning(self, started_core: Core) -> None:
        boom = _source(
            "boom",
            provides=frozenset({"boom_cap"}),
            body="\n    async def on_start(self):\n        raise RuntimeError('boom')\n",
        )
        dependent = _source("needs_boom", requires=frozenset({"boom_cap"}))
        independent = _source("independent")
        report = await started_core.try_load_plugins(
            [(boom, "boom.py"), (dependent, "dep.py"), (independent, "ind.py")]
        )
        assert _reasons(report) == {
            "boom": "on_start_error",
            "needs_boom": "dependent_of_skipped",
        }
        assert [name for name, _ in report.loaded] == ["independent"]
        # Self-cleaning registration: the raiser never enters the registry.
        assert await _live_names(started_core) == {"independent"}

    @pytest.mark.asyncio
    async def test_on_start_error_cause_is_the_exception(self, started_core: Core) -> None:
        boom = _source(
            "boom",
            body="\n    async def on_start(self):\n        raise RuntimeError('kaboom')\n",
        )
        report = await started_core.try_load_plugins([(boom, "boom.py")])
        skip = _by_name(report)["boom"]
        assert skip.reason == "on_start_error"
        assert isinstance(skip.cause, RuntimeError)
        assert "kaboom" in str(skip.cause)

    @pytest.mark.asyncio
    async def test_earlier_commits_are_not_unwound(self, started_core: Core) -> None:
        # 'storage' commits first, then a raiser — storage must stay live.
        boom = _source(
            "boom",
            body="\n    async def on_start(self):\n        raise RuntimeError('boom')\n",
        )
        report = await started_core.try_load_plugins([(STORAGE_SRC, "s"), (boom, "boom.py")])
        assert "storage" in {name for name, _ in report.loaded}
        assert "storage" in await _live_names(started_core)


class TestMaterializeErrorSkips:
    """A source that fails to materialize is skipped with name=None; siblings load."""

    @pytest.mark.asyncio
    async def test_broken_syntax_skips_only_that_source(self, started_core: Core) -> None:
        report = await started_core.try_load_plugins(
            [(BROKEN_SYNTAX_SRC, "broken.py"), (STORAGE_SRC, "storage.py")]
        )
        assert len(report.skipped) == 1
        skip = report.skipped[0]
        assert skip.name is None
        assert skip.origin == "broken.py"
        assert skip.reason == "materialize_error"
        assert skip.cause is not None
        assert [name for name, _ in report.loaded] == ["storage"]

    @pytest.mark.asyncio
    async def test_constructor_raises_is_materialize_error(self, started_core: Core) -> None:
        report = await started_core.try_load_plugins(
            [(CONSTRUCTOR_RAISES_SRC, None), (STORAGE_SRC, None)]
        )
        assert report.skipped[0].reason == "materialize_error"
        assert report.skipped[0].origin is None  # anonymous source, no sentinel
        assert report.skipped[0].name is None
        assert [name for name, _ in report.loaded] == ["storage"]


class TestLiveNameCollision:
    """A candidate whose name is already live skips; the live plugin is untouched."""

    @pytest.mark.asyncio
    async def test_live_name_collision(self, started_core: Core) -> None:
        await started_core.load_plugin(_source("resident", provides=frozenset({"r"})))
        collider = _source("resident", provides=frozenset({"other"}))
        fresh = _source("fresh")
        report = await started_core.try_load_plugins(
            [(collider, "collider.py"), (fresh, "fresh.py")]
        )
        assert _reasons(report) == {"resident": "live_name_collision"}
        assert [name for name, _ in report.loaded] == ["fresh"]
        assert "resident" in await _live_names(started_core)


class TestContractFailure:
    """A provider that violates its typed capability's protocol skips."""

    @pytest.mark.asyncio
    async def test_contract_failure_skips_candidate(self, started_core: Core) -> None:
        report = await started_core.try_load_plugins(
            [(CONTRACT_FAILURE_SRC, "bad.py"), (STORAGE_SRC, "storage.py")]
        )
        assert _reasons(report) == {"bad_greeter": "contract_failure"}
        assert [name for name, _ in report.loaded] == ["storage"]


class TestReportShapeAndPartition:
    """The report is frozen+slots, and loaded plus skipped partitions the input."""

    def test_dataclasses_are_frozen_and_slotted(self) -> None:
        assert SkippedSource.__dataclass_params__.frozen
        assert BatchLoadReport.__dataclass_params__.frozen
        assert hasattr(SkippedSource, "__slots__")
        assert hasattr(BatchLoadReport, "__slots__")
        with pytest.raises((AttributeError, TypeError)):
            SkippedSource(None, None, "x", None).reason = "y"  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_every_input_index_lands_in_exactly_one_bucket(self, started_core: Core) -> None:
        # A batch spanning several fault families plus successes.
        ghost = _source("ghost_needer", requires=frozenset({"ghost"}))
        report = await started_core.try_load_plugins(
            [
                (STORAGE_SRC, "s"),
                (BROKEN_SYNTAX_SRC, "broken"),
                (ghost, "ghost"),
                (_source("plain"), "plain"),
            ]
        )
        loaded_count = len(report.loaded)
        skipped_count = len(report.skipped)
        assert loaded_count + skipped_count == 4

    @pytest.mark.asyncio
    async def test_skipped_is_input_ordered(self, started_core: Core) -> None:
        # Three broken sources — skips must appear in submission order.
        report = await started_core.try_load_plugins(
            [
                (BROKEN_SYNTAX_SRC, "first"),
                (STORAGE_SRC, "ok"),
                (BROKEN_SYNTAX_SRC, "third"),
            ]
        )
        assert [s.origin for s in report.skipped] == ["first", "third"]


class TestDeterminism:
    """Identical inputs yield identical reports across fresh cores."""

    @pytest.mark.asyncio
    async def test_reports_are_deterministic(self) -> None:
        ghost = _source("g", requires=frozenset({"ghost"}))
        sources = [(GRAPH_SOURCES["storage"], "s"), (ghost, "g"), (_source("p"), "p")]

        def project(report: BatchLoadReport) -> tuple:
            return (
                report.loaded,
                tuple((s.origin, s.name, s.reason) for s in report.skipped),
            )

        results = []
        for _ in range(3):
            core = Core()
            await core.start()
            try:
                results.append(project(await core.try_load_plugins(sources)))
            finally:
                await core.stop()

        assert results[0] == results[1] == results[2]


class TestNonRunningCore:
    """Batch loading requires a RUNNING core; the registry stays empty otherwise."""

    @pytest.mark.asyncio
    async def test_raises_core_error_when_not_started(self, clean_core: Core) -> None:
        assert clean_core.state is not CoreState.RUNNING
        with pytest.raises(CoreError):
            await clean_core.try_load_plugins([(STORAGE_SRC, "s")])
        assert await _live_names(clean_core) == set()
