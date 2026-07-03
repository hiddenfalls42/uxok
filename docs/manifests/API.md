# uxok — Constitutional API

> **THIS DOCUMENT IS THE TARGET OF TRUTH.** When this document and the code disagree,
> the CODE is wrong and must be changed to match. Compliance agents use this document
> to detect non-conformance in the implementation.

> **Versioning policy:** Pre-1.0, breaking changes are allowed; each one must update
> `CHANGELOG.md` and this document in the same commit. At 1.0 the API stabilizes and
> follows SemVer.

> **Import package:** the kernel is imported as `uxok` (distribution name `uxok` on
> PyPI). The import package was renamed from `orion_core` to `uxok` pre-1.0; the public
> API surface below is otherwise unchanged. All examples import from `uxok`.

---

## Contents

1. [Top-level public exports](#1-top-level-public-exports)
2. [Core](#2-core)
   - [2.1 Constructor](#21-constructor)
   - [2.2 Async methods](#22-async-methods)
   - [2.3 Properties](#23-properties)
3. [Plugin](#3-plugin)
   - [3.1 Constructor](#31-constructor)
   - [3.2 Methods](#32-methods)
   - [3.3 Properties](#33-properties)
4. [Decorators](#4-decorators)
5. [EventBus — `core.events`](#5-eventbus--coreevents)
6. [HookSystem — `core.hooks`](#6-hooksystem--corehooks)
7. [Data structures](#7-data-structures)
   - [7.1 Event](#71-event)
   - [7.2 Hook](#72-hook)
   - [7.3 CoreConfig](#73-coreconfig)
   - [7.4 CoreState](#74-corestate)
   - [7.5 PluginMetadata](#75-pluginmetadata)
8. [Exceptions](#8-exceptions)
9. [ConfigField and REQUIRED](#9-configfield-and-required)
10. [PluginCollection and PluginView](#10-plugincollection-and-pluginview)
11. [Subpackage public exports](#11-subpackage-public-exports)
12. [Framework event contracts](#12-framework-event-contracts)
13. [State machine](#13-state-machine)
14. [Hot-reload swap sequence](#14-hot-reload-swap-sequence)
15. [Removed API](#15-removed-api)

---

## 1. Top-level public exports

`src/uxok/__init__.py:6`

```python
__all__ = [
    "REQUIRED",
    "BatchLoadError",
    "CapabilityAccessError",
    "CapabilityError",
    "ConfigField",
    "Core",
    "CoreError",
    "MissingCapabilityError",
    "Plugin",
    "PluginError",
    "StalePluginError",
    "event",
    "hook",
]
```

```python
from uxok import (
    Core, Plugin, event, hook,
    ConfigField, REQUIRED,
    CoreError, PluginError, CapabilityError,
    MissingCapabilityError, BatchLoadError, CapabilityAccessError, StalePluginError,
)
```

The top-level surface is **curated flat**: it holds exactly what an ordinary plugin
author and the minimal host application touch directly — the two classes everyone
imports (`Core`, `Plugin`), the two decorators on the first line of every plugin
(`event`, `hook`), config-schema construction (`ConfigField`, `REQUIRED`), and the
exceptions the framework raises *into* caller code (`CoreError`, `PluginError`,
`CapabilityError`, `MissingCapabilityError`, `CapabilityAccessError`, `BatchLoadError`
— see [§8](#8-exceptions)). These names are
*re-exported* here; their definition homes are `uxok.plugin` and
`uxok.errors`, both of which remain importable.

Everything else stays subpackage-only by design: `handle_errors` (advanced, in
`uxok.plugin`), `Event`/`CoreConfig`/protocol types (in `uxok.protocols`),
and the `EventBus`/`HookSystem` surfaces (reached via `core.events`/`core.hooks`).

**The line is drawn by *use*, not by tidiness.** A name is top-level iff author or host
code must *write it* to do its job — one of:

- **construct it** — `ConfigField`, `REQUIRED`;
- **decorate with it** — `event`, `hook`;
- **subclass or instantiate it** — `Plugin`, `Core`;
- **catch it** — `CoreError`, `PluginError`, `CapabilityError`, `MissingCapabilityError`,
  `CapabilityAccessError` (an `except` clause forces the author to write the name).

A name the kernel only *hands to* the author — received ready-to-use and read by
attribute, never imported or constructed — is **not** top-level, even when a type
annotation could mention it. This is why `Event`, `Hook`, and `PluginMetadata` stay in
`uxok.protocols`: the core does the setup so the author does not. `CoreConfig` is
the same case for the host — `Core(**kwargs)` takes keyword arguments, so ordinary host
code never writes `CoreConfig`; only reflective field introspection imports it.

---

## 2. Core

`class Core(CoreProtocol)` — `src/uxok/core/_core.py:51`.
Re-exported at `uxok.core.Core` and `uxok.Core`.

### 2.1 Constructor

```python
def __init__(self, **kwargs: Any) -> None
```

`Core` takes `**kwargs` only. There are no explicit positional or keyword parameters.
kwargs are forwarded verbatim to `CoreConfig(**kwargs)` (`_core.py:87`); validation
happens in `CoreConfig.__post_init__`. Any kwarg not a `CoreConfig` field raises
`TypeError` from the dataclass. Accepted kwargs are exactly the `CoreConfig` fields:

| kwarg | Type | Default |
|---|---|---|
| `max_plugins` | `int` | `100` |
| `hook_precaching` | `str` | `"on_core_start"` |
| `capability_collision` | `str` | `"last_wins_with_warning"` |
| `capability_selection` | `str` | `"last_registered"` |
| `capability_missing` | `str` | `"raise"` |
| `capability_access` | `str` | `"open"` |
| `tick_rate` | `int` | `1000` |
| `tick_slip_threshold` | `int` | `5` |
| `tick_precision` | `str` | `"sleep"` |
| `tick_busy_wait_us` | `int` | `200` |
| `tick_catchup` | `str` | `"skip"` |
| `plugin_configs` | `dict[str, dict[str, Any]]` | `{}` (default_factory) |

See [§7.3 CoreConfig](#73-coreconfig) for the accepted enum values and numeric bounds on each field.

### 2.2 Async methods

| Member | Signature | Returns | Raises |
|---|---|---|---|
| `start` | `async def start(self) -> None` | `None` | `CoreError` if not INITIALIZED |
| `stop` | `async def stop(self) -> None` | `None` | `CoreError` if not in a stoppable state |
| `register_plugin` | `async def register_plugin(self, plugin: PluginProtocol) -> bool` | `True` if registered | `CoreError` if not RUNNING, else `PluginError`, `MissingCapabilityError` |
| `check_plugin` | `async def check_plugin(self, candidate: PluginProtocol) -> AdmissionResult` | `AdmissionResult` (advisory; never raises) | — |
| `unregister_plugin` | `async def unregister_plugin(self, plugin_id: UUID \| str, *, force: bool = False) -> bool` | `False` if not found | `PluginError` (active-operation or dependents present) |
| `load_plugin` | `async def load_plugin(self, code: str, origin: str \| None = None) -> bool` | `True` if loaded or reloaded | `CoreError` if not RUNNING, else `PluginError` (compile or module-execution failure, or 0 or >1 Plugin subclass found) |
| `load_plugins` | `async def load_plugins(self, sources: Iterable[tuple[str, str \| None]]) -> tuple[str, ...]` | plugin names, in commit (topological) order | `CoreError` if not RUNNING, else `BatchLoadError` (phase `"plan"` or `"commit"`, with `cause`, `installed`, `failed`) |
| `get_plugin` | `async def get_plugin(self, plugin_id: UUID \| str) -> PluginProtocol \| None` | live instance or `None` | — |
| `list` | `async def list(self) -> PluginCollection` | `PluginCollection` — the single discovery surface (plugins **and** capabilities; see [§10](#10-plugincollection-and-pluginview)) | — |
| `get_capability` | `async def get_capability(self, capability: str \| type, *, tag: str \| None = None) -> Any` | provider | `CapabilityError` if unavailable; `PluginError` if provider fails protocol contract |
| `__aenter__` | `async def __aenter__(self) -> Core` | `self` after `start()` | as `start()` |
| `__aexit__` | `async def __aexit__(self, exc_type, exc_val, exc_tb) -> None` | `None` | as `stop()` |

Notes:

- `register_plugin` and `load_plugin` require the core to be `RUNNING` — they raise `CoreError` on a non-running core. Start the core first (`await core.start()` or use `async with Core() as core:`).
- `load_plugins` also requires `RUNNING` (`CoreError` otherwise). It materializes every source, then commits all of them together under a single hold of the lifecycle lock — one atomic admission, not N separately-locked `load_plugin` calls. Commit order is a topological sort of the candidates' declared `provides`/`requires` (plus any already-live providers), so every candidate's `requires` is already satisfied by the time it is admitted.
- `load_plugins` is fresh-load-only: a candidate whose `metadata.name` collides with an already-live plugin is a plan-phase error — it does not hot-reload (use `load_plugin` for that).
- On failure `load_plugins` raises `BatchLoadError`. `phase` (`"plan"` | `"commit"`) discriminates a pre-commit fault (a cycle, a missing capability, a duplicate name, a materialize/compile failure, or — under `error_on_conflict` — a duplicate provider) from a candidate's own `on_start()` failing partway through the commit loop. `installed` is the committed prefix in commit order (always `()` on `phase="plan"`); `failed` names the offending candidate (`None` for graph-wide faults); `cause` is the underlying exception, chained via `from`. Rollback on a `BatchLoadError` is host policy, not kernel behavior — `installed` is the exact teardown handle: feed it to `unregister_plugin` in reverse to unwind, or keep it as-is to accept a partial boot.
- `unregister_plugin`'s `force` is **keyword-only** (`*, force: bool = False`).
- `get_capability`'s `tag` is **keyword-only**. When `capability` is a `type`, it is
  resolved to a name via `derive_capability_name` before lookup.
- **Protocol contract check (typed capabilities).** When a plugin declares a Protocol
  type in `provides=` (or a consumer resolves with `get_capability(SomeProtocol)`), the
  kernel validates the provider **structurally** — method-by-method, not via
  `isinstance` — at registration and again at typed resolution. For each public
  Protocol method the provider must (1) define a callable of the same name and
  (2) have a **signature-compatible** implementation: it accepts every parameter the
  protocol declares (by name or absorbed by `*args`/`**kwargs`), requires no parameter
  the protocol does not supply (extra *optional* params and `*args`/`**kwargs` are fine),
  and — when both sides annotate it — agrees on the return annotation. A method whose
  signature cannot be introspected falls back to presence-only. A missing or
  signature-incompatible method raises `PluginError`.
- **`check_plugin` is a side-effect-free admission probe** (RFC 0003 v2). It validates a
  candidate against the *live* plugin graph and returns an [`AdmissionResult`](#76-admissionresult)
  describing any structural faults (missing `requires`, id conflict, `provides` collisions,
  protocol-contract failures) **without committing** — no registration, no `start()`, no
  `plugin.registered` hook, no events. It is the advisory pre-flight for write→check→repair
  loops. It is *advisory because unlocked*: it takes no lifecycle lock, so a concurrent
  registration can change the graph before you act on the verdict. For the guarantee, call
  `register_plugin` — the **same** admission runs atomically under the lifecycle lock at
  commit (so a probe that passed can still be rejected at commit if the graph drifted). The
  two paths share one routine, so they cannot disagree. **Scope — a clean verdict means
  "fits the graph now," not "commit will succeed":** `AdmissionResult` models exactly four
  faults;   `register_plugin` can still reject at commit for a **name** conflict (distinct from
  the modeled *id* conflict), `max_plugins`, or a declared/circular dependency fault,
  none of which admission models. Separately, admission certifies the
  *declared* manifest, not that it is *complete* for what the body resolves at runtime — under
  RFC 0002 `resolves` is deliberately not registration-validated, so an under-declared
  `resolves` admits cleanly here and fails later as `CapabilityAccessError`. **Reachable
  ambiently** (RFC 0006): because the `AdmissionResult` is data and the probe discloses no
  more than `list`, `check_plugin` is mirrored onto the attenuated `CoreFacet` with no grant,
  so a plugin under `"declared"`/`"sealed"` calls `self.core.check_plugin(...)` directly (see §3.2).

### 2.3 Properties

| Property | Type | Notes |
|---|---|---|
| `state` | `CoreState` | Current core state |
| `events` | `EventBus` | The event bus (see [§5](#5-eventbus--coreevents)) |
| `hooks` | `HookSystem` | The hook system (see [§6](#6-hooksystem--corehooks)) |
| `tick` | `int` | Lock-free read; returns `0` before `start()` |
| `slip` | `int` | Current tick slip in periods. Lock-free read. Returns `0` before `start()`. |
| `config` | `CoreConfig` | Read-only configuration |
| `id` | `UUID` | Unique instance ID |

---

## 3. Plugin

`class Plugin(PluginProtocol)` — `src/uxok/plugin/_base.py:27`.
Exported from `uxok.plugin` and top-level `uxok`.

### 3.1 Constructor

Construction is **coreless**: the kernel attaches `self.core` *after* construction (at
register/reload), so it is available from `on_start` onward, not inside `__init__` (RFC 0001
§3.2.3). There is no `core` constructor argument; all parameters are keyword-only (`*,` at
`_base.py:69`).

```python
def __init__(
    self,
    *,
    name: str | None = None,
    version: str = "0.0.1",
    description: str = "",
    author: str = "",
    requires: set[str] | frozenset[str] | None = None,
    resolves: set[str] | frozenset[str] | None = None,
    provides: set[str] | frozenset[str] | None = None,
    dependencies: set[UUID] | frozenset[UUID] | None = None,
    hooks_consumed: set[str] | frozenset[str] | None = None,
    events_published: set[str] | frozenset[str] | None = None,
    tags: set[str] | frozenset[str] | None = None,
    config_schema: dict[str, Any] | None = None,
) -> None
```

Plugin identity is **kernel-owned**: the kernel assigns a unique `UUID` to every
plugin at construction and preserves it across hot-reload (zero-downtime swap).
Authors never set it — there is no `id` constructor parameter; passing `id=`
raises `TypeError` (unknown kwarg). Read identity via `PluginView.id` or
`Plugin.metadata.id`.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `name` | `str \| None` | `None` | Auto-detected from class name when `None` |
| `version` | `str` | `"0.0.1"` | |
| `description` | `str` | `""` | |
| `author` | `str` | `""` | |
| `requires` | `set[str] \| frozenset[str] \| None` | `None` | Hard load-order dependencies — every name must exist at registration. Runtime resolution gate too (union with `resolves`). May include Protocol types (normalized to string names) |
| `resolves` | `set[str] \| frozenset[str] \| None` | `None` | Capabilities authorized to resolve at runtime under `"declared"`/`"sealed"` (RFC 0002). **Not** validated at registration — a name may have no provider until one appears. May include Protocol types (normalized to string names) |
| `provides` | `set[str] \| frozenset[str] \| None` | `None` | May include Protocol types |
| `dependencies` | `set[UUID] \| frozenset[UUID] \| None` | `None` | Plugin UUIDs this plugin depends on |
| `hooks_consumed` | `set[str] \| frozenset[str] \| None` | `None` | |
| `events_published` | `set[str] \| frozenset[str] \| None` | `None` | |
| `tags` | `set[str] \| frozenset[str] \| None` | `None` | Used for tag-based capability provider selection |
| `config_schema` | `dict[str, Any] \| None` | `None` | Dict of `ConfigField` objects (see [§9](#9-configfield-and-required)) |

Raises: `TypeError` on unknown kwargs; `ValueError` on name validation failure.

**Name auto-detection:** when `name` is `None`, the framework converts the class name
to `snake_case` and strips common plugin-suffix words (e.g. `DataProcessor` → `data_processor`,
`UserAuthPlugin` → `user_auth`).

### 3.2 Methods

| Member | Signature | Returns | Raises |
|---|---|---|---|
| `emit` | `async def emit(self, event_name: str, data: Any = None, *, at_tick: int \| None = None) -> None` | `None` | `ValueError` if `at_tick <= core.tick` |
| `has_subscribers` | `def has_subscribers(self, event_name: str) -> bool` | `bool` | — |
| `subscribe` | `async def subscribe(self, event_pattern: str, handler: Callable) -> None` | `None` | — |
| `register_hook` | `async def register_hook(self, hook_name: str, handler: Callable, *, priority: int = 0) -> None` | `None` | — |
| `hook` | `hook(name: str, *args, at_tick: int \| None = None, firstresult: bool = False, **kwargs) -> Any` (instance attribute, not a `def`) | list of results, or first non-`None` when `firstresult=True`; `None` when `at_tick` is set (deferred, fire-and-forget) | `ValueError` if `at_tick <= core.tick` |
| `config` | `def config(self, key: str, default: Any = None) -> Any` | `Any` | — |
| `get_capability` | `async def get_capability(self, capability: str \| type, *, tag: str \| None = None) -> Any` | `Any` / typed `_T` (two `@overload`s) | `CapabilityError`; `CapabilityAccessError` under `capability_access="declared"`/`"sealed"` if not in the runtime grant `requires ∪ resolves` |
| `get_state` | `async def get_state(self) -> dict` | `dict` (default `{}`) | — |
| `restore_state` | `async def restore_state(self, state: dict) -> None` | `None` (default no-op) | — |
| `create_background_task` | `async def create_background_task(self, coro: Coroutine[Any, Any, Any], name: str \| None = None) -> asyncio.Task` | `asyncio.Task` | — |
| `on_start` | `async def on_start(self) -> None` | `None` (override hook; default no-op) | — |
| `on_stop` | `async def on_stop(self) -> None` | `None` (override hook; default no-op) | — |
| `start` | `async def start(self) -> None` | `None` | `PluginError` if started after shutdown (instances are one-shot) or on config-schema validation failure |
| `stop` | `async def stop(self) -> None` | `None` | — (isolates and logs errors) |

Notes:

- `emit` publishes the event name **verbatim** — no prefix is added. `Event.source` is
  stamped with the plugin's name so subscribers can identify the emitter without encoding
  it in the topic. `emit`'s `at_tick` is **keyword-only** and optional. Deferral is
  **tick-based only** (deterministic). There is no millisecond/wall-clock deferral:
  compute a target tick from `core.tick` if a time-based delay is needed. The immediate
  path (no `at_tick`) is short-circuited before `Event` allocation when there are no
  subscribers (demand gate); the `at_tick` deferred path always builds the `Event` eagerly
  and gates at fire time via `publish()`.
- `has_subscribers` is a **synchronous** demand guard for capability authors. It is
  mute-aware — returns `False` when the topic is suppressed. Use it to skip expensive
  payload construction: ``if self.has_subscribers(name): await self.emit(name, build())``.
  `mute`/`unmute` remain host-only on `core.events`; `has_subscribers` is surfaced here
  so plugin authors do not need to reach through `core.events` for the common guard case.
- `hook` is an instance attribute assigned a closure in `__init__`, not a class-level
  `def`. When `at_tick` is `None`, it executes immediately and returns results (list, or
  first non-`None` when `firstresult=True`). When `at_tick` is set, execution is deferred
  to that tick and `None` is returned (fire-and-forget); raises `ValueError` if
  `at_tick <= core.tick`.
- There is no recurring-execution primitive. Periodic work is built by self-rescheduling:
  a handler re-arms itself with `emit(at_tick=core.tick + N)` or
  `hook(name, at_tick=core.tick + N)`.
- `get_state` / `restore_state` are the hot-reload state-handoff contract. Both default
  to no-ops; subclasses override to participate in state continuity across reloads and
  supervised restarts.
- `subscribe` and `register_hook` are the dynamic registration primitives. The `@event`
  decorator desugars to `subscribe` and the `@hook` decorator desugars to `register_hook`
  — there is exactly one registration path. Both work in `on_start` or any later async
  context; `owner`/`plugin_id` bindings give the same instance-scoped auto-cleanup the
  decorator path gets.
- `get_capability` is gated on `capability_access` (RFC 0001 §3.2, refined by RFC 0002
  §3.2). Under `"open"` (default) any capability resolves and the raw provider is returned.
  Under `"declared"`/`"sealed"` a plugin may resolve only capabilities in its **runtime
  grant** — the union `requires ∪ resolves` — or, if it holds `kernel.dispatch`, any
  capability by name (`CapabilityAccessError` otherwise). `requires` carries a load-order
  constraint (must exist at registration); `resolves` does not — it authorizes runtime
  resolution of names that may be lazy, cyclic, or hot-loaded after the resolver registers.
  Under `"sealed"`, a **typed** resolution
  (`get_capability(SomeProtocol)`) additionally returns a protocol-limited *facet* that
  forwards only the protocol's public methods to the live provider — accessing any other
  member raises `AttributeError`, a hot-swap rebinds the facet transparently, and a call
  after the provider is revoked raises `StalePluginError`. An **untyped** string resolution
  has no protocol surface to attenuate to and returns the raw provider even under
  `"sealed"`. The facet is structurally the protocol (not a named public type).
- **Sealed return guard** (RFC 0004 §4 / spec 0005 §C). Under `"sealed"`, a typed
  resolution's facet **refuses** (raises `CapabilityAccessError`) when a provider method
  returns a live authority handle — a `Plugin`, or a kernel handle
  (`Core`/`CoreFacet`/`LifecycleFacet`). Returning such a handle hands the consumer a
  second-hop authority its manifest never declared (the `return self` / `return self.core` /
  `return get_plugin(...)` accident). Data, dataclasses, primitives, the ambient bus/hook
  systems, and already-attenuated views (`PluginView`/`PluginCollection`/`CapabilityFacet`)
  pass through unchanged. This is **robustness, not a boundary**: it is one hop only — a
  handle wrapped in a container (`[plugin]`), a bound method/closure over the live plugin, or
  one reached by reflection still escapes — so it closes the dominant *accidental* leak, not a
  determined one. `"open"`/`"declared"` build no facet and are unaffected.
- **The grant set is an *invocation* boundary, not reference isolation** (RFC 0004 / spec
  0005 §4). Under `"declared"`/`"sealed"`, `requires ∪ resolves` is the complete statement of
  what a plugin may **invoke by name** through its kernel handle — the reviewable surface for
  hallucinated authority. It is **not** an enforced reference-isolation boundary, for three
  reasons (none cheaply enforceable for in-process Python, all by design):
  1. **References cross edges.** A live handle can ride a return value, an argument, or an
     event/hook payload — channels that are deliberately ambient (RFC 0001 §2.3) — and arrive
     somewhere no manifest records. (Under `"sealed"` a *return value* that is itself a live
     plugin/kernel handle is refused — see the sealed return guard above — but an argument or
     a payload still crosses freely.)
  2. **Reflection reaches past the gate.** A plugin can read `self._Plugin__core_real` — the
     unattenuated `Core` it stores even under `"sealed"` — and invoke outside its grant set.
  3. **`kernel.lifecycle` is a declared full-authority escalation.** A holder obtains raw
     plugin instances via `get_plugin` and thus has ambient reach to every plugin's full
     surface. Enumerate `kernel.lifecycle` holders (greppable, one per manifest) and
     scrutinize exactly those.
- `kernel.lifecycle` is a **reserved, kernel-provided** capability (RFC 0001 §2d), not
  backed by a plugin. Declaring `requires={"kernel.lifecycle"}` is always satisfiable (no
  provider, no bootstrap ordering); resolving it returns a *lifecycle facet* forwarding
  exactly `register_plugin`, `unregister_plugin`, `load_plugin`, `get_plugin` to the
  kernel. This is how a plugin obtains graph control under `"declared"`/`"sealed"`, where
  those methods are no longer ambient on the `core` facet. The grant resolves identically
  under `"open"`, so plugins that use it are forward-compatible across all modes.
- `kernel.dispatch` is a **reserved, kernel-recognized** grant (RFC 0002 §3.4) — unlike
  `kernel.lifecycle` it backs **no facet** and is never itself resolved. A plugin that
  declares `resolves={"kernel.dispatch"}` may resolve **any** capability by name under
  `"declared"`/`"sealed"` (the gate authorizes it; resolution still queries the live
  capability system and raises the normal `CapabilityError` if no provider exists). Like
  `kernel.lifecycle` it is registration-exempt. It is deliberately coarse: a single,
  greppable, auditable declaration that "this plugin is a dispatcher / control plane" (e.g.
  an HTTP `/call` surface resolving capabilities named by an incoming request), replacing
  ad-hoc reaches around the facet to the raw core.
- `list` is **ambient** on the `core` facet under every mode (RFC 0001 §3.2.2): enumerating
  "what exists" is benign and needs no grant. It returns descriptive-only `PluginView`s —
  the view's invocation members (`call`/`get_object`) were removed kernel-wide (§10.1), so
  discovery is not a backdoor to invoking other plugins. To *act on* a discovered plugin,
  resolve it via `kernel.lifecycle` or a typed capability.
- `check_plugin` is **ambient** on the `core` facet under every mode (RFC 0006): it is the
  read-only sibling of `list` — a pure read of graph state whose `AdmissionResult` is data,
  not handles, so it discloses no more than `list` already does and needs no grant. A plugin
  under `"declared"`/`"sealed"` reaches the admission probe as `self.core.check_plugin(...)`,
  not only through the `_Plugin__core_real` reflection escape. It is *not* on `LifecycleFacet`:
  a probe-only consumer (a gate) must not take graph-mutation authority to ask a question.

### 3.3 Properties

| Property | Type |
|---|---|
| `metadata` | `PluginMetadata` (immutable) |
| `core` | `Core` — under `capability_access="declared"`/`"sealed"`, an attenuated facet exposing only the plugin-safe kernel surface (`tick`, `slip`, `state`, `config`, `events`, `hooks`, ambient `list`/`check_plugin`, gated `get_capability`), not the real `Core` (RFC 0001 §3.2.1, RFC 0006) |

The canonical plugin-author idiom for resolving a capability is
`self.get_capability(...)` (§3.2), the convenience sibling of `self.emit`/`self.hook`/
`self.config`. `self.core.get_capability(...)` is the same call on the lower-level facet
and is retained for the security model (it is the gated facet route exercised by the
secure-capability suite, RFC 0001 §3.2.1) — an **equivalent gate, not a second recommended
path**. Prefer `self.get_capability(...)` in plugin code.

---

## 4. Decorators

Defined in `uxok.plugin` (`hook`, `event`, `handle_errors`). `hook` and `event`
are re-exported at top-level `uxok`, which is the **canonical author path**
(`from uxok import event, hook`); the `uxok.plugin` path is the definition
home and remains valid. These are the same objects reached two ways, not two
implementations. `handle_errors` is advanced and stays `uxok.plugin`-only.

### 4.1 `hook`

```python
def hook(hook_name: str, priority: int = 0) -> Callable[[Callable], Callable]
```

Registers a method as a hook handler. Hook names are global — no auto-prefixing.
There is no `every_ticks` parameter; recurring execution uses self-rescheduling via
`Plugin.hook(name, at_tick=core.tick + N)`.

| Parameter   | Type  | Default    |
| ----------- | ----- | ---------- |
| `hook_name` | `str` | (required) |
| `priority`  | `int` | `0`        |

### 4.2 `event`

```python
def event(event_pattern: str) -> Callable[[Callable], Callable]
```

Subscribes a method to event patterns. Glob patterns are supported (e.g. `"user.*"`).
There is no `typed` parameter.

| Parameter | Type | Default |
|---|---|---|
| `event_pattern` | `str` | (required) |

### 4.3 `handle_errors`

```python
def handle_errors(
    emit_event: bool = True,
    return_on_error: Any = None,
    log_level: str = "ERROR",
) -> Callable[[Callable], Callable]
```

Wraps a method with automatic error catching, logging, and optional event emission.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `emit_event` | `bool` | `True` | Whether to emit an error event on exception |
| `return_on_error` | `Any` | `None` | Value returned when an exception is caught |
| `log_level` | `str` | `"ERROR"` | Accepts `"ERROR"`, `"WARNING"`, `"INFO"`; other values silence logging |

**Emitted event name — critical distinction:**

- When wrapping a real `Plugin` instance (object has `_emit_plugin_error`): emits
  `"core.plugin_error"` with `source="handled_method"`.
- When wrapping a duck-typed object (has `emit` but no `_emit_plugin_error`): emits
  `"plugin.error"` as a legacy fallback. Sync wrappers cannot await the emit and
  only log.

See [§12](#12-framework-event-contracts) for the full `core.plugin_error` payload specification.

---

## 5. EventBus — `core.events`

`core.events` is typed as the `EventBus` protocol (`uxok.protocols.events.EventBus`;
**not** part of `uxok.protocols.__all__` — reached via `core.events`, never named
by authors). The concrete class `_EventBus` is internal. Access the event bus exclusively
via the `core.events` property or the `@event` decorator.

Dispatch is **concurrent fire-and-forget**: `publish()` dispatches each subscriber as an
independent tracked task and returns immediately without awaiting any of them. Events are
**stamped** with the publish-time tick counter, but dispatch is **not serialized** — multiple
subscribers run concurrently. Ordering is **causal**: a handler's own nested `emit`/`hook`
calls complete before it continues, but there is no global ordering guarantee across
independent publishers.

| Method | Signature | Returns |
|---|---|---|
| `publish` | `async def publish(self, event: Event) -> bool` | `bool` |
| `subscribe` | `async def subscribe(self, event_name, callback: Callable[[Event], None], plugin_id: UUID \| None = None, owner: object \| None = None) -> str` | subscription id (`str`) |
| `unsubscribe` | `async def unsubscribe(self, subscription_id: str) -> None` | `None` |
| `unsubscribe_plugin` | `async def unsubscribe_plugin(self, plugin_id: UUID) -> None` | `None` |
| `unsubscribe_owner` | `async def unsubscribe_owner(self, owner: object) -> None` | `None` |
| `has_subscribers` | `def has_subscribers(self, name: str) -> bool` | `bool` |
| `mute` | `def mute(self, pattern: str) -> None` | `None` |
| `unmute` | `def unmute(self, pattern: str) -> None` | `None` |

All eight methods are in both the `_EventBus` implementation and the `EventBus` protocol.
`subscribe`'s `plugin_id` and `owner` are positional-or-keyword with defaults of `None`.
`has_subscribers`, `mute`, and `unmute` are plain synchronous `def` (no `async`) — they
are pure in-memory operations with no I/O.

**Who this is for.** Plugin authors do not call these directly — `@event` covers
subscription and `Plugin.emit()` covers publishing (verbatim name, `source` stamped).
`core.events` is public for the **host application** (the code that owns the `Core`
and runs lifecycle/security outside the plugin graph) and because it is the `EventBus`
protocol contract the kernel implements. The three `unsubscribe*` variants are retained
because they serve distinct lifecycles: by subscription id, by plugin, and by owner
(hot-reload). Events published directly via `core.events.publish()` do not have
`Event.source` set; that field is stamped only by `Plugin.emit()`.

**Demand-driven emission.** `has_subscribers(name)` lets the host or a mechanism layer
check whether an event would be delivered before spending work to generate it. It is
mute-aware: if the topic is suppressed, `has_subscribers` returns `False`, so a
demand-driven emitter naturally skips it without a separate mute check.
`mute(pattern)` / `unmute(pattern)` suppress matching events at the source — muted
events are dropped in `publish()` before subscriber lookup, so no dispatch tasks are
created and no subscribers are notified. Pattern syntax is `fnmatch` (same as
subscription patterns). `has_subscribers` is also surfaced on `Plugin` (sync,
mute-aware) so capability authors can guard payload construction without reaching
through `core.events`; `mute`/`unmute` remain host-only on `core.events`.
`emit()` / `publish()` now short-circuit before `Event` allocation when the topic has
no subscribers or is muted — an unsubscribed emit costs only two hash-table lookups.
These three are host/mechanism primitives; plugin authors continue to use `@event`
and `Plugin.emit()`.

---

## 6. HookSystem — `core.hooks`

`core.hooks` is typed as the `HookSystem` protocol (`uxok.protocols.hooks.HookSystem`;
**not** part of `uxok.protocols.__all__` — reached via `core.hooks`, never named by
authors). The concrete class `_HookSystem` is internal. The primary public path for plugin
authors is the `@hook` decorator and `Plugin.hook()` method; direct use of `core.hooks` is advanced.

| Method | Signature | Returns | Tier |
|---|---|---|---|
| `execute` | `async def execute(self, name: str, *args, firstresult: bool = False, plugin_id: str = "", **kwargs) -> list[object] \| object \| None` | list, single value, or `None` | **Public** (`Plugin.hook()` is the preferred path) |
| `register` | `async def register(self, name: str, callback: Callable, *, priority: int = 0, plugin_id: str = "", owner: object \| None = None) -> None` | `None` | **Public.** Primitives-based registration; builds the `Hook` value object internally. `Plugin.register_hook()` is the preferred author path; `Plugin.start()` routes the `@hook` decorator through here. `owner` enables instance-scoped hot-reload cleanup for any callable (closures included). |
| `precache_hooks` | `async def precache_hooks(self, hook_names: list[str] \| None = None) -> None` | `None` | **Advanced.** Hook warming; the kernel calls it internally (`Core._precache_hooks`, private) based on `CoreConfig.hook_precaching`. |
| `unregister` | `async def unregister(self, name: str, hook: Hook, priority: int \| None = None) -> bool` | `bool` | **Advanced.** In the `HookSystem` protocol and non-underscore, but hook lifecycle is normally managed by the framework. |
| `unregister_plugin_hooks` | `async def unregister_plugin_hooks(self, plugin_id: str) -> None` | `None` | **Internal lifecycle — not for plugin authors.** Drains all hooks for a plugin ID at unregistration. In the `HookSystem` protocol to allow the kernel to call it, but plugin authors do not call this directly. |
| `unregister_owner_hooks` | `async def unregister_owner_hooks(self, owner: object) -> None` | `None` | **Internal lifecycle — not for plugin authors.** Drains instance-scoped hooks at hot reload. In the `HookSystem` protocol for the same reason. |

`get_hooks` and `clear_cache` exist on the `_HookSystem` implementation but are NOT in
the `HookSystem` protocol and are therefore NOT part of the constitutional surface. They
are excluded from this document.

---

## 7. Data structures

### 7.1 Event

`@dataclass(frozen=True, slots=True)` — `src/uxok/protocols/events.py:12`.

Custom `__init__` signature (the dataclass `__init__` is replaced):

```python
Event(name, data, timestamp=None, tick=0, slip=0, source=None)
```

When `timestamp` is `None`, the custom `__init__` sets `time.time()`.

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | `str` | (required) | |
| `data` | `Any` | (required) | |
| `timestamp` | `float` | `0.0` in dataclass; auto-set to `time.time()` when constructed with `None` | |
| `tick` | `int` | `0` | Stamped by core at publish when the tick system is running |
| `slip` | `int` | `0` | Tick-boundary drift at stamp time |
| `source` | `str \| None` | `None` | Emitting plugin's name; stamped by `Plugin.emit()`. Metadata only — not part of the topic name. `None` when published directly via `core.events.publish()`. |

### 7.2 Hook

`@dataclass(frozen=True, slots=True)` — `src/uxok/protocols/hooks.py:24`.

The dataclass has seven declared fields, but the **custom constructor signature differs**:

```python
Hook(name, callback, priority=0, plugin_id="", owner=None)
```

The constructor argument is `callback` (stored as the field `func`). `is_async` and
`accepts_tick_context` are derived at construction time, not passed by the caller.

| Field (declared) | Type | Default |
|---|---|---|
| `name` | `str` | (required) |
| `func` | `Callable[..., Any]` | (required; passed as `callback` in constructor) |
| `priority` | `int` | `0` |
| `plugin_id` | `str` | `""` |
| `owner` | `object \| None` | `None` — registering instance, for instance-scoped hot-reload cleanup |
| `is_async` | `bool` | Derived — not a constructor argument |
| `accepts_tick_context` | `bool` | Derived — not a constructor argument |

### 7.3 CoreConfig

`@dataclass` — `src/uxok/protocols/config.py:9`. Constructed internally by `Core(**kwargs)`;
rarely constructed directly. `__post_init__` delegates to `validate_core_config` which raises
`ValueError` (not a custom exception) on invalid field values.

Full field list with accepted enum values and numeric bounds:

| Field | Type | Default | Accepted values / bounds |
|---|---|---|---|
| `max_plugins` | `int` | `100` | Positive integer |
| `hook_precaching` | `str` | `"on_core_start"` | `"disabled"`, `"on_core_start"` |
| `capability_collision` | `str` | `"last_wins_with_warning"` | `"error_on_conflict"`, `"first_wins"`, `"last_wins_with_warning"` |
| `capability_selection` | `str` | `"last_registered"` | `"first_registered"`, `"last_registered"` |
| `capability_missing` | `str` | `"raise"` | `"raise"`, `"return_none"` |
| `capability_access` | `str` | `"open"` | `"open"`, `"declared"`, `"sealed"` |
| `tick_rate` | `int` | `1000` | Positive integer, ≤ 10000 |
| `tick_slip_threshold` | `int` | `5` | Positive integer |
| `tick_precision` | `str` | `"sleep"` | `"sleep"`, `"hybrid"` |
| `tick_busy_wait_us` | `int` | `200` | Positive integer, ≤ 1000000 |
| `tick_catchup` | `str` | `"skip"` | `"skip"`, `"burst"` |
| `plugin_configs` | `dict[str, dict[str, Any]]` | `{}` (default_factory) | Dict of plugin name → config dict |

### 7.4 CoreState

`Enum` — `src/uxok/protocols/_types.py:13`. Exactly five members. There is no
`ERROR` member.

| Member | Value |
|---|---|
| `INITIALIZED` | `"initialized"` |
| `RUNNING` | `"running"` |
| `STOPPING` | `"stopping"` |
| `STOPPED` | `"stopped"` |
| `FAILED` | `"failed"` |

### 7.5 PluginMetadata

`@dataclass(frozen=True)` — `src/uxok/protocols/plugin.py:11`.
Returned by `Plugin.metadata`. Exported from `uxok.protocols`.

`__post_init__` raises `ValueError` if `name` or `version` is empty.

| Field | Type | Default |
|---|---|---|
| `id` | `UUID` | (required) |
| `name` | `str` | (required) |
| `version` | `str` | (required) |
| `description` | `str` | `""` |
| `author` | `str` | `""` |
| `dependencies` | `frozenset[UUID]` | `frozenset()` |
| `requires` | `frozenset[str]` | `frozenset()` |
| `resolves` | `frozenset[str]` | `frozenset()` |
| `provides` | `frozenset[str]` | `frozenset()` |
| `hooks_consumed` | `frozenset[str]` | `frozenset()` |
| `events_published` | `frozenset[str]` | `frozenset()` |
| `tags` | `frozenset[str]` | `frozenset()` |

### 7.6 AdmissionResult

`@dataclass(frozen=True)` — `src/uxok/protocols/core.py`.
Returned by `Core.check_plugin` (advisory) and the verdict `register_plugin` rejects on at
commit. Importable from `uxok.protocols` or `uxok.core`. Like `PluginMetadata` it is
**not** a top-level `uxok` export — the kernel hands it to the caller, who reads it by
attribute rather than constructing it (the §1 curation rule).

The faults a candidate would raise against the *live* graph, computed without committing.
`ok` is a derived property — it can never disagree with the fault fields. An empty
`AdmissionResult()` is a clean pass.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `missing_requires` | `frozenset[str]` | `frozenset()` | load-order `requires` with no live provider (reserved grants exempt) |
| `id_conflict` | `bool` | `False` | the candidate's id is already registered |
| `provides_conflicts` | `frozenset[str]` | `frozenset()` | provided capabilities colliding with the live graph (only under `capability_collision="error_on_conflict"`) |
| `contract_failures` | `frozenset[str]` | `frozenset()` | typed capabilities whose provider violates its protocol contract |
| `ok` | `bool` (read-only property) | — | `True` iff every fault field is empty/false |

---

## 8. Exceptions

Defined in `src/uxok/errors.py`. All seven are **re-exported at top-level
`uxok`** and also importable from `uxok.errors`. They are public not because
plugins raise them — plugins almost never do — but because the framework raises them
*into* caller code, so authors and the host must be able to name them in `except`
clauses. An exception belongs to this surface iff it crosses the API boundary in either
direction (raised to the caller, or caught by the caller).

```python
from uxok import (  # canonical
    BatchLoadError,
    CapabilityAccessError,
    CapabilityError,
    CoreError,
    MissingCapabilityError,
    PluginError,
    StalePluginError,
)
# also valid (definition home):
from uxok.errors import (
    BatchLoadError,
    CapabilityAccessError,
    CapabilityError,
    CoreError,
    MissingCapabilityError,
    PluginError,
    StalePluginError,
)
```

Inheritance:

```
Exception
└── CoreError
    ├── PluginError
    │   ├── StalePluginError
    │   └── BatchLoadError       (a load_plugins() batch failed)
    └── CapabilityError
        ├── MissingCapabilityError   (required capability absent at registration)
        └── CapabilityAccessError    (capability exists, but outside the caller's runtime grant)
```

There is no `HookError` or `EventError`: a per-subsystem exception with neither a raise
site nor a catch site anywhere in the codebase is not API. Hook failures isolate to the
`core.hook_error` event (see [§12](#12-framework-event-contracts)), not an exception.

Constructors:

- `CoreError`, `PluginError`, `StalePluginError`: no custom `__init__` — standard `Exception(*args)`.
- `StalePluginError`: raised by `PluginView.uptime()` and `PluginView.methods()` when the
  plugin has been unregistered or torn down between the time the view was fetched and the
  time the read was attempted. EAFP callers must catch it. Inherits from `PluginError`.
- `CapabilityError.__init__(self, capability: str | list[str] | None, available: list[str] | None = None, message: str | None = None)` — when `message` is given, it is used verbatim; otherwise the message is built from `capability` and (optionally) `available` as suggestions.
- `MissingCapabilityError.__init__(self, missing: list[str], phase: str = "register", available: list[str] | None = None, requirer: str | None = None)` — sets `self.missing: list[str]`, `self.phase: str`, and `self.requirer: str | None` (the name of the plugin whose `requires` failed, when known); delegates to `CapabilityError`.
- `CapabilityAccessError.__init__(self, capability: str, plugin_name: str, declared: list[str] | None = None)` — sets `self.capability: str` and `self.plugin_name: str`; delegates to `CapabilityError`. The `declared` argument carries the caller's full runtime grant (`requires ∪ resolves`) for the message. Raised by `Plugin.get_capability` / the `CoreFacet` when, under `capability_access="declared"`/`"sealed"`, a plugin resolves a capability outside its runtime grant and without the `kernel.dispatch` grant (RFC 0001 §3.2, RFC 0002 §3.2).
- `BatchLoadError.__init__(self, *, phase: str, cause: BaseException, installed: tuple[str, ...] = (), failed: str | None = None)` — keyword-only; inherits from `PluginError`. Raised by `Core.load_plugins` (RFC 0008). Sets four attributes: `phase` — `"plan"` (a pre-commit fault: cycle, missing capability, duplicate name, materialize/compile failure, or duplicate provider under `error_on_conflict`; `installed` is always `()`) or `"commit"` (a candidate's own `on_start()` raised partway through the batch); `cause` — the underlying exception, also chained via `from`; `installed` — the plugins committed before the failure, in commit order, the exact handle for host-side rollback; `failed` — the offending candidate's origin/name, or `None` for graph-wide faults such as a cycle.

---

## 9. ConfigField and REQUIRED

Defined in `uxok.plugin` and re-exported at top-level `uxok` (canonical
author path).

```python
from uxok import ConfigField, REQUIRED        # canonical
from uxok.plugin import ConfigField, REQUIRED  # also valid (definition home)
```

**`REQUIRED`** is a sentinel singleton (`_RequiredType()`); `repr() == "REQUIRED"`.
Used as the default value for `ConfigField` when the caller must supply a value.

**`ConfigField`** — `@dataclass` — `src/uxok/plugin/config_field.py:19`.

```python
ConfigField(type: type, default: Any = REQUIRED, description: str = "")
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `type` | `type` | (required) | Expected Python type; used for validation at `Plugin.start()` |
| `default` | `Any` | `REQUIRED` | Supply `REQUIRED` to make the field mandatory |
| `description` | `str` | `""` | Human-readable description used in error messages |

Valid construction forms: `ConfigField(int)`, `ConfigField(int, 10)`,
`ConfigField(int, 10, "description")`, `ConfigField(str, REQUIRED, "description")`.
The form `ConfigField(int, default=10, "…")` is invalid Python (positional after keyword).

---

## 10. PluginCollection and PluginView

**Importable from `uxok.registry` and return types of `core.list()`.**
`uxok.registry.__all__ == ["CapabilityInfo", "PluginCollection", "PluginView"]`. All
three names are directly importable for type annotations.

```python
from uxok.registry import CapabilityInfo, PluginCollection, PluginView
```

`_FilterProxy` is internal-named and is not exported.

### 10.1 PluginView

`@dataclass` — `src/uxok/registry/_plugin_view.py`.
A descriptive snapshot — a description, not a handle. It exposes no way to invoke a method
on, or hand back, the live instance (RFC 0001 §3.2.2: discovery must not be a backdoor to
invocation). To act on a plugin, resolve it through the `kernel.lifecycle` grant
(`get_plugin`) or a typed capability. Descriptive fields are fresh-at-fetch (rebuilt when
the registry changes); the benign live reads resolve the current instance but return only
data.

**Descriptive fields** (frozen at collection-build time):

| Field | Type |
|---|---|
| `id` | `str` |
| `name` | `str` |
| `provides` | `set[str]` |
| `requires` | `set[str]` |
| `tags` | `set[str]` |
| `used_by` | `list[str]` |
| `hooks_provided` | `list[str]` |
| `hooks_consumed` | `list[str]` |
| `events_published` | `list[str]` |
| `events_subscribed` | `list[str]` |
| `load_order` | `int` |

**Benign live reads** (resolve the current instance on access but return only data — never
the instance, never arbitrary invocation):

| Member | Signature | Returns | Raises |
|---|---|---|---|
| `status` (property) | `status -> Literal["created", "active", "stopped"]` | Live lifecycle status; `"stopped"` when plugin is gone | — |
| `ready` (property) | `ready -> bool` | `True` when `status == "active"` | — |
| `uptime` | `async def uptime(self) -> float` | Seconds since plugin start | `StalePluginError` if plugin is gone |
| `methods` | `async def methods(self) -> list[dict]` | Own public methods (excluding `Plugin` base methods); each dict has `name, signature, parameters, return_annotation, doc` | `StalePluginError` if plugin is gone |
| `invalidate_cache` | `def invalidate_cache(self) -> None` | `None` | — |

There is no `__getattr__` delegation on `PluginView`, and no `call`/`get_object` — by
design (RFC 0001 §3.2.2). The view is a description; invocation lives behind the
`kernel.lifecycle` grant or a typed capability, not behind discovery.

**Description vs live-read source-of-truth:** `status`/`ready` are sync reads off the
cached instance (so `PluginCollection.active` can filter without awaiting), while the
live reads (`uptime`/`methods`) are registry-authoritative — they resolve against the
live registry and raise `StalePluginError` when the plugin is no longer registered. In
every standard teardown the instance's shutdown flag is set before it leaves the
registry, so `status` reports `"stopped"` consistently with the live reads raising; treat
the registry (i.e. the live read) as authoritative if the two ever disagree.

### 10.2 PluginCollection

`src/uxok/registry/_plugin_view.py`.

Constructor: `PluginCollection(plugins: list[PluginView], *, build_indexes: bool = True, capability_info: dict[str, CapabilityInfo] | None = None)`.
The keyword arguments are internal construction details; a collection is normally
obtained from `core.list()`, not constructed directly.

Public members:

| Member | Kind | Signature → returns |
|---|---|---|
| `active` | property | `-> PluginCollection` (live-status active only) |
| `capability` | property | `-> _FilterProxy` (filter by capability) |
| `hook` | property | `-> _FilterProxy` (filter by hook) |
| `event` | property | `-> _FilterProxy` (filter by event) |
| `names` | property | `-> list[str]` |
| `capabilities` | property | `-> list[str]` (sorted, de-duplicated names of every capability provided across the collection — the single discovery surface for "what capabilities exist") |
| `count` | property | `-> int` |
| `uptime_over` | async method | `async (seconds: float) -> PluginCollection` (async because `uptime` is now live; stale plugins are excluded) |
| `by_name` | method | `(name: str) -> PluginView \| None` |
| `by_id` | method | `(plugin_id: str \| UUID) -> PluginView \| None` |
| `first` | method | `() -> PluginView \| None` |
| `__iter__` | dunder | `-> Iterator[PluginView]` |
| `__len__` | dunder | `-> int` |
| `__getitem__` | dunder | `(index: int) -> PluginView` |

**`_FilterProxy`** is internal-named but is the object returned by `collection.capability`,
`collection.hook`, and `collection.event`. Its public methods:

| Method | Signature → returns |
|---|---|
| `provides` | `(name: str) -> PluginCollection` |
| `consumes` | `(name: str) -> PluginCollection` |
| `info` | `(name: str) -> CapabilityInfo \| None` (capability filter only; `None` for hook/event filters or unknown capability names) |

Example DSL: `plugins.capability.provides("storage")`, `plugins.hook.consumes("data.validate")`,
`plugins.capability.info("storage")`.

### 10.3 CapabilityInfo

`@dataclass(frozen=True)` — `src/uxok/registry/_plugin_view.py`.
Typed result for capability-protocol introspection. Returned by
`collection.capability.info(name)`.

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Capability name |
| `providers` | `list[dict]` | Provider descriptors: `name, id, version, description, tags` |
| `selected_provider` | `str` | Name of the currently selected provider |
| `provider_count` | `int` | Total number of registered providers |
| `typed` | `bool` | `True` when a Protocol type was associated at registration |
| `protocol_name` | `str` | Protocol class name; `""` when `typed` is `False` |
| `protocol_methods` | `list[dict]` | `get_protocol_methods` output; `[]` when `typed` is `False` |

Each `protocol_methods` entry has the same shape as `PluginView.methods()` dicts:
`name, signature, parameters, return_annotation, doc`.

---

## 11. Subpackage public exports

These are the only importable public names per module. A compliance test parses this
section to assert importability. Import blocks contain ONLY names that appear in the
corresponding `__all__` and were empirically confirmed importable.

### `uxok` — top-level

```python
from uxok import (
    Core, Plugin, event, hook,
    ConfigField, REQUIRED,
    CoreError, PluginError, CapabilityError,
    MissingCapabilityError, BatchLoadError, CapabilityAccessError, StalePluginError,
)

__all__ = [
    "REQUIRED",
    "BatchLoadError",
    "CapabilityAccessError",
    "CapabilityError",
    "ConfigField",
    "Core",
    "CoreError",
    "MissingCapabilityError",
    "Plugin",
    "PluginError",
    "StalePluginError",
    "event",
    "hook",
]
```

### `uxok.protocols`

```python
from uxok.protocols import (
    AdmissionResult,
    Core,
    CoreConfig,
    CoreState,
    Event,
    Hook,
    PluginMetadata,
    PluginProtocol,
)
```

Note: `protocols.Core` is the `Core` **Protocol** (`protocols/core.py`), distinct from
the concrete `uxok.Core` class.

**Not in `uxok.protocols.__all__`** (deliberately, see [§1](#1-top-level-public-exports)):

- `EventBus` / `HookSystem` — the protocols typing `core.events` / `core.hooks`. Reached
  through those properties, never named by authors. Importable from their definition
  modules (`uxok.protocols.events`, `uxok.protocols.hooks`) for internal use.
- `EventName` / `HookName` (`= str`) and `PluginId` (`= UUID`) — type aliases with no
  contract value. Signatures in this document use the concrete `str` / `UUID` instead.

### `uxok.plugin`

```python
from uxok.plugin import (
    REQUIRED,
    ConfigField,
    Plugin,
    event,
    handle_errors,
    hook,
)
```

### `uxok.errors`

```python
from uxok.errors import (
    BatchLoadError,
    CapabilityAccessError,
    CapabilityError,
    CoreError,
    MissingCapabilityError,
    PluginError,
    StalePluginError,
)
```

All seven are also re-exported at top-level `uxok` (the canonical author path).
`uxok.errors` remains importable as their definition home.

### `uxok.registry`

```python
from uxok.registry import CapabilityInfo, PluginCollection, PluginView
```

`uxok.registry.__all__ == ["CapabilityInfo", "PluginCollection", "PluginView"]`. These are the
return types of `core.list()` and `collection.capability.info()`, importable for type annotations.
`_FilterProxy` is internal and not exported.

### Packages exposing no public surface

The following packages expose no public names for import:

- `uxok.events` — no public `__all__`; the `EventBus` contract is defined here
  (`uxok.protocols.events.EventBus`) but is not in `uxok.protocols.__all__`.
- `uxok.hooks` — no public `__all__`; the `HookSystem` contract is defined in
  `uxok.protocols.hooks` but is not in `uxok.protocols.__all__`.
- `uxok.timing` — `__all__ == []`; timing machinery (`TickClock`, `TickScheduler`)
  is entirely internal. Author-facing timing is on `Core` and `Plugin`: `core.tick`,
  `core.slip`, `emit(at_tick=)`, `hook(at_tick=)`. Deferral is tick-based only — there
  is no millisecond/wall-clock variant.
- `uxok.utils` — has an `__all__` containing kernel-internal helpers (`AsyncTaskManager`,
  `validate_identifier`, `_AsyncSafeSet`, etc.); these are NOT constitutional public API and
  are excluded from this document entirely.

---

## 12. Framework event contracts

Events the kernel publishes. Any plugin or host application may subscribe. Payload keys
marked as "stable" are guaranteed across all emit sites for that event name.

### `core.plugin_error`

Emitted when a plugin's event handler, background task, lifecycle step, or
`@handle_errors`-wrapped method fails.

Stable payload keys (present at every emit site):

| Key | Type | Notes |
|---|---|---|
| `plugin_id` | `str` | Plugin UUID |
| `plugin_name` | `str` | Plugin name (present at most sites; absent at the raw event-handler path which omits it) |
| `source` | `str` | Origin of the failure: `"lifecycle"`, `"event_handler"`, `"handled_method"`, or `"background_task"` |
| `error` | `str` | String representation of the exception |
| `error_type` | `str` | Exception class name |

Source-dependent extra keys:

| `source` value | Extra keys |
|---|---|
| `"lifecycle"` | `phase` (e.g. `"register"`, `"start"`, `"on_stop"`) |
| `"event_handler"` | `event_name` |
| `"handled_method"` | `method` |
| `"background_task"` | `task_name` |

Failures inside a `core.plugin_error` handler are logged but not re-reported (no error
loops). A supervisor plugin is the natural consumer: subscribe, count failures, and apply
a restart policy — all in plugin code, since the kernel only emits the signal.

### `core.hook_error`

Emitted when a hook handler raises. Execution isolates the failure to `None` and
continues with remaining handlers.

| Key | Type |
|---|---|
| `hook_name` | `str` |
| `plugin_id` | `str` |
| `error` | `str` |
| `error_type` | `str` |

### `core.tick_slip`

Emitted when a tick boundary slips by at least `tick_slip_threshold` ticks.

| Key | Type |
|---|---|
| `tick` | `int` |
| `slip` | `int` |
| `tick_rate` | `int` |

### `core.plugin_reloaded`

Emitted when a hot-reload swap completes successfully.

| Key | Type |
|---|---|
| `plugin_name` | `str` |
| `old_id` | `str` |
| `new_id` | `str` |

### `core.tick_clock_failed`

Emitted when the tick loop crashes unexpectedly. In-flight dispatch tasks are
cancelled on shutdown. Supervisors should treat this as fatal.

| Key | Type |
|---|---|
| `tick` | `int` |
| `tick_rate` | `int` |

### `core.capability.rebound`

Emitted when a hot-reload swap installs a new provider instance for a capability that is
still provided (RFC 0001 §3.4). One event per rebound capability. Because the swap is an
in-place reload, both instances share the plugin ID, so `old_provider_id == new_provider_id`
— the event signals "the provider instance was replaced," not "a different plugin took over."
Capabilities the new version *adds* are fresh registrations and do not emit this event.

| Key | Type | Notes |
|---|---|---|
| `capability` | `str` | Capability name whose provider was replaced |
| `old_provider_id` | `str` | Replaced provider's plugin UUID |
| `new_provider_id` | `str` | Installed provider's plugin UUID (equal to `old_provider_id` on reload) |

### `core.capability.revoked`

Emitted when the **last** provider of a capability is unregistered, leaving the capability
unresolvable (RFC 0001 §3.4). One event per fully-revoked capability. Not emitted when another
provider remains, nor on the failed-registration rollback path (a plugin that never fully
registered does not announce revocation).

| Key | Type | Notes |
|---|---|---|
| `capability` | `str` | Capability name that is now fully revoked |
| `old_provider_id` | `str` | Departed provider's plugin UUID |

### Hooks named like events — not events

`"plugin.registered"` and `"plugin.unregistered"` are **hooks**, not events. They are
executed via `core.hooks.execute("plugin.registered", plugin)` and
`core.hooks.execute("plugin.unregistered", real_id)`. They do not appear in the event
bus and are not subscribable with `@event`.

### `plugin.error` — legacy duck-typed fallback

`"plugin.error"` is NOT a kernel framework event for real `Plugin` instances. It is
emitted only by `@handle_errors` when wrapping a non-`Plugin` object that has an `emit`
method but lacks `_emit_plugin_error`. Payload: `plugin`, `method`, `error`,
`error_type`, `timestamp`. This is distinct from `core.plugin_error`.

---

## 13. State machine

`Core` follows a strict constitutional state graph. Plugin-level failures are signals
(`core.plugin_error`, `core.hook_error`), not core states; supervision policy lives
in plugins.

```
Normal flow:     INITIALIZED → RUNNING → STOPPING → STOPPED
Teardown fault:  STOPPING → FAILED  (teardown itself failed)
Restart flow:    STOPPED/FAILED → INITIALIZED → RUNNING  (fresh plugin graph)
```

`STOPPING` is the drain phase. `core.stop()` is a full teardown: it unregisters every
plugin, leaving an empty but reusable core. Plugin instances are one-shot; state
continuity across restarts is explicit via `get_state()` / `restore_state()`.

Every state transition fires the `core.state.changed` hook with `(old_state, new_state)`
as arguments.

---

## 14. Hot-reload swap sequence

`core.load_plugin(code)` replaces an existing plugin if a class with the same name is
already registered. The swap sequence is:

1. Compile and validate the new code (fails fast on syntax error or wrong class count).
2. Validate capabilities — the new version's `requires` are checked; `MissingCapabilityError`
   fails the reload before any state changes.
3. Call `get_state()` on the old instance to serialise durable state.
4. Start the new instance; registry and capability providers swap; dependency edges are
   reconciled to the new version's `requires`.
5. Call `restore_state(state)` on the new instance with the state from step 3.
6. Call `on_stop()` on the old instance (failures here are signalled via `core.plugin_error`
   and do not roll back).
7. Drain the old instance.

Failures at any step through step 5 roll back to the old version. A failing `on_stop`
in step 6 is signalled but does not roll back.

---

## 15. Removed API

| Old name | Status | Replacement |
|---|---|---|
| `PluginProxy` | **Renamed** | `PluginView` — `from uxok.registry import PluginView` |
| `on` | **Removed** | `event` — `from uxok import event` |
| `@on(...)` | **Removed** | `@event(...)` — identical behavior, new name |
| `from uxok import on` | **Removed** | `from uxok import event` |
| `Core(config=CoreConfig(...))` | **Removed** | `Core(...)` — pass `CoreConfig` fields as kwargs directly |
| `from uxok import CoreConfig` | **Removed** | `from uxok.protocols import CoreConfig` |
| `Plugin.hook_every` | **Removed** | Self-rescheduling via `hook(name, at_tick=core.tick + N)` |
| `uxok.timing.ScheduleHandle` | **Removed from public API** | No public replacement; timing is now entirely internal |
| `uxok.timing.TickClock` | **Removed from public API** | Internal only; not author-facing |
| `uxok.timing.TickGate` | **Deleted** | Serial gate removed; event dispatch is now concurrent fire-and-forget (see §5) |
| `uxok.timing.TickScheduler` | **Removed from public API** | Internal only; not author-facing |
| `Core.registry` | **Removed** | Use `core.list()` and `core.get_plugin()` |
| `Core.capability_system` | **Removed** | Consume with `core.get_capability()`; discover with `core.list().capabilities` |
| `Core.list_capabilities` / `Core.get_capability_info` | **Removed** | Discovery consolidated onto `core.list()` (`PluginCollection.capabilities`) |
| `Core.precache_hooks` | **Made private** (`Core._precache_hooks`) | Kernel-internal hook warming; not author-facing |
| `Plugin.emit_after(ms=)` | **Removed** | Use `emit(at_tick=)`; deferral is tick-based (deterministic), no wall-clock variant |
| `HookSystem._register(hook: Hook)` | **Promoted public** | `HookSystem.register(name, callback, *, priority=0, plugin_id="", owner=None)` — primitives-based; no longer takes a `Hook` object |
| `EventBus` / `HookSystem` from `uxok.protocols` | **Removed from public exports** | Not in `protocols.__all__`; reached via `core.events` / `core.hooks`; defined in `uxok.protocols.events` / `.hooks` |
| `EventName` / `HookName` / `PluginId` from `uxok.protocols` | **Removed from public exports** | Plain `str` / `UUID` aliases with no contract value |
| `Registry` from `uxok.protocols` | **Removed from public exports** | Not in `protocols.__all__`; internal |
| `CapabilitySystem` from `uxok.protocols` | **Removed from public exports** | Not in `protocols.__all__`; internal |
| `CoreConfig.tick_operation_timeout` | **Removed** | Serial gate is gone; timeouts are a plugin concern (compare `current_tick − fired_at_tick`) |
| `CoreConfig.tick_queue_max_size` | **Removed** | Serial gate queue is gone; no dispatch queue exists |
| `CoreConfig.tick_queue_overflow` | **Removed** | Serial gate queue is gone; no dispatch queue exists |
| `CoreConfig.blocked_plugins` | **Removed** | No kernel-level plugin blocklist; hosts enforce admission policy before calling `register_plugin()` |
| `Registry.block` / `unblock` / `is_blocked` | **Removed** | No kernel-level plugin blocklist; hosts enforce admission policy before calling `register_plugin()` |

Zero backward compatibility on all removed names. `on` is completely gone; code
using `@on(...)` or `from uxok import on` raises `ImportError`. `CoreConfig`
is not at top-level `uxok`; it is only importable from `uxok.protocols`.
