# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

**IMPORTANT**: Think and respond critically. Tell the truth, ESPECIALLY when the user is
incorrect — no sugarcoating, no praise. NEVER say "you're absolutely right."

## Project Overview

**uxok** is an experimental, hot-loading plugin microkernel for Python, in the spirit of
the MIT exokernel (mechanism not policy; secure bindings; downloaded policy — but no
protection boundary between plugins, which share a process). The core provides only
essential primitives (event bus, hooks, plugin registry, capability system); everything
else is a plugin.

## Constitutional API

`docs/manifests/API.md` is the **single source of truth** for the public API — the
constitution. All API changes get discussed and reflected there first.

- Pre-1.0: breaking changes allowed, but each must update `API.md` **and** `CHANGELOG.md`
  in the same commit.
- At 1.0 the API stabilizes and follows SemVer.

## Essential Commands

```bash
# Fast test run (no coverage)
pytest

# Coverage gate (91.5% branch floor — raise as it improves, NEVER lower; CI enforces it).
# COVERAGE_RUN=1 disables Hypothesis deadlines (instrumentation slows the tick system past
# the 200ms default and sends failing examples into endless shrink loops).
# COVERAGE_CORE=sysmon uses the fast Python 3.12 monitoring backend.
COVERAGE_RUN=1 COVERAGE_CORE=sysmon pytest tests/ --cov=src/uxok --cov-branch --cov-fail-under=91.5

# Targeted runs
pytest tests/integration/ -v        # integration (incl. hot reload)
pytest tests/properties/ -v         # property-based (Hypothesis)
pytest tests/test_core_lifecycle.py -v -k test_name
pytest -m performance               # perf tests (deselected by default)

# Lint / format / types — ZERO ruff violations enforced (rule set curated in pyproject.toml;
# tests relax annotation pedantry). No per-change noqa without a reason comment.
ruff check src tests plugins
ruff format src tests plugins
mypy src

# Dev install
pip install -e .[dev]
```

## Kernel Architecture

**The kernel is `src/uxok/` and nothing else.** Reference plugins under `plugins/` (e.g.
`example_host/`) are built *with* the kernel, not part of it. Dependency arrow points one
way: plugins import the kernel, never the reverse. Enforced by
`tests/test_imports.py::TestKernelBoundary`.

The core provides **ONLY** these primitives — everything else is a plugin:

1. **Event Bus** — `src/uxok/events/_bus.py` — publish-subscribe inter-plugin comms
2. **Hook System** — `src/uxok/hooks/_system.py` — priority-ordered extension points
3. **Plugin Registry** — `src/uxok/registry/impl.py` — registration, lookup, deps
4. **Capability System** — `src/uxok/core/_capability_system.py` — dependency declarations
5. **Plugin** — `src/uxok/plugin/_base.py` — developer-experience abstraction

```
src/uxok/            THE KERNEL (nothing else is)
├── core/            orchestrator, capability system, state manager
├── events/          event bus + subscription manager
├── hooks/           hook system + cache
├── plugin/          plugin base class, decorators, config fields
├── protocols/       protocol definitions (the contracts) — a package, not a file
├── registry/        registry, proxy/collection introspection
├── timing/          tick clock, gate, scheduler
└── utils/           helpers, task manager, async primitives

plugins/             committed reference plugins (e.g. example_host/)
```

Full architecture: `docs/explanation/architecture-overview.md`.

## Philosophy (summary — full text in `docs/explanation/framework-philosophy.md`)

uxok is a **framework, not a product**: clean building blocks, not opinionated solutions.
Protect its simplicity fiercely. Before any change, weigh:

- Framework (building blocks) over product (complete solutions)
- Convention over configuration (e.g. `emit(at_tick=N)`, not a `DeferredEvent` class)
- Simplicity over features — remove complexity before adding it
- Protocol-first: depend on `EventBus`/`CoreConfig` protocols, never on `EventBusImpl` or
  concrete configs
- Opt-in with sensible defaults, never forced behavior; predictable (same input → same output)
- If it can be built from primitives, it's a plugin, not core

## Implementation Notes

### Configuration (single source of truth)
- All settings live in one `CoreConfig` dataclass — `src/uxok/protocols/config.py`. NEVER
  create separate config classes or scatter settings.
- New fields need sensible defaults (backward compat). All validation is centralized in
  `CoreConfig.__post_init__()`.

### State management
- `CoreState` graph: INITIALIZED → RUNNING → STOPPING → STOPPED; STOPPING → FAILED
  (teardown itself failed); STOPPED/FAILED → INITIALIZED (restart with a fresh plugin graph).
- STOPPING is the drain phase: `core.stop()` is a full teardown that unregisters every
  plugin, leaving an empty reusable core. Plugin instances are one-shot; state continuity is
  explicit via `get_state()`/`restore_state()`.
- Plugin-level failures are signals (`core.plugin_error`, `core.hook_error`), not core
  states; supervision policy lives in plugins, not the kernel.
- Transitions are validated; `core.state.changed` hook fires on every transition. `__state`
  is name-mangled for true privacy.

### Plugin lifecycle
1. Instantiate (user code)
2. `core.register_plugin(plugin)` — registers, validates capabilities
3. `start()` — processes decorators, calls `on_start()`
4. Runs (event handlers, hooks)
5. `core.unregister_plugin(plugin_id)` — calls `on_stop()`, cleanup

Plugin surface: auto-name from class (override `name=`); `emit()`, `config()`, `hook()`;
declarative `provides`/`requires`/`hooks_consumed`/`events_published`; capabilities via
`self.core.get_capability()`; lifecycle `on_start()`/`on_stop()`.

### Async invariants (the load-bearing rules)
- All core operations are async.
- **Lock-free critical sections**: registry and capability-system state mutations are
  synchronous — NEVER add an `await` inside one. Under cooperative asyncio that makes them
  atomic without locks (decision #12). An await inside a mutation requires reintroducing a
  lock around it.
- **Lifecycle lock**: multi-step lifecycle ops (register/load/unregister/swap) span awaits
  and are serialized by a single reentrant `_ReentrantLock` in `Core`. Per-plugin
  `_active_operations` guards remain for id-level protection.
- **Background tasks**: use `Plugin.create_background_task()` — tracked in the plugin's
  `_task_manager`, cancelled automatically on stop/reload.
- **Event dispatch is concurrent fire-and-forget**: `publish()` dispatches each subscriber
  as an independent tracked task and returns immediately. Ordering is causal (a handler's
  nested emit/hook completes before it continues), not global. There is no dispatch queue,
  timeout, or cross-publisher serialization (the serial `TickGate` was deleted).

### Testing
- 91.5% branch coverage floor (baseline 2026-06-12: ~91.8–91.9%); raise it, never lower it.
- `@pytest.mark.asyncio` for async tests. Favor integration over unit — test real
  workflows; mock external deps, not internal components. Use Hypothesis for property tests.

## Agents, audits, and docs

- For non-trivial changes, audit before merging with the relevant project agent
  (`architecture-analyzer` for architecture/quality, `documentation-auditor` for docs) via
  the **Agent** tool, and pass the matching skill from `.claude/skills/`
  (`developer-documentation-blueprint-SKILL`, `public-document-blueprint-SKILL`,
  `writing-style-SKILL`). Fix CRITICAL/HIGH findings, then re-audit.
- Docs follow an audit→write→re-audit loop, **one note at a time**: `technical-writer`
  writes, `documentation-auditor` verifies against the blueprint skill. Verifications land in
  `docs/agent_data/compliance-auditor/verifications/`; auditor handoffs in
  `docs/agent_data/doc-auditor/handoffs/`.

## Key Documentation

- `docs/manifests/API.md` — **constitutional API reference** (single source of truth)
- `docs/explanation/architecture-overview.md` — kernel architecture
- `docs/explanation/framework-philosophy.md` — design philosophy
- `docs/` — public docs (tutorials, how-to, explanation, reference)
