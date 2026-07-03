# uxok Deep Audit — 2026-07-02

Five-agent parallel audit (architecture cohesiveness, clean code/DRY/KISS, philosophy + API surface, docs↔code coherence, pip readiness). Synthesis first, then each agent's report verbatim.

Branch at time of audit: `docs/modular-getting-started-tutorial` (uncommitted tutorial + `examples/getting_started/*` rework in the working tree).

---

## Synthesis

**Bottom line: this is a genuinely disciplined codebase with excellent bones and zero correctness findings. The real problems cluster in two places: the release pipeline is broken (CI gates red, no publish path), and the framework's marketing claims about itself ("five primitives," "eleven names," "protocol-first") have drifted slightly ahead of the code.**

| Dimension | Grade | One-line verdict |
|---|---|---|
| Architecture / cohesiveness | A− | Async invariants verified real in code; a few dependency knots |
| Docs↔code coherence | A− | Reader-facing docs executably correct; rot quarantined in excluded `manifests/` essays |
| API surface simplicity | A− | 12-name constitution, drift-tested; one broken docstring example |
| Philosophy adherence | B+ | Supervision-as-signal is exemplary; three stated tenets oversold |
| Clean code / DRY / KISS | B+ / B / B | Zero ruff/mypy debt; a cluster of near-duplicates and one 957-line orchestrator |
| Pip readiness | B− | The **artifact** is publish-grade (A−); the **pipeline** is broken (D) |

### What's verified genuinely excellent

Several agents independently confirmed the same strengths:

- **The async invariants actually hold.** Every registry and capability mutation was checked — no `await` inside any critical section, exactly as decision #12 claims. The `_ReentrantLock`, task tracking, and hot-swap rebind logic are correct.
- **The constitution is machine-enforced and honored.** `tests/test_api_constitution.py` and `tests/test_imports.py::TestKernelBoundary` turn philosophy into CI gates. Recent breaking commits (`88e3eee`, `f66d610`) really did pair API.md + CHANGELOG.md in the same commit. Zero API.md↔code drift was found.
- **Docs are executable and executed.** Both `examples/getting_started` and the README quickstart run verbatim (exit 0, expected output) — including from a **freshly built wheel installed in a clean venv**. `mkdocs build --strict` passes.
- **Packaging fundamentals are right.** Zero runtime deps, `py.typed` ships, wheel contains only `src/uxok`, twine-valid, PEP 639 license, and the PyPI name `uxok` is confirmed free.

### Critical and high-priority findings

1. **[CRITICAL] CI lint/format jobs fail on every run.** `.github/workflows/ci.yml:34` and `:51` run ruff against a nonexistent `plugins/` directory (`E902`), so the style gates are red on every push *and* `examples/` is never actually linted. Fix: `plugins` → `examples` in both jobs.
2. **[HIGH] No release path exists.** There is no publish workflow, no PyPI Trusted Publishing, and zero git tags despite pyproject and CHANGELOG both claiming 0.1.0. Additionally, `scripts/dev_utilities/bump_version.py` is broken — its regex looks for `## Unreleased` but the CHANGELOG uses `## [Unreleased]`, and it mutates pyproject.toml *before* the changelog step fails, leaving a half-bumped tree.
3. **[HIGH] The `Plugin` base-class docstring teaches a removed pattern.** `src/uxok/plugin/_base.py:52-56` shows `await storage.initialize()` on a `PluginView` — which is descriptive-only post-RFC-0001 and raises `AttributeError`. The first docstring every plugin author reads actively mis-teaches. Should show `self.get_capability("storage")`.
4. **[HIGH] `core/_shared_utils.py` inverts the primitive→core dependency arrow.** The registry (`registry/impl.py:8`) and capability system import formatters *up* from `core/`, contradicting the one-way-arrow story. Moving the pure formatters to `utils/` and `resolve_plugin` to `registry/` removes both edges.
5. **[HIGH] Dead, name-colliding capability protocol.** `protocols/capability_system.py` defines a `CapabilitySystem` Protocol imported nowhere in `src/` — and it shares the exact name of the concrete class. The capability system is the one primitive that opted out of the protocol-hiding rule. Honest fix: delete the dead protocol.

### Cross-cutting theme: the docs' hard numbers are stale

Three independent agents converged on this: the code is fine, but the self-description oversells.

- **"Exactly five primitives and nothing else"** — `timing/` is a de-facto sixth: `core.tick`/`slip` are on the Core protocol, the bus stamps every event, and it carries actual *policy* (catchup `skip`/`burst`, busy-wait precision) in `timing/_clock.py:152-158` — the one notable "mechanism not policy" leak.
- **"Exactly eleven names"** (`framework-philosophy.md:7`) — the real `__all__` has twelve.
- **"Protocol-first, Core depends on the protocol"** — internally false: `_core.py:114-119` constructs concrete `_EventBus`/`_HookSystem`/`_Registry` directly, and the clock is wired in by back-patching privates with `type: ignore` (`_core.py:133-134`).

Either promote timing to a named sixth primitive and fix the counts, or move it — but the claims and the code should match, because claim-drift is exactly what this framework says it guards against.

### Medium findings worth batching

- **DRY cluster in the kernel:** the `at_tick` deferral logic duplicated verbatim in `_base.py` (206-225 vs 452-467); two parallel capability-info builders (`get_capability_info` has *no production caller* — delete it); the `core.plugin_error` payload hand-built at four sites; the collision-policy value set declared twice; three different "capability not available" formatters.
- **KISS:** `handle_errors` decorator (90 lines, zero kernel/example usage, clever sentinel-return trick) — flagged independently by two agents as the weakest inhabitant of the kernel. `Plugin.hook` as an `__init__`-closure instead of a method is why the at_tick duplication exists.
- **`Core` at 957 lines** carries two extractable subsystems (hot-reload swap trio, plugin loader).
- **`Plugin.config()` silently falls through to `CoreConfig`** — a plugin key colliding with a kernel field name (e.g. `"tick_rate"`) returns the kernel's value.
- **Stale duplicate essays:** `manifests/FRAMEWORK_PHILOSOPHY.md` and `manifests/KERNEL_ARCHITECTURE.md` show removed config fields and the removed `Core.registry` — build-excluded but exactly the duplicate/stale pointers the mesh goal wants gone. Retire or redirect them.
- **Packaging polish:** sparse `[project.urls]` (no Homepage/Documentation/Issues), uncurated sdist (ships `tests/`, no `MANIFEST.in`).
- **Low:** leftover `_orion_*` naming in hot-load internals (`_core.py:457`, `_decorators.py:16-17`); dead utils (`locked`, `cleanup_task`, `log_plugin_op`); deprecated `safety check` in CI.

### Recommended order of attack

1. **One-line CI fix** (`plugins`→`examples`) — unblocks everything else being verifiable.
2. **Fix the `Plugin` docstring example** — trivial, actively harmful as-is.
3. **Reconcile the counts** in `framework-philosophy.md` / `architecture-overview.md` (five→six or move timing; eleven→twelve) and retire the two stale `manifests/` essays.
4. **DRY/dead-code sweep**: `_defer` helper + `hook`-as-method + plugin-error event builder; delete `get_capability_info`, dead utils, and the dead capability protocol; move `_shared_utils` formatters to fix the dependency arrows.
5. **Release pipeline**: repair `bump_version.py` (regex + atomicity), add a tag-triggered publish workflow with Trusted Publishing, tag `v0.1.0`.
6. **Larger, isolated refactor**: extract loader + hot-reload from `Core`.

---

# Report 1 — Architecture & Cohesiveness (architecture-analyzer)

I have thoroughly read the entire kernel, the import graph, and the boundary tests (63 passed). Here is the audit.

---

## uxok Kernel — Architectural Cohesiveness Audit

**Scope covered:** all 40 modules under `src/uxok/` (7,299 LOC), the import graph, `tests/test_imports.py`, and the constitutional protocol surfaces. `pytest tests/test_imports.py -q` → 63 passed (kernel boundary + no-circular-import assertions hold).

**Headline:** This is a genuinely well-architected kernel. The async invariants it advertises actually hold in the code, the protocol surface is disciplined, and the capability-attenuation design is coherent. There are **no CRITICAL correctness findings**. The issues are cohesion/layering smells and cruft — real, but low-blast-radius.

---

### HIGH

#### H1 — `core/_shared_utils.py` is a low-cohesion grab-bag that inverts the primitive→core arrow
`/home/bork/vault/1-Projects/uxok/src/uxok/core/_shared_utils.py` mixes three unrelated concerns: pure string/log helpers (`format_plugin_error`, `format_capability_error`, `log_op`, `log_plugin_op` — lines 130-164), a registry helper (`resolve_plugin`, lines 105-127), and heavy lifecycle orchestration (`drain_plugin_resources`, lines 20-102, which touches bus/hooks/capabilities/scheduler). Because it lives in `core/` yet exports those pure formatters, two *lower* primitives reach *up* into the core package to get them: `registry/impl.py:8` (`from uxok.core._shared_utils import format_plugin_error, log_op`) and `core/_capability_system.py:10`. The registry — one of the five primitives, and the one whose docstring calls itself "zero-contention" and standalone — now has a load-time dependency edge on `core/`. That contradicts the stated one-way arrow. **Recommendation:** move the pure formatters and `log_op`/`log_context` into `utils/`, move `resolve_plugin` into `registry/`, and leave only `drain_plugin_resources` in `core/`. That removes the registry→core and capability→core edges entirely and gives each function a home that matches its dependencies.

#### H2 — The capability system is the one primitive *not* hidden behind its protocol, and its protocol is dead
Audit question #3: `Core.__init__` annotates `self._event_bus: EventBus`, `self._hook_system: HookSystem`, `self._registry: Registry` (all protocol types — `/home/bork/vault/1-Projects/uxok/src/uxok/core/_core.py:114-119`) but `self._capability_system = CapabilitySystem(...)` (`_core.py:145`) binds the concrete class directly with no protocol annotation. Meanwhile `/home/bork/vault/1-Projects/uxok/src/uxok/protocols/capability_system.py` *defines* a `CapabilitySystem` Protocol that is imported nowhere in `src/` except a name-check test (`tests/test_imports.py:159-163`) — it is dead contract. Worse, the protocol and the concrete impl share the exact class name `CapabilitySystem`, so a reader grepping the symbol gets two unrelated definitions. This is an inconsistency in the layering rule the framework enforces everywhere else. **Recommendation:** either annotate `self._capability_system: CapabilitySystemProtocol` and keep the contract live (renaming the protocol to avoid the collision), or delete `protocols/capability_system.py` and stop pretending the capability system is protocol-abstracted. Given the concrete class is a pure kernel-internal (never swapped, never third-party-implemented), deleting the protocol is the more honest, simpler choice.

---

### MEDIUM

#### M1 — The tick clock is wired in by mutating subsystem privates; the constructor `clock` params are dead on the real path
`_core.py:133-134` does `self._event_bus._clock = self._tick_clock  # type: ignore[attr-defined]` and the same for `_hook_system._clock`. Yet `_EventBus.__init__` (`events/_bus.py:33-38`) and `_HookSystem.__init__` (`hooks/_system.py:28-37`) both already accept a `clock` parameter — which the real Core path never passes (it constructs the bus first, the clock second, then back-patches). So the public-looking ctor param is effectively test-only, and production wiring reaches through name-privacy with a `type: ignore`. This is a construction-order coupling (bus needs no clock, clock needs bus, hooks need clock for stamping) papered over by mutation. **Recommendation:** give the bus/hook systems an explicit `attach_clock(clock)` method (documented, no `type: ignore`) or restructure construction so the clock is injected once, and drop the unused ctor params. It removes two `type: ignore`s and makes the tick-stamping dependency legible.

#### M2 — "The core provides ONLY these 5 primitives" is inaccurate: timing/ is a de-facto sixth primitive
Audit question #4. The tick system is not one of the five listed primitives, nor is it "direct support" for one — it is an independent capability woven into the kernel: `core.tick`/`slip` are on the `Core` protocol (`protocols/core.py:197-205`), the event bus stamps every event with `tick`/`slip` (`events/_bus.py:53-61`), the hook system injects `_tick_context` (`hooks/_system.py:189-194`), and `emit(at_tick=N)`/`hook(at_tick=N)` depend on `TickScheduler`. It cannot be extracted as a plugin without unpicking all of that. `utils/` is fine as kernel-internal support, but `timing/` is a full subsystem the "only 5" framing hides. **Recommendation:** reconcile the docs — either promote timing to a named sixth primitive in `CLAUDE.md`/`architecture-overview.md`, or explicitly document it as "the clock, an opinionated kernel service" and stop claiming a bare five. The code is fine; the claim is stale.

#### M3 — Duplicated capability-descriptor construction (DRY)
`CapabilitySystem.get_capability_info` (async, returns `dict` — `core/_capability_system.py:438-480`) and `CapabilitySystem.snapshot_capability_info` (sync, returns `CapabilityInfo` — `_capability_system.py:630-668`) build the *same* `provider_info` list comprehension and the same `get_protocol_methods(protocol)` payload, differing only in return shape. Both are live (the async one is in API.md and tested). **Recommendation:** have `get_capability_info` derive its dict from the `CapabilityInfo` produced by a shared private builder, so the provider-descriptor shape has a single definition.

#### M4 — `utils/` reaches back into `plugin/` (leaf module with an upward edge)
`utils/_capability_utils.py:160` lazily imports `from uxok.plugin._base import Plugin` inside `get_instance_methods` to compute the base-method exclusion set. `utils/` should be the dependency floor (protocols only), but this makes it structurally circular with `plugin/` (broken only by the lazy import + the mirrored lazy import in `_base.py`). `get_instance_methods` is used solely by `PluginView.methods()`. **Recommendation:** move `get_instance_methods` to `registry/_plugin_view.py` (its only caller) or to `plugin/`, removing the utils→plugin edge and keeping utils a true leaf.

---

### LOW

#### L1 — "orion" (former project name) leftovers in kernel internals
`_core.py:457` `pkg_name = f"_orion_plugin_{uuid4().hex}"`, `_core.py:472` `"<orion_plugin>"`, and the decorator marker attributes `_HOOK_MARKER = "_orion_hooks"` / `_ON_HANDLER_MARKER = "_orion_event_handlers"` (`plugin/_decorators.py:16-17`). These are hot-load and discovery internals; a stray plugin author who inspects `dir(method)` sees `_orion_hooks`. Cosmetic, but it's naming debt in a project that renamed to uxok. **Recommendation:** rename to `_uxok_*` in one sweep.

#### L2 — Dead code to prune
`log_plugin_op` (`core/_shared_utils.py:135`) — defined, zero callers. `locked` async context manager (`utils/_helpers.py:17`, exported in `utils/__all__`) — zero callers (leftover from the removed RWLock). `AsyncTaskManager.cleanup_task` (`utils/_helpers.py:153`) — zero callers. **Recommendation:** delete; they widen the internal surface for no benefit.

#### L3 — Dangling empty section header
`plugin/_base.py:626` ends the file with `# ========= Internal Helpers ==========` and no body. Minor cruft; remove it or move the section marker.

#### L4 — `handle_errors` is the one product-shaped convenience in the kernel
`plugin/_decorators.py:99-188` is a try/catch-and-emit decorator with a duck-typed legacy fallback path — pure sugar over `Plugin._emit_plugin_error`, buildable outside the kernel. It's blessed by API.md so this isn't a violation, but per the "framework not product / if it can be built from primitives it's a plugin" philosophy it's the weakest inhabitant of the kernel. Flagging for awareness, not action.

---

### Done notably WELL (balance)

- **The async invariants are real, not aspirational.** I verified every registry mutation (`_Registry.add/remove/swap_instance/_replace_dependency_edges`) and every capability mutation (`register_capabilities`, `unregister_capabilities_by_plugin`, `swap_provider`) contains **no `await` inside the critical section** — they are `async def` for interface uniformity but atomic under cooperative scheduling, exactly as decision #12 claims. `StateManager.transition` correctly fires `core.state.changed` *outside* the lock (`_state_manager.py:96-97`). The `_ReentrantLock` (`_core.py:49-79`) is a clean owner-task/depth implementation that genuinely solves the on_start re-entrancy problem. Every fire-and-forget task (dispatch, slip, scheduled, background) is tracked in a set with a `discard` done-callback and drained on stop. This is the strongest part of the codebase.

- **Protocol-first discipline and boundary enforcement.** The curated top-level `__all__`, the independent second-opinion surface test, the AST-based `TestKernelBoundary`, and the deliberate non-re-export of `EventBus`/`HookSystem`/`PluginId` (with a comment explaining why) show unusual rigor. The dependency arrow to *application* code is airtight (test passes); the intra-kernel tangles above are the only slippage.

- **The capability-attenuation model is elegant.** `CapabilityFacet` re-resolving the provider live on every call (`_capability_facet.py:62-79`) means swap-rebind and revoke→`StalePluginError` fall out for free rather than needing bespoke invalidation — the same trick `PluginView` uses. The open/declared/sealed tiers, the `enforce_requires` gate applied identically on both resolution routes, and the honestly-documented "robustness not a boundary" limit of `attenuate_return` reflect mature design judgment.

---

### Verdict

uxok is a cohesive, disciplined microkernel whose hardest guarantees (lock-free atomicity, fire-and-forget dispatch, hot-reload swap correctness, kernel boundary) are backed by the actual code, not just the docstrings. Its cohesion defects are concentrated and mundane: one grab-bag module (`core/_shared_utils.py`) that drags the registry and capability primitives into a dependency on `core/`, one primitive (capability system) that opted out of the protocol-hiding rule the rest of the kernel follows (with a dead, name-colliding protocol left behind), a clock wired in by poking privates, and a "5 primitives only" slogan that quietly undercounts timing. None threaten correctness; all are refactors of a few hundred lines that would tighten the story the framework tells about itself. The bones are excellent; the connective tissue has a few knots.

**Grade: A− (would be A after H1/H2 and M1/M2 are addressed).**

Key files for follow-up: `/home/bork/vault/1-Projects/uxok/src/uxok/core/_shared_utils.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/protocols/capability_system.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/core/_core.py` (lines 114-134, 145), `/home/bork/vault/1-Projects/uxok/src/uxok/utils/_capability_utils.py` (line 160), `/home/bork/vault/1-Projects/uxok/src/uxok/core/_capability_system.py` (lines 438-480 vs 630-668).

---

# Report 2 — Clean Code / DRY / KISS (code-auditor)

I have completed a thorough read of the kernel, examples, and tooling. Here is the audit report.

---

## Code Audit Report — uxok microkernel

**Code Examined**: `src/uxok/` (all 40 modules), `examples/`, and cross-referenced `tests/`
**Evaluated against**: CLEAN CODE, DRY, KISS (plus project philosophy in `CLAUDE.md` / `framework-philosophy.md`)
**Overall Assessment**: PASS (CONDITIONAL — several MEDIUM cleanups recommended, none blocking)
**Risk Level**: LOW

### Summary

This is a well-disciplined codebase. `ruff check src tests examples` and `mypy src` both pass clean, confirming the project's zero-violation claim. Modules are small and cohesive, the kernel/plugin boundary is real, and the documented lock-free invariant (decision #12) is applied consistently. The findings below are refinements, not rescues: a cluster of genuine near-duplicates, one over-built decorator, an oversized orchestrator, and one clever-where-plain construct in the Plugin base class. No CRITICAL or HIGH issues, no correctness bugs, no god object beyond the justified orchestrator.

---

### Medium Priority Issues

#### M1 [DRY] `at_tick` deferral logic duplicated verbatim in `Plugin`
`src/uxok/plugin/_base.py:206-225` (the `hook_method` closure) and `_base.py:452-467` (`emit`) implement the identical pattern: validate `at_tick > current_tick`, raise the **same verbatim** `ValueError` string ("at_tick=... is in the past ... Use core.tick + N for future scheduling."), then `self.__core_real._tick_scheduler.schedule_at(at_tick, current_tick, factory=..., owner=self)`.
**Recommendation**: Extract one private `_defer(self, at_tick, factory) -> None` that validates and schedules. Both call sites shrink to `if at_tick is not None: return self._defer(at_tick, lambda: ...)`. Removes the duplicated error string (a single knowledge source) and the duplicated guard.

#### M2 [DRY + dead-in-production] Two parallel capability-info builders
`CapabilitySystem.get_capability_info` (`_capability_system.py:438-480`, returns `dict`) and `snapshot_capability_info` (`:630-668`, returns `CapabilityInfo`) build the **same** `provider_info` comprehension (`name/id/version/description/tags`) and the same selected-provider derivation. `CapabilityInfo`'s own docstring admits it "mirrors the shape of the old `get_capability_info` dict." Grep confirms `get_capability_info` has **no production caller** — only tests (`test_capability_system.py`, `test_typed_capabilities.py`, `test_core_lifecycle.py`); the live path is `collection.capability.info()` → `CapabilityInfo`.
**Recommendation**: Delete `get_capability_info` and retarget its tests at `snapshot_capability_info`/the collection API. If a dict is still wanted somewhere, derive it from `CapabilityInfo` (`dataclasses.asdict`). One builder, one shape.

#### M3 [DRY] `core.plugin_error` payload schema re-encoded at every emit site
The event payload (`plugin_id`, `plugin_name`, `source`, `error`, `error_type`, `phase`) is hand-built inline at `_core.py:365-375`, `_core.py:779-789`, and `_bus.py:123-132`, while `Plugin._emit_plugin_error` (`_base.py:604-624`) is the canonical builder the core cannot reach (it has no plugin-method receiver). The schema lives in four places; a field rename touches all four silently.
**Recommendation**: Add a module-level `build_plugin_error_event(plugin_id, plugin_name, source, error, **extra) -> Event` in `_shared_utils.py` and route all four sites (including `Plugin._emit_plugin_error`) through it. Impact: internal only; subscribers see identical payloads.

#### M4 [DRY] Collision-policy value set has two sources of truth
Valid `capability_collision` values are declared in `_config_validation.py:34` (`{"error_on_conflict","first_wins","last_wins_with_warning"}`) **and again** in `_capability_system.py:81` as `_VALID_COLLISION_POLICIES`. Adding a policy requires editing both, or validation and runtime silently disagree.
**Recommendation**: Hoist the canonical frozenset to one location (e.g. alongside the policy dataclass) and import it into the validator. The same applies more loosely to the enum values duplicated between `config.py` docstring/comments and `_config_validation.py` — comments are the weaker copy and will drift.

#### M5 [DRY / inconsistent errors] Three different "capability not available" formatters
`format_capability_error` (`_shared_utils.py:147-152`), `CapabilityError.__init__` (`errors.py:44-66`), and `MissingCapabilityError.__init__` (`errors.py:128-137`) each format the same concept with different punctuation and wording ("not available." vs "not available.\nAvailable capabilities:" vs "No plugin provides required capability:"). `Core.get_capability` (`_core.py:889-898`) even builds a `KeyError` via `format_capability_error`, discards it, and rebuilds a `CapabilityError` — formatting the same message twice.
**Recommendation**: Make the exception classes the single formatting authority; delete `format_capability_error` or reduce it to a thin shim the exceptions call. Have `get_capability` raise `CapabilityError` directly rather than KeyError→catch→reformat.

#### M6 [KISS / YAGNI] `handle_errors` decorator is speculative surface
`_decorators.py:99-188`: 90 lines, dual sync/async wrappers, a duck-typed "legacy behavior" fallback that returns the exception object `e` as an out-of-band signal (`if _handle(self, e) is e:`), plus an `_error_context` carrying a timestamp used nowhere else. Grep shows **no kernel or example usage** — only its own `test_decorators.py`. For a framework whose stated principle is "if it can be built from primitives, it's a plugin," this is exported product surface (`plugin/__init__.py`) with no consumer.
**Recommendation**: Either delete it (plugins can `try/except` + `self._emit_plugin_error`), or if kept as a documented convenience, drop the duck-typed fallback branch and the sentinel-return trick — collapse to the real-`Plugin` path only. The clever `is e` signaling is the KISS violation regardless.

#### M7 [CLEAN CODE / SRP] `Core` (957 lines) carries two extractable subsystems
`src/uxok/core/_core.py` is the largest file by far. Two clusters inflate it:
- **Hot reload**: `_reload_plugin_now`, `_swap_plugin` (~120 lines, 8 numbered steps with nested rollback), `_drain_instance` (`:660-830`).
- **Plugin loader**: `load_plugin` (`:395-515`, ~120 lines) mixes three concerns — isolated-module exec/`sys.modules` synthesis, Plugin-subclass discovery, and load-vs-reload branching.

The codebase already factors peers out (`_shared_utils.py`, `_state_manager.py`), so the pattern exists.
**Recommendation**: Move the loader's materialize-and-discover half into `_loader.py` (`materialize_plugin(code, origin) -> type[Plugin]`) and the swap trio into `_hot_reload.py`. `Core` keeps the locked entry points and delegates. Shrinks the orchestrator toward its single job (lifecycle orchestration) and makes `_swap_plugin` testable in isolation.

#### M8 [KISS] `Plugin.hook` is a closure assigned in `__init__`, not a method
`_base.py:184-234`: `hook` is built as a nested function and bound with `self.hook = hook_method`. A plain method `def hook(self, name, *args, at_tick=None, firstresult=False, **kwargs)` is behaviorally identical, costs no per-instance closure, is overridable by subclasses, and shows up in normal introspection (`get_instance_methods` already special-cases base methods). The closure form is clever where plain would do, and it is why the at_tick logic (M1) had to be duplicated rather than shared as a method.
**Recommendation**: Convert to a regular method; combine with M1's `_defer` helper.

### Low Priority Issues

#### L1 [DEAD CODE] Test-only utilities with no production caller
`locked` (`_helpers.py:17-25`) and `AsyncTaskManager.cleanup_task` (`_helpers.py:153-162`) are exercised only by `test_shared_utils.py`; the kernel uses `async with self._lock` directly everywhere and never calls `cleanup_task`. `log_plugin_op` (`_shared_utils.py:135-144`) is likewise tests-only. (`HookSystem.unregister`/`get_hooks` and `PluginCollection.uptime_over` are also production-unused but are protocol contract / public collection surface — keep those, but note they are validated only by their own tests.)
**Recommendation**: Delete `locked`, `cleanup_task`, and `log_plugin_op` with their tests unless a near-term consumer is planned.

#### L2 [DRY] `_FilterProxy.provides` / `consumes` are structural twins
`_plugin_view.py:249-291`: both methods repeat the same fast-path (index lookup) / slow-path (linear scan) shape across a 3-way `capability/hook/event` branch, differing only in which index dict and which `PluginView` field they read.
**Recommendation**: Drive from a small mapping `{(filter_type, direction): (index_attr, view_field)}` and have one helper resolve it. Optional — the current form is readable, just repetitive.

#### L3 [CLEAN CODE] `validate_plugin_name` computes a value that doesn't affect the result
`_naming.py:31-34`: `validate_identifier` already restricts the charset to `[A-Za-z0-9_.-]`, so `sanitize_identifier(validated, ...)` cannot change anything a subsequent `^[a-z][a-z0-9_]*$` check would react to — `sanitized` is effectively `validated`. The sanitize step reads as meaningful guard logic but is inert.
**Recommendation**: Drop the `sanitize_identifier` call and regex-check `validated` directly, or document why the intermediate exists.

#### L4 [CLEAN CODE] In-code docstring density / signal-to-noise
Many functions carry docstrings 3-5x their body, dense with RFC/spec section cross-references (e.g. `Core._admit`, `check_plugin`, `attenuate_return`, `CoreFacet.check_plugin`). With the constitution already canonical in `docs/manifests/API.md`, these in-source essays risk drift from the very documents they cite.
**Recommendation**: Keep the invariant-critical notes (lock-free rationale, TOCTOU), trim the RFC re-narration to a one-line pointer. Not a code defect — a maintenance-cost observation.

### Strengths Observed

1. **Lock-free invariant discipline is real, not aspirational.** Every synchronous critical section in `registry/impl.py`, `_capability_system.py`, and `_subscriptions.py` is genuinely await-free, and the boundary is documented at each site (e.g. `_capability_system.py:74-78`). `swap_provider` and `unregister_capabilities_by_plugin` correctly return revocation/rebind lists and push the event-bus `await` to the caller rather than awaiting mid-mutation. This is the hardest thing in the codebase to get right, and it is right.
2. **Configuration is a textbook single source of truth.** One `CoreConfig` dataclass, all validation centralized in `_config_validation.py`, every field a valid `Core(**kwargs)`. No scattered settings, sensible defaults, clean per-field validators.
3. **Admission predicates are unified where duplication would be dangerous.** `Core._admit`/`check_plugin` and the raising enforcers (`validate_requirements`, `register_capabilities`, `raise_admission_error`) share the same synchronous predicates (`missing_requirements`, `provides_conflicts`, `contract_failures`), so the advisory probe and the at-commit gate provably cannot drift — DRY applied exactly where correctness depends on it.
4. Honorable mention: `StateManager`, `TickScheduler`, and `_Registry` are small, single-purpose, and readable — good exemplars of the "primitives, not products" philosophy.

### Impact Analysis

All recommendations are internal-only. M2 (delete `get_capability_info`) and L1 (delete `locked`/`cleanup_task`/`log_plugin_op`) touch tests and require retargeting/removing those tests — verify the 91.5% branch floor holds after (`test_capability_system.py`, `test_typed_capabilities.py`, `test_shared_utils.py`, `test_decorators.py` are the affected suites). M7 (extract loader/hot-reload) is the largest change but is pure relocation behind `Core`'s existing locked entry points — no public API or `API.md` change, guarded by the existing integration/hot-reload tests. M3/M4/M6 are behavior-preserving. Nothing here alters the constitutional API, so no `API.md`/`CHANGELOG.md` coupling is triggered except an M6 removal of `handle_errors` from the public export (which would).

### Recommended Next Steps (prioritized)

1. M3 + M1 + M8 together: introduce the plugin-error event builder and the `_defer` helper, and convert `hook` to a method — one focused `plugin/`+`_shared_utils` pass that kills three duplications at once.
2. M2 + L1: delete the production-dead `get_capability_info`, `locked`, `cleanup_task`, `log_plugin_op` and retarget tests; re-run the coverage gate.
3. M4 + M5: collapse the collision-policy constant and the three capability-error formatters to single sources.
4. M6: decide `handle_errors` in or out; if in, strip the duck-typed fallback.
5. M7: schedule the `Core` loader/hot-reload extraction as its own reviewed change (largest blast radius, so isolate it).

### Grades

- **Clean code: B+** — zero lint/type debt, cohesive small modules; dinged by the 957-line `Core`, the closure-`hook`, the 120-line `load_plugin`/`_swap_plugin`, and an inert sanitize step.
- **DRY: B** — excellent where correctness demands it (admission predicates), but several concrete near-duplicates remain: at_tick logic, capability-info builders, plugin-error payloads, collision-policy constant, and three error formatters.
- **KISS: B** — most complexity is RFC-justified mechanism; the real offenders are the speculative `handle_errors` decorator and the closure-based `hook`. The `capability_access` facet subsystem is heavy but earns its place per the exokernel "secure bindings" thesis.

Key files for follow-up (absolute paths): `/home/bork/vault/1-Projects/uxok/src/uxok/plugin/_base.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/core/_core.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/core/_capability_system.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/core/_shared_utils.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/plugin/_decorators.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/_config_validation.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/errors.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/utils/_helpers.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/plugin/_naming.py`.

---

# Report 3 — Philosophy Adherence & API Surface Simplicity (architecture-analyzer)

I have sufficient evidence. Here is the audit report.

---

## uxok Deep Audit — Philosophy Adherence & API Surface Simplicity

### Summary

uxok is a genuinely disciplined microkernel with an unusually strong constitutional apparatus: the public surface is machine-enforced (`tests/test_api_constitution.py`), the kernel boundary is machine-enforced (`tests/test_imports.py`), and CHANGELOG/API.md pairing on breaking changes is *actually* being honored (verified below). The exokernel "mechanism not policy" tenet is upheld exemplarily in the one place most frameworks fail — supervision. Where it drifts is not sloppiness but scope creep dressed in good prose: a whole timing subsystem lives in a kernel that advertises "five primitives and nothing else," the protocol-first claim is skin-deep (the kernel hardwires concrete impls), and the RFC 0001-0006 secure-capability layer is a large, self-admittedly-leaky complexity load bolted onto a kernel whose stated top value is "remove complexity before adding it."

---

### Part A — Philosophy Adherence (tenet by tenet)

| # | Tenet | Verdict | Evidence |
|---|-------|---------|----------|
| A1 | Mechanism not policy — **supervision/retry** | **UPHELD (exemplary)** | `_core.py:362-376` emits `core.plugin_error` and re-raises/rolls back; no restart, no backoff, no circuit breaker anywhere in core. Hook failures isolate to `core.hook_error` (`_capability_system` / hook system), not exceptions. This is textbook exokernel and the strongest part of the codebase. |
| A2 | Mechanism not policy — **scheduling** | **PARTIALLY VIOLATED** | `timing/_clock.py:152-158` bakes catchup policy (`"skip"` re-anchors and drops missed boundaries; `"burst"` replays) into the kernel loop. `config.py:66-69` even editorializes the policy ("right for live robots" vs "for simulation/replay"). Precision (`sleep`/`hybrid` busy-wait, `tick_busy_wait_us`) and slip thresholds are also kernel-resident. It is *configurable* policy, which softens this, but the decision lives in core, not in a downloaded plugin. |
| A3 | Secure bindings / downloaded policy | **UPHELD (with caveat)** | `capability_access` open/declared/sealed, `enforce_requires` (`_core_facet.py:52`), `kernel.lifecycle`/`kernel.dispatch` reserved grants — a faithful "secure binding" model. Caveat under A6. |
| A4 | Framework not product | **UPHELD** | No logging/HTTP/persistence/metrics in core; all are plugins. Sole exception is the timing subsystem (A2/A7). |
| A5 | Convention over configuration | **UPHELD** | `emit(at_tick=N)` instead of a `DeferredEvent` class (`_base.py:436`); name auto-detection (`_base.py:126-128`). Exactly as the philosophy advertises. |
| A6 | Simplicity over features ("remove complexity before adding it") | **PARTIALLY VIOLATED** | The secure-capability layer is the single largest complexity mass in the kernel: `CoreFacet` + `LifecycleFacet` + `CapabilityFacet` + `enforce_requires` + `attenuate_return` + `_leak_types`. The **sealed return guard** (`_capability_system.py:220-256`) adds a whole `isinstance`-against-`_leak_types` mechanism whose own docstring concedes it is *"robustness, not a boundary… one hop only"* — trivially defeated by `[plugin]` or `self._Plugin__core_real`. Adding non-trivial kernel machinery for a guarantee it explicitly does not provide is precisely the "does this add complexity? default verdict no" test failing. |
| A7 | "If it can be built from primitives, it's a plugin, not core" / **"five primitives and nothing else"** | **VIOLATED (as stated)** | `architecture-overview.md:3` claims "exactly five primitives … and nothing else." The **timing subsystem is a sixth** — `TickClock` + `TickScheduler`, instantiated directly in `_core.py:121-130`, surfaced as `core.tick`/`core.slip`/`emit(at_tick=)`. A tick-emitting scheduler is buildable from `create_background_task` + the event bus; it was chosen for core instead. Defensible as a design decision, but it flatly contradicts the advertised primitive count. |
| A8 | Protocol-first (depend on protocols, never concrete impls) | **PARTIALLY VIOLATED** | The claim (`framework-philosophy.md:23`, `Core` "depends on that protocol, not on any particular implementation") is false for the kernel itself: `_core.py:20-21,114-119` imports and constructs `_EventBus`, `_HookSystem`, `_Registry` concretes directly — you cannot inject an alternative bus. `Plugin._attach_core` (`_base.py:253`) and `_core_facet.py:44` type against the **concrete** `Core`, not the protocol. Protocol-first holds for the *author-facing* surface; it does not hold internally. |
| A9 | Opt-in, sensible defaults, never forced | **UPHELD (minor)** | New behavior gated behind config with back-compat defaults (`capability_access="open"`, additive `check_plugin`). Minor: default `capability_collision="last_wins_with_warning"` (`config.py:48`) silently replaces a provider — an opinionated default, though overridable. |
| A10 | Predictable / deterministic (same input → same output) | **PARTIALLY** | Tick-based deferral is deterministic. But the **default** immediate path — concurrent fire-and-forget dispatch with "no global ordering guarantee across independent publishers" (API.md §5) — is non-deterministic in handler ordering. Documented and defended, but it is in direct tension with the literal tenet. |

---

### Part B — API Surface Simplicity (findings by severity)

#### B1 — API.md ↔ code drift: **NONE found (constitution intact)** — positive
Enumerated the real surface and it matches API.md exactly:
- top-level `__all__`: 12 names — matches API.md §1.
- `uxok.protocols` 8, `uxok.registry` 3, `uxok.plugin` 6, `uxok.errors` 6 — all match §11.
No public-in-code-but-absent-from-API.md, no phantom API.md entries. `tests/test_api_constitution.py` parses §11 and asserts importability, so drift is actively prevented. **This is done notably well.**

#### B2 — [MEDIUM] Prose contradicts the constitution on surface size
`framework-philosophy.md:7` states the surface "exports **exactly eleven names**." The real `__all__` has **twelve** (`StalePluginError` is the 12th; API.md §1 correctly lists 12). A load-bearing philosophy sentence is numerically wrong and undercuts its own "small surface" argument. Fix: "twelve." Pairs with A7's "five primitives … and nothing else" — both hard counts in the explanation docs are stale relative to code.

#### B3 — [HIGH] Broken canonical example in the most-read docstring
`src/uxok/plugin/_base.py:52-56`, the `Plugin` class docstring every author opens first:
```python
storage_providers = plugins.capability.provides("storage")
storage = await storage_providers.first()
await storage.initialize()
```
`.first()` returns a `PluginView` (API.md §10.2), which post-RFC-0001 §3.2.2 is **descriptive-only, has no `__getattr__`, and no invocation members**. `await storage.initialize()` raises `AttributeError`. The example teaches a pattern the kernel deliberately removed. Replace with `self.get_capability("storage")`.

#### B4 — [MEDIUM] Redundant capability-resolution paths (three ways, one blessed)
Same provider is reachable via: (a) `self.get_capability(...)` — now blessed canonical (CHANGELOG Unreleased, API.md §3.3); (b) `self.core.get_capability(...)` — identical gate, retained as "security-model detail" (`_core_facet.py:108`); (c) `core.list().capability.provides(name).first()` — returns a descriptive view, not a live provider. The team has *documented* the winner (a) but both (a) and (b) remain live and identical. Acceptable resolution given (b) is the facet mechanism, but it is still two author-visible ways to do one thing. **Winner: `self.get_capability`.** Ensure no reader-facing doc/docstring still shows (b) or the broken (c) (see B3).

#### B5 — [MEDIUM] `Plugin.config()` namespace bleed into `CoreConfig`
`_base.py:515-543`: lookup falls through plugin-scoped → schema default → **`getattr(core.config, key)`** → arg default. A plugin key colliding with a `CoreConfig` field name (e.g. `self.config("tick_rate")` or `"max_plugins"`) silently returns the *kernel's* value, not the plugin's. Mixing two config namespaces in one accessor is a leaky, surprising overlap. Consider dropping level 3 or gating it behind a separate `core_config(...)` accessor.

#### B6 — [MEDIUM] "hook" is overloaded three ways; asymmetric with events
`@hook` (decorator, registers), `self.hook(...)` (instance attribute, **invokes**), `self.register_hook(...)` (registers), plus `core.hooks`. The event side uses three distinct words (`@event`/`subscribe`/`emit`); the hook side reuses "hook" for both decorate-register and invoke. A newcomer reading `@hook("persona")` then `await self.hook("persona")` (exactly the getting_started pattern, `model.py:17` + `agent.py:33`) must infer that identical spelling means opposite operations. Naming asymmetry, not a bug.

#### B7 — [LOW] `Plugin.hook` is an in-`__init__` closure, not a method
`_base.py:184-234` builds `hook_method` per instance and assigns `self.hook`. It does not appear on the class, won't show in `help(Plugin)`/IDE, and can't be cleanly overridden. Justified for hot-reload fresh-binding, but a normal `async def hook(self, ...)` reading `self` achieves the same with less magic. Minor over-engineering.

#### B8 — [LOW] `uxok.utils.__all__` exports an underscore-named symbol
`_AsyncSafeSet` is in a public `__all__`. API.md §11 explicitly declares `uxok.utils` non-constitutional, so no constitution violation — but a private-named symbol in a public `__all__` is a smell. Either rename or drop from `__all__`.

#### B9 — [LOW] Default-mode `self.core` is the raw kernel (leaky by design)
Under `capability_access="open"` (the default), `self.core` **is** the concrete `Core` (`_base.py:270-271`), so any plugin can call `core.stop()`, touch `core._registry`, `core._capability_system`. The `Core` protocol annotation hides that the runtime object is fully unattenuated. This is the documented single-trust-domain tradeoff, not a defect — noted for completeness as a leaky abstraction present at defaults.

#### B10 — CHANGELOG discipline: **VERIFIED UPHELD** — positive
Both recent breaking commits touched API.md **and** CHANGELOG.md in the same commit:
- `88e3eee refactor!: remove kernel auto-start` → API.md +5, CHANGELOG +1.
- `f66d610 refactor!: remove kernel-level plugin blocklist` → API.md +12, CHANGELOG +5, plus test removals.
The constitutional rule is being followed in practice, not just asserted.

#### Concept count for a working plugin (litmus: `examples/getting_started/`)
To write the two-plugin example an author touches ~8 concepts: `Plugin` subclass, keyword metadata (`name`/`requires`/`provides`), `on_start`, `get_capability`, `emit` + `@event` (event bus), `self.hook` + `@hook` (hook system), and host-side `Core` + `load_plugin`/context-manager. A true "hello world" needs ~4 (`Plugin`, `super().__init__`, `@event`/`emit`). The surface is well-curated. The one avoidable tax is that the flagship example forces **both** the event bus and the hook system into a trivial demo, so the reader must learn the events-vs-hooks distinction on page one.

---

### Positive observations
- Supervision-as-signal (A1) is a model implementation of the exokernel tenet; most "microkernels" fail exactly here.
- Two machine-enforced invariants — `test_api_constitution.py` (surface) and `test_imports.py::TestKernelBoundary` (one-way dependency) — turn philosophy into CI gates. Rare and excellent.
- CHANGELOG/API.md pairing is real (B10), not aspirational.
- The lock-free-critical-section invariant is documented consistently at every mutation site (`_capability_system.py:74-78`, `_core.py:136-142`).
- The 12-name curated top-level surface with a written "drawn by *use*, not tidiness" rule (API.md §1) is a genuinely thoughtful boundary.

---

### Grades

**Philosophy adherence: B+**
The kernel's discipline is real and, in supervision and its constitutional tooling, best-in-class. But three stated invariants do not survive contact with the code: "five primitives and nothing else" is contradicted by a sixth (timing) subsystem carrying configurable scheduling *policy*; "protocol-first — Core depends on the protocol, not any implementation" is false internally (the kernel constructs and type-binds concrete `_EventBus`/`_HookSystem`/`_Registry`/`Core`); and "simplicity over features" is strained by an RFC 0001-0006 secure-capability layer whose centerpiece guard is self-described as bypassable. None are fatal, and all are defensible design choices — but the docs oversell the purity the code delivers, which is itself a philosophy-drift signal the framework claims to guard against.

**API simplicity: A-**
The author-facing surface is small, curated, constitutionally documented, drift-tested, and honestly versioned — genuinely top-tier. Points come off for author-visible redundancy that the team has documented-but-not-eliminated (`self.get_capability` vs `self.core.get_capability`), a `config()` accessor that silently bleeds into `CoreConfig`, the three-way overloading of "hook," and — most sharply — a broken, invocation-teaching example sitting in the `Plugin` base-class docstring (B3) that directly contradicts the descriptive-only `PluginView` the kernel went to great lengths to enforce.

### Highest-value fixes (ordered)
1. **B3** — fix the `Plugin` docstring example (`_base.py:52-56`); it actively mis-teaches. (HIGH, trivial effort)
2. **A7/B2** — reconcile the docs' hard counts with code: either call timing a primitive and say "six," or move it, and change "eleven"→"twelve". (MEDIUM)
3. **B5** — decide whether `Plugin.config()` should reach `CoreConfig` at all. (MEDIUM)
4. **A6** — reassess whether the sealed return guard earns its complexity given it is not a boundary. (MEDIUM, design discussion)

Relevant files: `/home/bork/vault/1-Projects/uxok/src/uxok/plugin/_base.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/core/_core.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/core/_core_facet.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/core/_capability_system.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/timing/_clock.py`, `/home/bork/vault/1-Projects/uxok/src/uxok/protocols/config.py`, `/home/bork/vault/1-Projects/uxok/docs/explanation/framework-philosophy.md`, `/home/bork/vault/1-Projects/uxok/docs/explanation/architecture-overview.md`, `/home/bork/vault/1-Projects/uxok/docs/manifests/API.md`.

---

# Report 4 — Documentation ↔ Code Coherence (documentation-auditor)

I have completed the audit. All verification steps are done: both examples execute verbatim, the strict docs build is green, ruff is clean, tests collect and pass, and the constitutional exports/signatures match source. Below is the report.

---

## Documentation ↔ Code Coherence Audit — uxok kernel

**Scope:** `docs/` (API.md, tutorials, how-to, explanation, reference, manifests, proposals), `README.md`, `CHANGELOG.md`, `CLAUDE.md` claims, and `examples/` as executable docs. Working-tree state on branch `docs/modular-getting-started-tutorial` (uncommitted tutorial + `examples/getting_started/*` rework).

**Verdict up front:** the *shipped* public documentation is in strong shape. Every reader-facing surface I could execute, ran; the constitution matches the code on the surfaces it governs; the strict build gate is green. The only genuine drift lives in two non-shipped `manifests/` files that are excluded from the site and superseded by their `explanation/` counterparts.

### What I verified as CORRECT (no finding)

- **`examples/getting_started` runs verbatim.** `python -m examples.getting_started.host` (the command the host docstring and tutorial give) exits 0 and prints exactly the four lines the tutorial promises.
- **README quick-start runs verbatim.** Extracted the fenced block from `README.md:23-73` unmodified — exits 0, correct output. The `Agent(done)` direct-registration variant works against the current coreless-constructor / no-auto-start kernel.
- **`mkdocs build --strict` passes.** Internal markdown links across the shipped Diátaxis layer resolve (the `validation.not_found` gate + `--strict` would fail otherwise). `manifests/` and `proposals/` are in `exclude_docs`, so they never reach the site.
- **Constitution exports match source exactly.** `uxok.__all__`, `uxok.protocols.__all__` (`AdmissionResult, Core, CoreConfig, CoreState, Event, Hook, PluginMetadata, PluginProtocol`), and `uxok.registry.__all__` (`CapabilityInfo, PluginCollection, PluginView`) all match API.md §1/§11 name-for-name.
- **Signatures match.** `Plugin.__init__` (keyword-only, coreless), `Core.__init__(**kwargs)`, `CoreConfig` fields (§7.3 matches `protocols/config.py` field-for-field, including removal of `tick_queue_*`/`blocked_plugins`), `Event`, `Hook`, `CoreState` (five members, no `ERROR`), the `get_capability` overloads — all consistent.
- **No stale terminology in reader-facing docs.** Grep for `TickGate`, "serial dispatch", "auto-start", `blocked_plugins`, `hook_every`, `emit_after`, `PluginProxy`, `@on(`, positional-`core` constructor (`super().__init__(core, …)`) — **zero hits** in `tutorials/`, `how-to/`, `explanation/`, `README.md`. The recently-removed auto-start and coreless-constructor changes are fully propagated.
- **Capability idiom is consistent** with the `[Unreleased]` CHANGELOG guidance: reader-facing plugin code uses `self.get_capability(...)`; bare `core.get_capability(...)` appears only in host-level examples, which is correct.
- **CLAUDE.md/README tooling claims hold.** `ruff check src tests examples` → "All checks passed!" (zero violations, as claimed). `pytest --collect-only` → 893/901 collected (8 perf deselected). `tests/test_getting_started.py` passes.

### Findings

#### MEDIUM

**M1 — Ghost/removed config fields presented as real usage in `manifests/FRAMEWORK_PHILOSOPHY.md`**
`docs/manifests/FRAMEWORK_PHILOSOPHY.md:78-79` and `:93-95` show `Core(tick_queue_max_size=10_000, tick_queue_overflow="block")` under the "Simple Configuration" / "User Choice" sections as live API. Those three fields (`tick_queue_max_size`, `tick_queue_overflow`, and the related serial-gate config) were **removed** — API.md §15 lists them Removed ("Serial gate queue is gone; no dispatch queue exists"), and `protocols/config.py` no longer defines them. A reader copying this snippet gets a `TypeError` from the dataclass. Not in a section flagged speculative. (Blast radius bounded: excluded from the built site.)
Code ref: `src/uxok/protocols/config.py:41-72` · Doc ref: `docs/manifests/API.md:1182-1184`

**M2 — Removed `Core.registry` used in `manifests/KERNEL_ARCHITECTURE.md`**
`docs/manifests/KERNEL_ARCHITECTURE.md:55` shows `plugin = await core.registry.get(plugin_id)` inside the real "3. Plugin Registry" primitive example (not the self-flagged speculative "Future Enhancements" block at ~line 576). `Core.registry` was removed — API.md §15: "Removed → Use `core.list()` and `core.get_plugin()`." The correct call is `await core.get_plugin(plugin_id)`.
Code ref: `src/uxok/core/_core.py:631` (`get_plugin`) · Doc ref: `docs/manifests/API.md:1172`

**M3 — Stale duplicate "why" layer: `manifests/` vs `explanation/` (mesh-health)**
`manifests/FRAMEWORK_PHILOSOPHY.md` and `manifests/KERNEL_ARCHITECTURE.md` duplicate the canonical, current `explanation/framework-philosophy.md` and `explanation/architecture-overview.md`. CLAUDE.md's "Key Documentation" points philosophy/architecture at the `explanation/` pages, and the `manifests` copies are excluded from the build — yet they are retained and have drifted (M1/M2 are the drift). This is exactly the "duplicate/stale/contradictory pointer" the project's mesh goal wants gone: two homes for one system-level "why," one of them stale. The `explanation/` pages are clean; the `manifests/` copies are the liability. (Note: `manifests/API.md` is *not* affected — it is current and correct and remains the single source of truth for the API.)
Recommendation is retirement/redirect, not rewrite — but that's the caller's call, not mine to execute.

#### LOW

**L1 — Constitution cites stale source line anchors for `Core`**
API.md §2 says `class Core … src/uxok/core/_core.py:51` and forwards kwargs at `_core.py:87`; the actual class is at `_core.py:82` and the `CoreConfig(**kwargs)` forward at `:108`. §3 cites `Plugin … _base.py:27`; actual is `:31`. (The other anchors — `Event events.py:12`, `Hook hooks.py:24`, `CoreConfig config.py:9`, `CoreState _types.py:13`, `PluginMetadata plugin.py:11`, `ConfigField config_field.py:19`, and `Plugin` `*,` at `_base.py:69` — are all accurate.) Line anchors are a fragile drift vector in a document that markets itself as the target of truth; the `Core` ones have slipped as the file grew.

**L2 — Tutorial overstates the sync guarantee**
`docs/tutorials/getting-started.md:63-64` claims the example is "kept in sync with what you see here by `tests/test_getting_started.py`." That test only asserts the example's **runtime output** (`assert printed == _EXPECTED`, `test_getting_started.py:40-51`); it does not assert textual equality between the tutorial's code blocks and the example files. The two happen to be identical right now (I diffed them by eye — they match), but a snippet could drift from the source file without the test catching it as long as the output still prints. Weaker guarantee than the sentence implies.

#### INFO

**I1 — Build emits a third-party "ProperDocs" advertisement.** `mkdocs build` prints a large red banner urging migration off MkDocs to a package called `properdocs` (injected "by one of the plugins that you depend on"). This is nuisance/supply-chain noise from a dependency, not a doc defect — worth a glance at which plugin injects it. Set `DISABLE_MKDOCS_2_WARNING=true` to silence.

**I2 — `SUMMARY.md contains an unrecognized relative link 'reference/'`** appears as an INFO during build. Benign and expected — it is the literate-nav trailing-slash convention that nests the generated reference section; strict build still passes.

**I3 — Containment note.** `manifests/` and `proposals/` are in `exclude_docs`, so M1-M3's stale content does not reach the public website. That bounds the severity — the drift is repo-/maintainer-facing, not visitor-facing.

### Coverage map (reader-facing surfaces)

| Surface | Runs/Builds | Accurate vs code | Notes |
|---|---|---|---|
| `examples/getting_started` | ✓ exits 0 | ✓ | verified via `python -m …host` |
| README quick-start | ✓ exits 0 | ✓ | run verbatim |
| `tutorials/getting-started.md` | ✓ (strict) | ✓ | L2 (sync-claim nuance) |
| `how-to/` (22 pages) | ✓ (strict) | ✓ | idiom + host/plugin split correct |
| `explanation/` (9 pages) | ✓ (strict) | ✓ | canonical, no stale terms |
| `manifests/API.md` | — (excluded) | ✓ surface / L1 anchors | constitution; current |
| `manifests/FRAMEWORK_PHILOSOPHY.md` | — (excluded) | ✗ | M1, M3 |
| `manifests/KERNEL_ARCHITECTURE.md` | — (excluded) | ✗ (real section) | M2, M3 |
| `CHANGELOG.md` | — | ✓ | matches removed-API table |

### Docs-health verdict

The documentation a user actually reads is **coherent with the code and executably correct**. That is the hard part and this project nails it: two independent runnable entry points both pass verbatim, the strict build gate catches broken links before merge, the constitution's public surface matches source name-for-name and signature-for-signature, and the invasive recent refactors (auto-start removal, coreless constructors, serial-gate deletion, blocklist removal) have been scrubbed clean from every reader-facing page. The rot is confined and quarantined: two `manifests/` design essays that the build already excludes and that CLAUDE.md already routes around, carrying removed config fields and a removed `Core.registry` call, plus a handful of stale line-number anchors in the constitution. Nothing here misleads a reader following the tutorials or the API reference; the damage is limited to a maintainer who opens the wrong (excluded, superseded) file. Fixing it is mostly deletion/redirection, not authoring.

**Grade: A−.** Docked from A only because the project's own stated goal is a single cross-linked mesh with *no* stale/contradictory pointers, and the drifted duplicate `manifests/` essays (M1-M3) are precisely that — a second, stale home for the system-level "why."

**Three things done well:**
1. **Executable documentation that actually executes.** Both the modular example and the README snippet run unmodified, and `tests/test_getting_started.py` pins the example's output — docs are treated as code, not prose.
2. **A disciplined constitution.** API.md's §15 "Removed API" table is unusually rigorous; every removal I probed (`tick_queue_*`, `blocked_plugins`, `PluginView.call`, `Core.registry`) is both gone from source and correctly recorded — that table is why the reader-facing docs stayed clean through aggressive pre-1.0 churn.
3. **A real merge gate.** `mkdocs build --strict` with `validation.not_found` promoted to error means broken cross-references can't silently ship, and the layer separation (`exclude_docs` for `manifests/`/`proposals/`/`agent_data/`) keeps design history out of the user's face.

*One process note for the caller:* M1-M3 all reduce to "retire or redirect the two stale `manifests/` essays," and L1 is a one-time line-anchor refresh in API.md — small, mechanical follow-ups, none of which I performed (audit only, no docs written or modified).

---

# Report 5 — PyPI Packaging Readiness (cicd-infrastructure-engineer)

Audit complete. All build/install evidence gathered. Here is the report.

---

## PyPI Packaging Readiness Audit — uxok

**Scope:** read-and-build-only. Repo untouched; all artifacts built into scratchpad (`.../scratchpad/dist`), fresh venvs used for build and install. Audited against my healthy-repo model (versioning-by-tagging, scripted operations, clean diffs, faithful CI) and uxok's stated conventions.

**Environment note:** the shell's default interpreter was another project's venv (`exokern/.venv`, no `build` module). I built and tested in dedicated scratchpad venvs with `build 1.5.0` / `twine 6.2.0` against a clean rsync copy of the repo, so the verdict reflects a from-scratch build, not local caches.

### Evidence — the good baseline

- **PyPI name `uxok` is FREE.** `pip index versions uxok` → `No matching distribution found`; `https://pypi.org/pypi/uxok/json` → `HTTP 404`. The name is claimable.
- **Build is clean.** `python -m build` → `Successfully built uxok-0.1.0.tar.gz and uxok-0.1.0-py3-none-any.whl`.
- **`twine check` PASSED both artifacts** (long-description renders for the PyPI page).
- **Wheel ships ONLY the kernel.** Top-level entries: `uxok/…` + `uxok-0.1.0.dist-info/`. Explicit leakage probe for `tests/examples/docs/scripts` → `NONE`. `uxok/py.typed` is present in the wheel and in the installed package.
- **Runtime deps are empty** — `dependencies = []`, and METADATA shows every `pytest*/ruff/mypy/mkdocs*` under `extra == "dev"` / `extra == "docs"`. Zero test-only deps leaking into runtime. This is exactly right.
- **Version single-source is sound.** `pyproject.version = "0.1.0"` (static) is the one source; `src/uxok/__init__.py` derives `__version__` via `importlib.metadata.version("uxok")` with a `0.0.0.dev0` source-tree fallback — no hand-synced constant to drift. Installed wheel reports `__version__ = 0.1.0`.
- **Install smoke test PASSED.** In a clean venv, `pip install` the wheel, then ran the README quickstart verbatim:
  ```
  user:  hello there
  agent: Cheerfully: you said 'hello there'.
  user:  what's the weather like?
  agent: Cheerfully: you said 'what's the weather like?'.
  SMOKE TEST OK
  ```
- **License is modern and consistent.** PEP 639 `License-Expression: MIT` + `license-files = ["LICENSE"]`; `LICENSE` ships in wheel (`dist-info/licenses/LICENSE`) and sdist. Correctly OMITS the legacy `License :: OSI Approved` classifier (which would now conflict with the SPDX expression). `Typing :: Typed` classifier present and backed by a real `py.typed`.
- **requires-python `>=3.12` matches classifiers** (3.12, 3.13).

### Findings

#### CRITICAL

**C1 — CI `lint` and `format` jobs point at a nonexistent `plugins/` path; both fail on every run.**
`.github/workflows/ci.yml:34` runs `ruff check src tests plugins` and `:51` runs `ruff format --check src tests plugins`. There is no `plugins/` directory (the example plugins live in `examples/`; CLAUDE.md itself documents the scope as `src tests examples`). Verified against the clean copy:
```
ruff check  src tests plugins  → exit 1   (E902 No such file or directory --> plugins)
ruff format --check ... plugins → exit 2
ruff check  src tests examples → exit 0   (control)
```
Two consequences: (a) the two most basic gates are RED on every push/PR, so no branch protection can honestly require them and the staging→main promotion path has no working style gate; (b) `examples/` is never actually linted or format-checked despite being in scope. This is a broken release gate → CRITICAL.
**Fix:** change both commands to `ruff check src tests examples` and `ruff format --check src tests examples` (match CLAUDE.md). If a `plugins/` tree is planned, create it or drop the token — do not reference paths that don't exist.

#### HIGH

**H1 — No publish/release workflow exists. The "versioning by tagging → PyPI" pipeline is entirely absent.**
`.github/workflows/` contains only `ci.yml`, `codeql.yml`, `docs.yml`. There is no tag-triggered build-and-publish, no `pypa/gh-action-pypi-publish`, no PyPI Trusted Publishing (OIDC) config. Corroborating this: `git tag -l` returns **zero tags**, even though `pyproject` and `CHANGELOG.md` both claim `0.1.0` (`## [0.1.0] — 2026-06-23`). So the tagging model is aspirational, not operational — nothing turns a tag into a release.
**Fix:** add `.github/workflows/release.yml` triggered on `push: tags: ['v*']` that (1) builds sdist+wheel with `python -m build`, (2) `twine check dist/*`, (3) publishes via Trusted Publishing (`permissions: id-token: write`, `pypa/gh-action-pypi-publish`, pinned by SHA) to a `pypi` GitHub Environment. Add a guard step asserting the tag matches `project.version`. Register the PyPI Trusted Publisher for `hiddenfalls42/uxok` before first publish.

**H2 — `scripts/dev_utilities/bump_version.py` is broken and non-atomic against the actual CHANGELOG.**
The changelog-roll regex is `_UNRELEASED_RE = re.compile(r"^## Unreleased\s*$")` (line 41), but the real heading is `## [Unreleased]` (Keep-a-Changelog bracket form). Verified: `grep -E '^## Unreleased\s*$' CHANGELOG.md` → **NO MATCH**, so `roll_changelog()` hits `sys.exit("could not find a ## Unreleased heading")` (line 85). Worse, `main()` calls `bump_pyproject()` **before** `roll_changelog()` (lines 124-125), so a real invocation mutates `pyproject.toml` to the new version and *then* aborts — leaving the working tree half-bumped, no changelog entry, no tag. A release script that fails partway is worse than none.
Secondary: even if the regex matched, it writes `## X.Y.Z (DATE)` (line 87) while the established style is `## [X.Y.Z] — DATE` (em dash, brackets) — format drift.
**Fix:** match `^## \[Unreleased\]\s*$`; emit `## [{new}] — {today}`; and make the operation atomic — compute all edits first, write nothing until every step (pyproject, changelog, and the API.md/CHANGELOG coupling check) succeeds, or wrap in a dry-run-verified transaction. Given the constitution rule (API.md + CHANGELOG in the same commit), the bump script should also refuse to proceed if `docs/manifests/API.md` wasn't touched alongside a breaking change.

#### MEDIUM

**M1 — Sparse `[project.urls]`; PyPI sidebar will be nearly empty.**
`pyproject.toml:29-31` defines only `Repository` and `Changelog`. Missing `Homepage`, `Documentation` (the docs site `https://hiddenfalls42.github.io/uxok/` exists and is linked from the README), and `Issues`/`Bug Tracker` (README already points users at `/issues`). METADATA confirms only two `Project-URL` lines.
**Fix:** add `Homepage`, `Documentation = "https://hiddenfalls42.github.io/uxok/"`, and `Issues = "https://github.com/hiddenfalls42/uxok/issues"`.

**M2 — sdist contents are uncontrolled: it ships `tests/` (37 entries) but not docs/examples/scripts, and there is no `MANIFEST.in`.**
`tar tzf uxok-0.1.0.tar.gz` top level: `LICENSE, PKG-INFO, README.md, pyproject.toml, setup.cfg, src/ (58), tests/ (37)`. Shipping the test suite in the sdist while excluding everything else is inconsistent and unintentional (setuptools default heuristic, not a deliberate choice). Not a blocker — twine passed and this is common — but it's uncurated packaging.
**Fix:** add a `MANIFEST.in` that deliberately controls the sdist: `prune tests`, `prune .github`, ensure `graft src/uxok` and `include LICENSE README.md CHANGELOG.md`. Decide intentionally whether tests belong in the sdist (many projects exclude them to keep it lean).

#### LOW

**L1 — No tag↔version consistency enforcement.** Static `version` in pyproject plus a hand-run tagger invites the tag/pyproject/CHANGELOG divergence already visible (0.1.0 declared, no `v0.1.0` tag). Either adopt `setuptools_scm` (version derived from the tag — one source, impossible to drift) or add a CI/release assertion that `git describe --tags` equals `project.version`. Given H1's release workflow, fold the check in there.

**L2 — CI `security` job runs `safety check` (workflow line 149).** `safety check` is deprecated in favor of `safety scan` and now generally requires an account/auth; it will emit deprecation noise or fail to authenticate. It's `continue-on-error: true` so non-blocking, but it's dead-ish tooling. Migrate to `safety scan` or drop in favor of `pip-audit`.

**L3 — README `pip install uxok` will 404 until first publish.** Expected pre-publish, not a defect; noted only so the first release is sequenced before anyone follows the README. Name is confirmed free, so the instruction becomes correct the moment H1 ships.

### Verdict

**Ready to publish? WITH FIXES.**

The **artifact** is genuinely publish-grade: clean build, twine-valid, zero-dependency runtime, correct `py.typed`/typing story, no source leakage in the wheel, sound single-source versioning, and a quickstart that runs end-to-end from the installed wheel. If you manually `twine upload` the built wheel today, it would install and work.

What's not ready is the **release pipeline and its hygiene around it**: the CI style gates are red (C1), there is no automated/trusted publish path (H1), and the one piece of release automation that exists is broken and non-atomic (H2). For a *credible, repeatable* 0.x release — not a one-off manual upload — those must be fixed first.

**Grade: B−.** (Artifact quality alone: A−. Release automation/pipeline: D. Blended down by the broken gates and missing publish path.)

### Prioritized fix checklist

1. **[CRITICAL]** Fix `ci.yml` ruff scope: `plugins` → `examples` in both the `lint` (line 34) and `format` (line 51) jobs. Confirm green.
2. **[HIGH]** Add tag-triggered `release.yml` with PyPI Trusted Publishing (OIDC, pinned actions, `pypi` environment) + a tag==version guard; register the Trusted Publisher on PyPI.
3. **[HIGH]** Repair `bump_version.py`: match `## [Unreleased]`, emit the `## [X.Y.Z] — DATE` format, and make it atomic (no partial pyproject mutation on failure); enforce the API.md+CHANGELOG coupling.
4. **[MEDIUM]** Flesh out `[project.urls]` (Homepage, Documentation, Issues).
5. **[MEDIUM]** Add `MANIFEST.in` to make sdist contents deliberate (decide on tests).
6. **[LOW]** Add tag/version consistency enforcement (setuptools_scm or a release-time assertion); create the `v0.1.0` tag to reconcile the existing 0.1.0 claim.
7. **[LOW]** Replace deprecated `safety check` with `safety scan`/`pip-audit`.

**Key file refs:** `/home/bork/vault/1-Projects/uxok/.github/workflows/ci.yml` (lines 34, 51, 149), `/home/bork/vault/1-Projects/uxok/pyproject.toml` (lines 6-31, 74-75), `/home/bork/vault/1-Projects/uxok/scripts/dev_utilities/bump_version.py` (lines 41, 81-96, 124-125), `/home/bork/vault/1-Projects/uxok/CHANGELOG.md` (line 9 heading), `/home/bork/vault/1-Projects/uxok/src/uxok/__init__.py` (lines 16-19), `/home/bork/vault/1-Projects/uxok/src/uxok/py.typed`. Built artifacts for inspection: `/tmp/claude-1000/-home-bork-vault-1-Projects-uxok/777e49d0-7fb1-4b67-9099-9c85e3a8ddb9/scratchpad/dist/`.
