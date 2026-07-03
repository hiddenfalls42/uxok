# Changelog

All notable changes to uxok are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Per the constitutional API
policy, every change to the public API (`docs/manifests/API.md`) lands in the same
commit as its CHANGELOG entry.

## [Unreleased]

### Added
- `Core.load_plugins(sources)` (RFC 0008): boots a batch of `(code, origin)` plugin
  sources in one call, computing load order from the candidates' `provides`/`requires`
  and committing them together under a single hold of the lifecycle lock. Returns plugin
  names in commit order; fresh-load-only (no hot-reload in a batch). On failure raises
  the new `BatchLoadError` (`PluginError` subclass), which carries `phase` (`"plan"` |
  `"commit"`), `cause`, `installed` (the committed prefix, in commit order), and `failed`
  so the host can implement its own rollback-or-keep policy. `docs/manifests/API.md` §2.2
  and §8 updated in this commit.

### Changed
- Capability-collision error message: under `capability_collision="error_on_conflict"`, a
  rejected second provider now raises `PluginError` with
  `Capability '<name>' is already provided by: <providers> (capability_collision policy is
  'error_on_conflict')` instead of the misleading `Capability '<name>' not available.
  Available: <providers>` (that wording belongs to the missing-capability case and described
  the opposite condition). Exception type and raise sites are unchanged; only the message
  text differs.
- Error-message overhaul (kernel-wide audit of raise sites; messages now describe the actual
  condition and carry identifying context):
  - `Core.get_capability(..., tag=...)`: a tag mismatch no longer collapses into the generic
    missing-capability `CapabilityError` (which self-contradictorily listed the capability as
    both unavailable and available) — the precise `No provider for capability '<cap>' has tag
    '<tag>'. Provider tags: [...]` message is preserved through the rewrap.
  - `MissingCapabilityError`: message is now `No registered plugin provides required
    capability: <caps> (required by plugin '<name>') …` with load-order guidance, replacing
    the garbled "Develop Capability or remove dependency" advice; kernel raise sites also
    pass the available-capabilities list.
  - Registry name conflict: names the colliding plugin name and the existing holder's id, and
    points at `name=` (was `Plugin <uuid>: name already in use`).
  - Dependency cycle in `load_order()`: names the plugins on the cycle.
  - `load_plugin`: module-execution failures (e.g. a failing top-level import) now say
    `Plugin code failed while executing at module top level: …` instead of claiming a
    compile failure; genuine `SyntaxError`s keep the compile wording.
  - Active-operation guard and dependents-present rejections: name the plugin (not just its
    UUID) and state the way forward (wait / unregister dependents / `force=True`).
  - Hook registration with a non-callable, and invalid plugin names: messages now name the
    hook/plugin and state the actual rule (plugin names must also start with a letter;
    "PluginProtocol" jargon dropped).
  - Sealed return guard: `CapabilityAccessError.plugin_name` is now `""` instead of the
    leaked type's name (the type remains in the message) — no plugin identity exists at
    that seam, and a type name masquerading as a plugin name misleads programmatic handlers.

### Breaking
- `Plugin.start()` after the instance was stopped now raises `PluginError` (was a bare
  `RuntimeError`), naming the plugin and pointing at the one-shot instance rule and
  `get_state()`/`restore_state()`. All sibling lifecycle failures already raise
  `PluginError`, so supervisors catching it no longer miss this case. Code that caught
  `RuntimeError` for this path must catch `PluginError` (`docs/manifests/API.md` §Plugin
  updated in this commit).
- `MissingCapabilityError.__init__` gained a trailing optional `requirer: str | None = None`
  keyword (additive; sets `self.requirer`). Positional construction is unaffected.
- Documentation: `self.get_capability(...)` is now the single canonical plugin-author idiom
  for resolving a capability (the convenience sibling of `self.emit`/`self.hook`/
  `self.config`). `self.core.get_capability(...)` is demoted to an internal facet /
  security-model detail — it remains callable and enforces the identical `requires ∪ resolves`
  gate (it is the gated `CoreFacet` route exercised by the secure-capability suite), but is no
  longer presented as a co-equal plugin idiom and is removed from reader-facing usage examples
  (README, tutorial). **No runtime behavior change**: both routes still work identically; this
  is a docs/guidance clarification only.

### Added
- Ambient `check_plugin` on the attenuated facet (RFC 0006): `CoreFacet` now forwards
  `check_plugin(candidate) -> AdmissionResult`, mirroring `list()`, so a plugin under
  `capability_access="declared"`/`"sealed"` can call `self.core.check_plugin(...)` with **no
  grant**. This closes the seam the probe's first real consumer exposed — before, the only
  attenuated path to the admission probe was the `_Plugin__core_real` reflection escape, taking
  the whole un-attenuated `Core` to perform a read-only query. The probe is the read-only sibling
  of `list`: its `AdmissionResult` is data (name sets + bools), not handles, so it discloses no
  more than discovery already does and needs no attenuation on the way out. Deliberately **not**
  added to `LifecycleFacet` (that would force a probe-only consumer to take graph-mutation
  authority) and **not** behind a new grant (no protection a `list`-equivalent read lacks).
  Additive; no change to admission semantics, `register_plugin`'s atomic admission, the `"open"`
  default, or the resolution hot path.
- Sealed return guard (RFC 0004 §4 / spec 0005 §C): under `capability_access="sealed"`, a
  typed resolution's facet now **refuses** (raises `CapabilityAccessError`) when a provider
  method returns a live authority handle — a `Plugin` or a kernel handle
  (`Core`/`CoreFacet`/`LifecycleFacet`). This closes the accidental second-hop leak
  (`return self` / `return self.core` / `return get_plugin(...)` from a sealed method) where a
  consumer would receive authority its manifest never declared. Data, the ambient bus/hook
  systems, and already-attenuated views pass through unchanged; it is one-hop robustness, not
  a boundary (a containered or reflection-reached handle still escapes). `CapabilityAccessError`
  gains an optional `message=` override for this path. `"open"`/`"declared"` are unaffected.
- Admission probe (RFC 0003 v2 / spec 0005 §A): `Core.check_plugin(candidate) -> AdmissionResult`,
  a side-effect-free probe that validates a candidate against the live plugin graph **without
  committing** — no registration, no `start()`, no `plugin.registered` hook, no events. It
  reports structural faults (`missing_requires`, `id_conflict`, `provides_conflicts`,
  `contract_failures`); `AdmissionResult.ok` is a derived property. The advisory pre-flight for
  write→check→repair loops. The *same* admission now runs atomically inside `register_plugin`
  under the lifecycle lock (closing the structural TOCTOU window), so the probe and the commit
  share one routine and cannot drift — there is no separate "atomic register" API.
  `AdmissionResult` is importable from `uxok.protocols` / `uxok.core` (not a top-level
  export — the kernel hands it to the caller). No behavior change to `register_plugin` at
  defaults: the same exceptions (`MissingCapabilityError`, `PluginError`) are raised.
- Visible capability revocation (RFC 0001 §3.4): the kernel publishes
  `core.capability.rebound` on hot-swap (a provider instance was replaced) and
  `core.capability.revoked` when the last provider of a capability is unregistered.
  Payloads documented in `docs/manifests/API.md` §12. Additive and independent of
  configuration; default behavior is unchanged.
- Secure capabilities, consumer side (RFC 0001 §3.2): `CoreConfig.capability_access`
  (`"open"` | `"declared"` | `"sealed"`, default `"open"`). Under `"declared"`/`"sealed"`
  a plugin may resolve only capabilities it declares in `requires`, and its view of the
  kernel (`self.core`) is an attenuated `CoreFacet` exposing only the tier-1 ambient
  surface (`tick`, `slip`, `state`, `config`, `events`, `hooks`) and a gated
  `get_capability` — graph control and host control are no longer ambient. New
  `CapabilityAccessError` (raised when a plugin resolves an undeclared capability).
- Secure capabilities, provider side (RFC 0001 §3.3): under `capability_access="sealed"`,
  a typed `get_capability(SomeProtocol)` returns a protocol-limited facet that forwards
  only the protocol's public methods to the live provider — non-protocol members raise
  `AttributeError`, a hot-swap rebinds the facet transparently, and a call after the
  provider is revoked raises `StalePluginError`. Untyped string resolutions return the raw
  provider even under `"sealed"`.
- Tier-2 graph-control grant (RFC 0001 §2d): `kernel.lifecycle` is a reserved,
  kernel-provided capability. Graph control (`register_plugin`, `unregister_plugin`,
  `load_plugin`, `get_plugin`) is no longer ambient on the attenuated `CoreFacet`; a plugin
  that declares `requires={"kernel.lifecycle"}` resolves it via `get_capability` and
  receives a `LifecycleFacet` forwarding exactly those four methods. The grant is always
  satisfiable (no provider plugin, no bootstrap ordering) and resolves identically under
  every `capability_access` mode.
- Attenuated discovery (RFC 0001 §3.2.2): `list()` is now **ambient** on the attenuated
  `CoreFacet`, so a plugin can enumerate the graph under `"declared"`/`"sealed"` without the
  `kernel.lifecycle` grant — "what exists" is benign and no longer requires graph-mutation
  power to observe. It returns descriptive-only `PluginView`s.
- Resolution grants (RFC 0002): `Plugin.__init__` gains a keyword-only
  `resolves: set[str] | frozenset[str] | None` parameter, and `PluginMetadata` gains a
  frozen `resolves: frozenset[str]` field (default empty), normalized identically to
  `requires` (Protocol types accepted and reduced to names). Under
  `capability_access="declared"`/`"sealed"`, the runtime resolution gate (`enforce_requires`)
  now checks the **union** `requires ∪ resolves`: a plugin may resolve any capability in
  either set. Unlike `requires`, `resolves` is **not** validated at registration — it
  authorizes lazy, cyclic, or hot-loaded resolutions whose providers need not exist when the
  resolver registers. New reserved grant `kernel.dispatch` (added to `RESERVED_CAPABILITIES`)
  authorizes resolving **any** capability by name, for control planes / dispatchers; it backs
  no facet and is never itself resolved. Backward-compatible at defaults (`resolves` empty;
  `"open"` short-circuits before the gate), and zero runtime cost (a synchronous set-union
  membership test, no new awaits). `CapabilityAccessError`'s message now reports the runtime
  grant and points at `resolves`.

### Changed
- **Breaking (pre-1.0):** the kernel no longer auto-starts on first plugin registration. `register_plugin`, `load_plugin`, and hot-reload now require the core to be `RUNNING` and raise `CoreError` otherwise. Call `core.start()` (or use `async with Core() as core:`) before registering plugins. The context-manager path and already-started cores are unaffected — hosts that already start explicitly see no behavioral change.
- **Breaking (pre-1.0):** plugin construction is now coreless (RFC 0001 §3.2.3). The core
  is no longer a constructor argument; the kernel attaches it at register/reload time, so
  `self.core` is available from `on_start` onward, not inside `__init__`. Plugins change
  from `def __init__(self, core): super().__init__(core, ...)` to
  `def __init__(self): super().__init__(...)`. Default `capability_access="open"` keeps
  `self.core` as the real `Core`, so runtime behavior is otherwise unchanged.
- Replaced the reference `supervisor` plugin with `plugins/example_host/` — a small,
  runnable sensor/alerting host that wires every kernel primitive (event bus, hook
  extension points, capability provider/consumer, lifecycle, the tick system, config
  schema, state continuity, and graceful shutdown) as a worked "hello world." The deleted
  supervisor's restart-on-failure policy is no longer shipped; supervision remains a
  natural plugin to build on the `core.plugin_error`/`core.hook_error` signals.
- Docs/constitution (RFC 0004 / spec 0005 §4): the `requires ∪ resolves` grant set is
  documented as the complete **invocation** boundary — what a plugin may invoke by name —
  not a reference-isolation boundary. A live reference can still cross a granted return /
  argument / (ambient) event-or-hook payload edge, reflection (`_Plugin__core_real`) reaches
  the ungated `Core`, and `kernel.lifecycle` holders obtain raw plugin instances. Corrects
  the over-strong "complete who-can-reach-what" claim in RFC 0001 §2.2 and RFC 0002 §7, adds
  the caveat to `API.md` §3.2, and documents the data-not-handles payload convention in the
  event-system, hook-system, and plugin-architecture explanations.

### Removed
- **Breaking (pre-1.0):** removed the `blocked_plugins` config field and the
  `Registry.block()`/`unblock()`/`is_blocked()` methods. There is no longer a kernel-level
  plugin blocklist. Hosts must enforce admission policy before calling `register_plugin()`.
  Note: `register_plugin` no longer returns `False` on a blocked name — that return path is
  gone entirely; it returns `True` or raises.
- **Breaking (pre-1.0):** the invocation surface of plugin discovery (RFC 0001 §3.2.2):
  `PluginView.call`, `PluginView.get_object`, and the `PluginCollection` fan-outs built on
  them (`call_method_on_all`, `start_all`, `stop_all`). A `PluginView` is now a description,
  not a handle — `list()` cannot be a backdoor to invoking or holding another plugin's live
  instance. To act on a plugin, resolve it via the `kernel.lifecycle` grant (`get_plugin`)
  or a typed capability. Benign live reads (`status`, `ready`, `uptime`, `methods`) remain.

## [0.1.0] — 2026-06-23

Initial public release of **uxok** — an experimental, hot-loading plugin microkernel
for Python.

### Added
- Kernel primitives: event bus, hook system, plugin registry, capability system, and
  the `Plugin` developer-experience base class.
- Hot-loading lifecycle: register / unregister / hot-swap plugins on a running core,
  with a constitutional state graph (INITIALIZED → RUNNING → STOPPING → STOPPED/FAILED).
- Concurrent fire-and-forget event dispatch with causal ordering.
- Constitutional public API defined in `docs/manifests/API.md`.
- Reference `supervisor` plugin demonstrating policy-as-a-plugin over kernel failure signals.

[0.1.0]: https://github.com/hiddenfalls42/uxok/releases/tag/v0.1.0
