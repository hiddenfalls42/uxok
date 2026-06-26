"""Tests for the event subscription manager: subscribe, unsubscribe, wildcards, caching."""

from __future__ import annotations

from uuid import uuid4

from uxok.events._subscriptions import _EMPTY_SUBSCRIBERS, SubscriptionManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Owner:
    """Tiny class whose bound methods carry __self__ for owner-derivation tests."""

    def handle(self, e: object) -> None:
        pass

    def handle2(self, e: object) -> None:
        pass


# ---------------------------------------------------------------------------
# TestSubscribe
# ---------------------------------------------------------------------------


class TestSubscribe:
    def test_subscribe_and_get_subscribers_returns_callback(self) -> None:
        mgr = SubscriptionManager()
        cb = lambda e: None
        mgr.subscribe("test.event", cb)
        subs = mgr.get_subscribers("test.event")
        assert len(subs) == 1
        assert subs[0][0] is cb

    def test_subscribe_with_plugin_id_records_plugin_id(self) -> None:
        mgr = SubscriptionManager()
        pid = uuid4()
        cb = lambda e: None
        mgr.subscribe("test.event", cb, plugin_id=pid)
        subs = mgr.get_subscribers("test.event")
        assert len(subs) == 1
        assert subs[0][0] is cb
        assert subs[0][2] == pid

    def test_subscribe_returns_unique_ids(self) -> None:
        mgr = SubscriptionManager()
        id1 = mgr.subscribe("a", lambda e: None)
        id2 = mgr.subscribe("b", lambda e: None)
        assert id1 != id2

    def test_sync_callback_is_async_false(self) -> None:
        mgr = SubscriptionManager()
        cb = lambda e: None
        mgr.subscribe("ev", cb)
        subs = mgr.get_subscribers("ev")
        assert subs[0][1] is False

    def test_async_callback_is_async_true(self) -> None:
        mgr = SubscriptionManager()

        async def acb(e: object) -> None:
            pass

        mgr.subscribe("ev", acb)
        subs = mgr.get_subscribers("ev")
        assert subs[0][1] is True

    def test_owner_derived_from_bound_method(self) -> None:
        mgr = SubscriptionManager()
        obj = _Owner()
        mgr.subscribe("ev", obj.handle)
        # owner stored in subscription record (index 4)
        record = next(iter(mgr._subscriptions_by_id.values()))
        assert record[4] is obj

    def test_explicit_owner_overrides_bound_method_self(self) -> None:
        mgr = SubscriptionManager()
        obj = _Owner()
        other_owner = object()
        mgr.subscribe("ev", obj.handle, owner=other_owner)
        record = next(iter(mgr._subscriptions_by_id.values()))
        assert record[4] is other_owner

    def test_owner_none_for_plain_lambda(self) -> None:
        mgr = SubscriptionManager()
        cb = lambda e: None
        mgr.subscribe("ev", cb)
        record = next(iter(mgr._subscriptions_by_id.values()))
        assert record[4] is None


# ---------------------------------------------------------------------------
# TestUnsubscribe
# ---------------------------------------------------------------------------


class TestUnsubscribe:
    def test_unsubscribe_by_id_removes_callback(self) -> None:
        mgr = SubscriptionManager()
        cb = lambda e: None
        sub_id = mgr.subscribe("test.event", cb)
        mgr.unsubscribe(sub_id)
        assert mgr.get_subscribers("test.event") == ()

    def test_unsubscribe_nonexistent_id_is_safe_noop(self) -> None:
        mgr = SubscriptionManager()
        mgr.unsubscribe("nonexistent")  # must not raise

    def test_unsubscribe_plugin(self) -> None:
        mgr = SubscriptionManager()
        pid = uuid4()
        mgr.subscribe("a", lambda e: None, plugin_id=pid)
        mgr.subscribe("b", lambda e: None, plugin_id=pid)
        mgr.unsubscribe_plugin(pid)
        assert mgr.get_subscribers("a") == ()
        assert mgr.get_subscribers("b") == ()

    def test_duplicate_callback_one_unsubscribe_keeps_other(self) -> None:
        """subscribe same callback object twice → two IDs; remove one, one remains."""
        mgr = SubscriptionManager()
        cb = lambda e: None
        sub1 = mgr.subscribe("dup.event", cb)
        sub2 = mgr.subscribe("dup.event", cb)

        mgr.unsubscribe(sub1)

        subs = mgr.get_subscribers("dup.event")
        assert len(subs) == 1
        assert subs[0][0] is cb
        assert len(mgr._subscriptions_by_id) == 1

    def test_duplicate_callback_remove_second_leaves_empty(self) -> None:
        mgr = SubscriptionManager()
        cb = lambda e: None
        sub1 = mgr.subscribe("dup.event", cb)
        sub2 = mgr.subscribe("dup.event", cb)

        mgr.unsubscribe(sub1)
        mgr.unsubscribe(sub2)

        assert mgr.get_subscribers("dup.event") == ()
        assert mgr._subscriptions_by_id == {}


# ---------------------------------------------------------------------------
# TestUnsubscribeOwner
# ---------------------------------------------------------------------------


class TestUnsubscribeOwner:
    def test_removes_all_subs_for_owner_and_returns_count(self) -> None:
        """unsubscribe_owner drains all subs by instance identity, returns count."""
        mgr = SubscriptionManager()
        owner = _Owner()
        pid = uuid4()

        # Two bound-method subs whose owner is derived automatically from __self__
        mgr.subscribe("ev.a", owner.handle, plugin_id=pid)
        mgr.subscribe("ev.b", owner.handle2, plugin_id=pid)

        # One sub with explicit owner= kwarg
        cb = lambda e: None
        mgr.subscribe("ev.c", cb, owner=owner)

        removed = mgr.unsubscribe_owner(owner)

        assert removed == 3
        assert mgr.get_subscribers("ev.a") == ()
        assert mgr.get_subscribers("ev.b") == ()
        assert mgr.get_subscribers("ev.c") == ()

    def test_different_owners_subs_survive(self) -> None:
        mgr = SubscriptionManager()
        owner_a = _Owner()
        owner_b = _Owner()

        cb_a = lambda e: None
        cb_b = lambda e: None
        mgr.subscribe("ev", cb_a, owner=owner_a)
        mgr.subscribe("ev", cb_b, owner=owner_b)

        removed = mgr.unsubscribe_owner(owner_a)

        assert removed == 1
        subs = mgr.get_subscribers("ev")
        assert len(subs) == 1
        assert subs[0][0] is cb_b

    def test_unsubscribe_owner_returns_zero_when_no_match(self) -> None:
        mgr = SubscriptionManager()
        phantom = object()
        removed = mgr.unsubscribe_owner(phantom)
        assert removed == 0

    def test_unsubscribe_owner_updates_wildcard_flag(self) -> None:
        """Removing an owner whose only subs are wildcards must flip the flag."""
        mgr = SubscriptionManager()
        owner = _Owner()
        mgr.subscribe("data.*", owner.handle, owner=owner)
        assert mgr._has_wildcard_patterns is True

        mgr.unsubscribe_owner(owner)

        assert mgr._has_wildcard_patterns is False


# ---------------------------------------------------------------------------
# TestUnsubscribePlugin
# ---------------------------------------------------------------------------


class TestUnsubscribePlugin:
    def test_removes_only_the_matching_plugins_subscriptions(self) -> None:
        mgr = SubscriptionManager()
        pid = uuid4()
        other = uuid4()
        cb_target = lambda e: None
        cb_other = lambda e: None
        mgr.subscribe("ev", cb_target, plugin_id=pid)
        mgr.subscribe("ev", cb_other, plugin_id=other)

        mgr.unsubscribe_plugin(pid)

        subs = mgr.get_subscribers("ev")
        assert len(subs) == 1
        assert subs[0][0] is cb_other

    def test_unknown_plugin_id_is_safe_noop(self) -> None:
        # unsubscribe_plugin is UUID-only; an id that matches nothing (including
        # the string form of a real id, which is never stored) removes nothing.
        mgr = SubscriptionManager()
        pid = uuid4()
        cb = lambda e: None
        mgr.subscribe("ev", cb, plugin_id=pid)

        mgr.unsubscribe_plugin(uuid4())  # different UUID — no match

        subs = mgr.get_subscribers("ev")
        assert len(subs) == 1
        assert subs[0][0] is cb

    def test_string_form_of_a_real_id_does_not_match(self) -> None:
        # Guards the constitutional contract: plugin_id is a PluginId (UUID).
        # The string form of a stored UUID must NOT remove it (no coercion).
        mgr = SubscriptionManager()
        pid = uuid4()
        mgr.subscribe("ev", lambda e: None, plugin_id=pid)

        mgr.unsubscribe_plugin(str(pid))  # type: ignore[arg-type]

        assert len(mgr.get_subscribers("ev")) == 1


# ---------------------------------------------------------------------------
# TestWildcardMatching
# ---------------------------------------------------------------------------


class TestWildcardMatching:
    def test_wildcard_pattern_returns_correct_callback(self) -> None:
        mgr = SubscriptionManager()
        cb = lambda e: None
        mgr.subscribe("user.*", cb)
        subs = mgr.get_subscribers("user.created")
        assert len(subs) == 1
        assert subs[0][0] is cb

    def test_non_matching_wildcard_excluded(self) -> None:
        mgr = SubscriptionManager()
        cb_match = lambda e: None
        cb_no_match = lambda e: None
        mgr.subscribe("user.*", cb_match)
        mgr.subscribe("order.*", cb_no_match)
        subs = mgr.get_subscribers("user.created")
        assert len(subs) == 1
        assert subs[0][0] is cb_match

    def test_exact_plus_wildcard_coexistence_order(self) -> None:
        """Exact-match subscribers appear before wildcard subscribers."""
        mgr = SubscriptionManager()
        cb_exact = lambda e: None
        cb_wild = lambda e: None
        mgr.subscribe("a.b", cb_exact)
        mgr.subscribe("a.*", cb_wild)

        subs = mgr.get_subscribers("a.b")

        assert len(subs) == 2
        # Exact comes first, wildcard second — pin current ordering
        assert subs[0][0] is cb_exact
        assert subs[1][0] is cb_wild

    def test_no_match_returns_empty(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("other.event", lambda e: None)
        assert len(mgr.get_subscribers("no.match")) == 0

    def test_no_wildcards_skips_pattern_matching(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("exact.event", lambda e: None)
        assert mgr._has_wildcard_patterns is False
        assert len(mgr.get_subscribers("exact.event")) == 1
        assert len(mgr.get_subscribers("other.event")) == 0

    def test_only_wildcard_no_exact_returns_correct_callback(self) -> None:
        mgr = SubscriptionManager()
        cb = lambda e: None
        mgr.subscribe("data.*", cb)
        subs = mgr.get_subscribers("data.loaded")
        assert len(subs) == 1
        assert subs[0][0] is cb

    def test_pattern_only_empty_path_returns_empty_singleton(self) -> None:
        """No exact match, no wildcards registered → early return of _EMPTY_SUBSCRIBERS."""
        mgr = SubscriptionManager()
        mgr.subscribe("something.else", lambda e: None)
        assert mgr._has_wildcard_patterns is False
        result = mgr.get_subscribers("no.match")
        assert result is _EMPTY_SUBSCRIBERS

    def test_wildcard_no_match_returns_empty_singleton(self) -> None:
        """Wildcards present but none match the name → _EMPTY_SUBSCRIBERS returned."""
        mgr = SubscriptionManager()
        mgr.subscribe("user.*", lambda e: None)
        result = mgr.get_subscribers("order.placed")
        assert result is _EMPTY_SUBSCRIBERS

    def test_question_mark_wildcard_matches_single_char(self) -> None:
        mgr = SubscriptionManager()
        cb = lambda e: None
        mgr.subscribe("ev.?", cb)
        subs = mgr.get_subscribers("ev.x")
        assert len(subs) == 1
        assert subs[0][0] is cb
        assert mgr.get_subscribers("ev.xy") == ()


# ---------------------------------------------------------------------------
# TestWildcardFlagLifecycle
# ---------------------------------------------------------------------------


class TestWildcardFlagLifecycle:
    def test_flag_set_on_wildcard_subscribe(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("user.*", lambda e: None)
        assert mgr._has_wildcard_patterns is True

    def test_flag_resets_on_unsubscribe_last_wildcard(self) -> None:
        mgr = SubscriptionManager()
        sub_id = mgr.subscribe("user.*", lambda e: None)
        assert mgr._has_wildcard_patterns is True

        mgr.unsubscribe(sub_id)
        assert mgr._has_wildcard_patterns is False

    def test_flag_false_means_exact_lookups_skip_pattern_scan(self) -> None:
        """After removing last wildcard, exact-only lookups work and flag is False."""
        mgr = SubscriptionManager()
        cb_exact = lambda e: None
        wild_id = mgr.subscribe("data.*", lambda e: None)
        mgr.subscribe("exact.key", cb_exact)

        mgr.unsubscribe(wild_id)

        assert mgr._has_wildcard_patterns is False
        subs = mgr.get_subscribers("exact.key")
        assert len(subs) == 1
        assert subs[0][0] is cb_exact

    def test_flag_persists_with_remaining_wildcards(self) -> None:
        mgr = SubscriptionManager()
        pid1 = uuid4()
        pid2 = uuid4()

        mgr.subscribe("user.*", lambda e: None, plugin_id=pid1)
        mgr.subscribe("data.*", lambda e: None, plugin_id=pid2)
        assert mgr._has_wildcard_patterns is True

        mgr.unsubscribe_plugin(pid1)
        assert mgr._has_wildcard_patterns is True

        mgr.unsubscribe_plugin(pid2)
        assert mgr._has_wildcard_patterns is False

    def test_flag_resets_on_unsubscribe_plugin(self) -> None:
        mgr = SubscriptionManager()
        pid = uuid4()
        mgr.subscribe("user.*", lambda e: None, plugin_id=pid)
        mgr.subscribe("data.*", lambda e: None, plugin_id=pid)
        assert mgr._has_wildcard_patterns is True

        mgr.unsubscribe_plugin(pid)
        assert mgr._has_wildcard_patterns is False

    def test_flag_resets_on_clear(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("user.*", lambda e: None)
        mgr.clear()
        assert mgr._has_wildcard_patterns is False

    def test_fast_path_restored_after_clear(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("temp.*", lambda e: None)
        mgr.clear()
        assert mgr._has_wildcard_patterns is False

        cb = lambda e: None
        mgr.subscribe("exact.event", cb)
        subs1 = mgr.get_subscribers("exact.event")
        mgr.get_subscribers("exact.event")  # second call hits cache

        assert "exact.event" in mgr._exact_match_cache
        assert len(subs1) == 1
        assert subs1[0][0] is cb


# ---------------------------------------------------------------------------
# TestSubscriptionCaching
# ---------------------------------------------------------------------------


class TestSubscriptionCaching:
    def test_cache_hit_returns_same_tuple(self) -> None:
        # The first lookup caches the exact tuple it returns, so first, second,
        # and third are all the same object — no redundant re-allocation on miss.
        mgr = SubscriptionManager()
        mgr.subscribe("ev", lambda e: None)
        first = mgr.get_subscribers("ev")
        second = mgr.get_subscribers("ev")
        third = mgr.get_subscribers("ev")
        assert first is second  # first lookup already returns the cached object
        assert second is third  # repeated cache hits return the same object
        assert first is mgr._exact_match_cache["ev"]  # it IS the cached tuple

    def test_cache_eviction_bounds_size(self) -> None:
        """Cache never exceeds _max_cache_size entries."""
        mgr = SubscriptionManager()
        mgr._max_cache_size = 3
        # Subscribe to events a, b, c and prime the cache
        for name in ("a", "b", "c"):
            mgr.subscribe(name, lambda e: None)
            mgr.get_subscribers(name)
        assert len(mgr._exact_match_cache) == 3

        mgr.subscribe("d", lambda e: None)
        mgr.get_subscribers("d")
        assert len(mgr._exact_match_cache) <= 3

    def test_cache_eviction_preserves_correctness(self) -> None:
        """An entry evicted from the cache recomputes correctly on the next lookup."""
        mgr = SubscriptionManager()
        mgr._max_cache_size = 3

        callbacks: dict[str, object] = {}
        for name in ("x", "y", "z"):
            cb = lambda e: None
            callbacks[name] = cb
            mgr.subscribe(name, cb)
            mgr.get_subscribers(name)  # prime cache

        # Cache is now full (x, y, z). Adding "w" evicts "x" (oldest).
        cb_w = lambda e: None
        mgr.subscribe("w", cb_w)
        mgr.get_subscribers("w")
        assert "x" not in mgr._exact_match_cache

        # Lookup of "x" must still return the correct callback despite the eviction.
        subs_x = mgr.get_subscribers("x")
        assert len(subs_x) == 1
        assert subs_x[0][0] is callbacks["x"]

    def test_cache_eviction_at_max_cache_size_boundary(self) -> None:
        """Drive >100 exact-match lookups (the real default cap) and verify correctness."""
        mgr = SubscriptionManager()
        assert mgr._max_cache_size == 100

        first_cb = lambda e: None
        mgr.subscribe("event.0", first_cb)
        mgr.get_subscribers("event.0")  # prime event.0 into cache (oldest entry)

        # Fill the remaining 99 slots
        for i in range(1, 100):
            mgr.subscribe(f"event.{i}", lambda e: None)
            mgr.get_subscribers(f"event.{i}")

        assert len(mgr._exact_match_cache) == 100

        # One more entry triggers eviction of event.0
        mgr.subscribe("event.100", lambda e: None)
        mgr.get_subscribers("event.100")
        assert len(mgr._exact_match_cache) == 100
        assert "event.0" not in mgr._exact_match_cache

        # event.0 must still resolve correctly after eviction
        recomputed = mgr.get_subscribers("event.0")
        assert len(recomputed) == 1
        assert recomputed[0][0] is first_cb

    def test_cache_invalidation_on_new_subscription(self) -> None:
        mgr = SubscriptionManager()
        cb1 = lambda e: None
        cb2 = lambda e: None
        mgr.subscribe("ev", cb1)
        mgr.get_subscribers("ev")  # cache [cb1]
        assert "ev" in mgr._exact_match_cache

        mgr.subscribe("ev", cb2)  # should invalidate cache
        assert "ev" not in mgr._exact_match_cache

        subs = mgr.get_subscribers("ev")
        assert len(subs) == 2

    def test_cache_invalidation_on_wildcard_change(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("a", lambda e: None)
        mgr.get_subscribers("a")
        assert "a" in mgr._exact_match_cache

        mgr.subscribe("*", lambda e: None)
        assert len(mgr._exact_match_cache) == 0


# ---------------------------------------------------------------------------
# TestClearAndCount
# ---------------------------------------------------------------------------


class TestClearAndCount:
    def test_clear_removes_all_subscribers(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("a", lambda e: None)
        mgr.subscribe("b", lambda e: None)
        mgr.clear()
        assert mgr.get_subscribers("a") == ()
        assert mgr.get_subscribers("b") == ()

    def test_count_returns_total_subscriptions(self) -> None:
        mgr = SubscriptionManager()
        assert mgr.count() == 0
        mgr.subscribe("a", lambda e: None)
        mgr.subscribe("a", lambda e: None)
        mgr.subscribe("b", lambda e: None)
        assert mgr.count() == 3

    def test_count_decrements_on_unsubscribe(self) -> None:
        mgr = SubscriptionManager()
        sid = mgr.subscribe("a", lambda e: None)
        mgr.subscribe("a", lambda e: None)
        assert mgr.count() == 2
        mgr.unsubscribe(sid)
        assert mgr.count() == 1

    def test_count_zero_after_clear(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("a", lambda e: None)
        mgr.subscribe("b", lambda e: None)
        mgr.clear()
        assert mgr.count() == 0

    def test_clear_resets_all_internal_state(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("user.*", lambda e: None)
        mgr.get_subscribers("user.created")

        assert mgr.count() == 1
        assert mgr._has_wildcard_patterns is True
        assert len(mgr._subscriptions_by_id) == 1
        assert len(mgr._exact_match_cache) == 1

        mgr.clear()

        assert mgr.count() == 0
        assert mgr._has_wildcard_patterns is False
        assert len(mgr._subscriptions_by_id) == 0
        assert len(mgr._exact_match_cache) == 0


# ---------------------------------------------------------------------------
# TestSubscriptionCleanup
# ---------------------------------------------------------------------------


class TestSubscriptionCleanup:
    def test_unsubscribe_removes_empty_patterns(self) -> None:
        mgr = SubscriptionManager()
        pid = uuid4()
        sub_id = mgr.subscribe("test.event", lambda e: None, plugin_id=pid)
        mgr.unsubscribe(sub_id)
        assert "test.event" not in mgr._subscribers

    def test_unsubscribe_plugin_removes_id_records(self) -> None:
        mgr = SubscriptionManager()
        pid = uuid4()
        mgr.subscribe("a.event", lambda e: None, plugin_id=pid)
        mgr.subscribe("b.event", lambda e: None, plugin_id=pid)
        other = mgr.subscribe("c.event", lambda e: None)

        mgr.unsubscribe_plugin(pid)

        remaining = list(mgr._subscriptions_by_id.keys())
        assert remaining == [other]

    def test_unsubscribe_one_of_duplicate_callbacks_keeps_other(self) -> None:
        mgr = SubscriptionManager()
        pid = uuid4()
        cb = lambda e: None
        sub1 = mgr.subscribe("dup.event", cb, plugin_id=pid)
        mgr.subscribe("dup.event", cb, plugin_id=pid)

        mgr.unsubscribe(sub1)

        assert len(mgr.get_subscribers("dup.event")) == 1
        assert len(mgr._subscriptions_by_id) == 1


# ---------------------------------------------------------------------------
# TestUnsubscribePluginCacheInvalidation
# ---------------------------------------------------------------------------


class TestUnsubscribePluginCacheInvalidation:
    """Regression: removing a plugin's last wildcard subscription must also
    invalidate cached lookups that matched through that wildcard. The old
    implementation recomputed the wildcard flag before invalidating, skipping
    the full-cache clear and leaving zombie callbacks serving events."""

    def test_no_zombie_callbacks_after_unsubscribe_plugin(self) -> None:
        mgr = SubscriptionManager()
        pid = uuid4()
        mgr.subscribe("user.*", lambda e: None, plugin_id=pid)

        assert len(mgr.get_subscribers("user.created")) == 1

        mgr.unsubscribe_plugin(pid)

        assert mgr.get_subscribers("user.created") == ()

    def test_unsubscribe_by_id_also_clears_wildcard_cache(self) -> None:
        mgr = SubscriptionManager()
        sid = mgr.subscribe("order.*", lambda e: None)
        assert len(mgr.get_subscribers("order.placed")) == 1

        mgr.unsubscribe(sid)

        assert mgr.get_subscribers("order.placed") == ()


# ---------------------------------------------------------------------------
# TestMultiplePluginSameEvent
# ---------------------------------------------------------------------------


class TestMultiplePluginSameEvent:
    def test_multiple_plugins_same_exact_event(self) -> None:
        mgr = SubscriptionManager()
        pid_a = uuid4()
        pid_b = uuid4()
        cb_a = lambda e: None
        cb_b = lambda e: None
        mgr.subscribe("ev", cb_a, plugin_id=pid_a)
        mgr.subscribe("ev", cb_b, plugin_id=pid_b)

        subs = mgr.get_subscribers("ev")
        assert len(subs) == 2
        callbacks = {s[0] for s in subs}
        assert cb_a in callbacks
        assert cb_b in callbacks

    def test_unsubscribe_one_plugin_leaves_other(self) -> None:
        mgr = SubscriptionManager()
        pid_a = uuid4()
        pid_b = uuid4()
        cb_a = lambda e: None
        cb_b = lambda e: None
        mgr.subscribe("ev", cb_a, plugin_id=pid_a)
        mgr.subscribe("ev", cb_b, plugin_id=pid_b)

        mgr.unsubscribe_plugin(pid_a)

        subs = mgr.get_subscribers("ev")
        assert len(subs) == 1
        assert subs[0][0] is cb_b

    def test_no_plugin_id_subscription_survives_plugin_unsubscribe(self) -> None:
        mgr = SubscriptionManager()
        pid = uuid4()
        cb_plugin = lambda e: None
        cb_anon = lambda e: None
        mgr.subscribe("ev", cb_plugin, plugin_id=pid)
        mgr.subscribe("ev", cb_anon)  # no plugin_id

        mgr.unsubscribe_plugin(pid)

        subs = mgr.get_subscribers("ev")
        assert len(subs) == 1
        assert subs[0][0] is cb_anon


# ---------------------------------------------------------------------------
# TestHasSubscribers — demand-driven emission query (mute-aware)
# ---------------------------------------------------------------------------


class TestHasSubscribers:
    def test_true_for_exact_subscription(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("a.b", lambda e: None)
        assert mgr.has_subscribers("a.b") is True

    def test_false_when_nothing_matches(self) -> None:
        mgr = SubscriptionManager()
        assert mgr.has_subscribers("a.b") is False

    def test_true_for_glob_subscription(self) -> None:
        mgr = SubscriptionManager()
        mgr.subscribe("a.*", lambda e: None)
        assert mgr.has_subscribers("a.deep.name") is True

    def test_muted_name_reports_no_subscribers(self) -> None:
        """A muted topic reports has_subscribers False even with a live subscriber."""
        mgr = SubscriptionManager()
        mgr.subscribe("a.b", lambda e: None)
        mgr.mute("a.*")
        assert mgr.has_subscribers("a.b") is False


# ---------------------------------------------------------------------------
# TestMute — source suppression via glob patterns
# ---------------------------------------------------------------------------


class TestMute:
    def test_is_muted_matches_glob(self) -> None:
        mgr = SubscriptionManager()
        mgr.mute("worker.*.text")
        assert mgr.is_muted("worker.abc.text") is True
        assert mgr.is_muted("worker.abc.completed") is False

    def test_unmute_restores(self) -> None:
        mgr = SubscriptionManager()
        mgr.mute("a.*")
        assert mgr.is_muted("a.b") is True
        mgr.unmute("a.*")
        assert mgr.is_muted("a.b") is False

    def test_unmute_absent_pattern_is_noop(self) -> None:
        mgr = SubscriptionManager()
        mgr.unmute("never.muted")  # must not raise

    def test_clear_resets_mute_patterns(self) -> None:
        mgr = SubscriptionManager()
        mgr.mute("a.*")
        mgr.clear()
        assert mgr.is_muted("a.b") is False
