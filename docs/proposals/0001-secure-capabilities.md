# RFC 0001 — Secure Capabilities

- **Status:** Draft (for discussion)
- **Date:** 2026-06-24
- **Affects:** `docs/manifests/API.md` (constitutional), `CoreConfig`, the capability
  system, the hot-reload swap path, `README.md`, `KERNEL_ARCHITECTURE.md`
- **Type:** Constitutional API change — per the versioning policy, the accepted version
  of this proposal lands its `API.md` and `CHANGELOG.md` edits in the same commit as the
  implementation. This document is the discussion artifact that precedes that edit; it is
  not itself the constitution.

---

## 1. Summary

Today an uxok "capability" is a **named service locator**: `get_capability(name)`
resolves any registered provider by name and hands back the **raw plugin instance**.
Nothing checks that the caller declared `requires=[name]`, and the caller can reach every
method and attribute on the returned object — not just the protocol it asked for.

This proposal turns the capability system into a real **capability** in the OS/security
sense — an authority granted at bind time and exercised through a narrow, revocable
handle — by borrowing three mechanisms directly from the MIT exokernel (xok):

1. **Secure binding** — authorize at declaration time, then use is cheap; a plugin can
   only resolve capabilities it declared in `requires`. (*consumer direction*)
2. **Attenuation / narrow interface** — a resolved typed capability exposes only its
   protocol surface, not the whole plugin. (*provider direction*)
3. **Visible revocation / abort protocol** — when a provider is hot-swapped or
   unregistered, consumers are notified and their handles transparently rebind or
   fail loudly, instead of silently holding a dead instance.

Plus one smaller borrow — **downloaded selection policy** (let a consumer supply the
provider-selection function instead of the kernel's fixed enum) — and a documentation
change stating honestly what we borrow from exokernels and what we deliberately do not.

All of it is **opt-in via `CoreConfig`** and **backward compatible at defaults**.

## 2. Motivation

### 2.1 The gap

The `requires`/`provides`/`used_by` authority graph is already fully materialized at
registration — `validate_requirements()` computes the dependency edges, and the registry
keeps `by_requires` / `by_capability` indexes. It is rich, descriptive metadata. It just
does not constrain anything at runtime:

- A plugin that declares `requires=[]` can still call `get_capability("anything")`. The
  manifest can lie about a plugin's reach.
- `get_capability(Greeting)` validates the provider against the `Greeting` protocol, then
  returns the **whole plugin** — the consumer can call non-protocol methods, mutate state,
  and reach other capabilities through it. There is no attenuation.
- After a hot-swap, a consumer holding the resolved provider holds a reference to a
  **torn-down instance**. There is no revocation dialogue; staleness is silent.

So "capability" currently writes a security check the code does not cash. This is the
real reason the term has felt wrong.

### 2.2 Why this is the right borrow

The exokernel thesis is *separate protection from management*: the core multiplexes a
resource and authorizes access once (a **secure binding**), then management lives in
replaceable user code. We cannot borrow xok's hardware multiplexing — we have no
protection boundary between mutually distrustful principals, and adding one (process /
subinterpreter isolation) would fight the simplicity philosophy. But the **binding
discipline** maps cleanly onto plugins and is exactly what is missing:

| xok mechanism | uxok analog (this RFC) |
|---|---|
| Secure binding (authorize once at bind, cheap use after) | `requires` becomes a grant; resolution is checked against it |
| Capability = unforgeable ref that designates **and** authorizes | typed resolution returns an attenuated facet limited to the protocol |
| Visible revocation / abort protocol | swap/unregister emits revocation events; handles rebind or raise |
| Downloading code into the kernel (ASH/DPF) | consumer-supplied `selector` for provider choice (policy as downloaded code) |

The payoff is concrete, not purist:

- **Least privilege / bounded blast radius.** A plugin can *invoke by name* only what its
  manifest declares. For agent-generated or third-party plugins, the `requires` list becomes
  the thing you review before loading — a sandbox story without process isolation.
- **An auditable authority graph.** Because the manifest can no longer lie about what a
  plugin *invokes by name*, the `requires`/`provides` graph is the complete, reviewable
  statement of **who can invoke what** — the surface where hallucinated authority shows up.
  Static review means something. It is **not** an enforced reference-isolation boundary,
  though: a live handle can still ride a return value, an argument, or an (ambient)
  event/hook payload, and reflection (`self._Plugin__core_real`) reaches the ungated core.
  See the invocation-boundary caveat in `API.md` §3.2 (and spec 0005 §4) for the three named
  escapes. *(Corrected post-implementation: this originally read "the complete 'who can reach
  what'," which overstated reference reachability — RFC 0004 / spec 0005 §4.)*
- **Safe hot-reload.** The headline feature stops having a silent sharp edge: consumers
  survive a swap instead of holding a corpse.
- **It earns the name.** Both "capability" and "uxok" stop being aspirational.

### 2.3 What "binding contract" actually means

The shift is not "we now check `requires`." It is that **`requires`/`provides` stop being
advisory metadata that happen to drive load order, and become the capability list** — the
complete, unforgeable statement of every way a plugin touches, and is touched by, the rest
of the system. Today the manifest *describes*; after this it *constrains*.

**The contract is not the check — it is "there is no other door."** A `requires` gate in
`get_capability` is worthless while `self.core` is the unrestricted root (§10). The
enforceable contract exists only once ambient authority is removed (the `CoreFacet`,
§3.2.1), so that the manifest is the *only* channel. That is the load-bearing idea.

It is a contract in **both directions**:

- *Consumer side* — `requires` becomes a grant: a plugin can *reach* only what it declared.
- *Provider side* — `provides` + protocol becomes the only export: a plugin can *be
  reached as* only what it declared, and a holder sees only that protocol surface (§3.3),
  not the whole object.

And it composes with the contract we already have, giving **two complementary contracts**:

| contract | question it answers | mechanism |
|---|---|---|
| **Type contract** (exists today) | Is the provider *shaped* like the protocol? | `_validate_protocol_contract` — structural, method-by-method |
| **Authority contract** (this RFC) | Is the `requires`/`provides` graph the *complete, enforced* boundary of who-may-touch-whom? | `CoreFacet` + `requires` gate + facets |

Type says "this thing is shaped like `Storage`." Authority says "and you're allowed to
hold it, and only its `Storage` face." Together the manifest becomes a real contract in
both senses.

Two honesty notes so "completely enforced" is not oversold:

1. **Full symmetry holds only for *typed* capabilities.** An untyped string capability has
   no protocol surface to attenuate to, so it gets the consumer-side `requires` gate but
   not provider-side narrowing — the raw provider comes back. The clean bi-directional
   contract is therefore a property of *typed* capabilities; untyped ones are
   half-enforced. (A deliberate nudge toward typing capabilities; see §6-open-#1.)
2. **It binds cooperative plugins, not in-process adversaries.** Name-mangling is true
   privacy against accident and convention, not against an author who deliberately digs
   out `self._Plugin__core_real`. That is the boundary we declined to build (§3.7).

**Scope decision (deliberate, not by analogy).** The manifest declares three pairs:
capabilities (`requires`/`provides`), events (`events_subscribed`/`events_published`), and
hooks (`hooks_consumed`/`hooks_provided`). Making the manifest a binding contract invites
"for all three?" — but the answer is not automatic. Capabilities are *point-to-point*
dependencies, where least-privilege fits cleanly and this RFC applies it. Events and hooks
are *broadcast* mechanisms whose value **is** loose coupling; enforcing "you may publish
only what you declared" could fight the very thing the bus is for. So capability
enforcement is the contract that clearly pays off; event/hook enforcement is a separate
question carrying real tension and is explicitly **out of scope** here, to be decided on
its own merits rather than dragged along.

## 3. Design

### 3.1 One knob, increasing strictness: `CoreConfig.capability_access`

A single ordered policy field rather than several booleans (keeps configuration unified
and the strictness legible):

| value | consumer binding | provider attenuation | behavior |
|---|---|---|---|
| `"open"` *(default)* | not enforced | none | **Exactly today's behavior.** Any plugin resolves any capability; returns the raw provider. |
| `"declared"` | enforced | none | A plugin may resolve only capabilities in its `requires`; still returns the raw provider. |
| `"sealed"` | enforced | typed → facet | As `"declared"`, **and** a typed resolution returns a facet exposing only the protocol surface. |

- Default `"open"` ⇒ **zero behavior change** on upgrade.
- `"declared"` is the consumer (secure-binding) direction; `"sealed"` adds the provider
  (attenuation) direction — i.e. "both directions."
- Validated in `CoreConfig.__post_init__` alongside the other capability enums.

### 3.2 Consumer secure binding (`"declared"` and `"sealed"`)

Under these modes, a **plugin-originated** resolution of capability `X` raises a new
`CapabilityAccessError` unless `X` is in that plugin's declared `requires` (string name,
or the name derived from a Protocol type).

- Enforcement happens in `Plugin.get_capability` (it knows `self`, hence the caller's
  `requires`). The check is a synchronous set-membership test against
  `self.metadata.requires` — it adds **no `await`** and therefore preserves the lock-free
  capability-mutation invariant (decision record #12).
- **The embedding application is the trusted root.** A *direct* `core.get_capability(...)`
  call (not routed through a plugin) is unrestricted — like the root principal in an
  object-capability system. Only plugin-to-plugin resolution is gated. `Core.get_capability`
  therefore needs to distinguish "called by the host" from "called on behalf of a plugin";
  the cleanest path is for `Plugin.get_capability` to do the check before delegating, and
  for `Core.get_capability` to remain the unrestricted root entry point. (See Open
  Question 6.1.)
- `MissingCapabilityError` at registration is unchanged and complementary: it means a
  required capability is *absent*; `CapabilityAccessError` means a capability exists but
  was *not declared* by the caller.

#### 3.2.1 Attenuating the core handle (required for §3.2 to mean anything)

The audit (§10) found that enforcing the `requires` check inside `Plugin.get_capability`
is **insufficient on its own**: every plugin holds the real, unrestricted `Core` as
`self._core`, exposed publicly via the `self.core` property. A plugin can therefore
bypass the check trivially — `self.core.get_capability(X)` goes straight to the
unrestricted root — and worse, `self.core.list()` → `PluginView.call(...)` reaches any
plugin's any method, while `self.core.get_plugin`, `self.core.events`, `self.core.hooks`,
`self.core._capability_system`, and `self.core._plugin_configs` expose the entire kernel
and other plugins' scoped state. This is **ambient authority**, and the object-capability
model this RFC invokes forbids it: a plugin must only be able to act through capabilities
it was granted.

So `"declared"`/`"sealed"` require that **the handle a plugin holds is itself attenuated**:

- The real `Core` is held privately by the `Plugin` base class — name-mangled
  (`self.__core`) for true privacy, matching the existing `__state` pattern — and used
  only by base-class machinery (tick scheduler, config scoping, internal emit/hook
  plumbing).
- The public `self.core` property returns a **`CoreFacet`** exposing only the
  plugin-safe surface: `tick`, `config`, the `get_capability` that enforces `requires`,
  and event/hook *registration* — **not** `list`, `get_plugin`, `register_plugin`,
  `unregister_plugin`, raw `_capability_system`, or `_plugin_configs`.
- **The embedding application keeps the real `Core`** (it constructs it) and is the
  trusted root — unrestricted, exactly as today. Only the *plugin's view* of the kernel
  is attenuated.
- Threat model is unchanged and explicit: this bounds *cooperative* plugins (and
  agent-generated ones) by their declared manifest. A plugin author who deliberately digs
  out `self._Plugin__core_real` has broken in on purpose; we do not defend against in-process
  adversaries (that needs the protection boundary we explicitly do not build — §3.7).

This is a larger change than the original §3.2 implied — it touches what `self.core` *is*
— and it is the load-bearing part. Without it, secure binding is decorative. It also
naturally subsumes the provider direction: `CoreFacet` not exposing `list`/`get_plugin`
removes the other ambient routes to a raw plugin instance, so §3.3's facets are the
*only* remaining way to hold another plugin, not just the polite one.

#### 3.2.2 The `CoreFacet` allow-list (three tiers)

The allow-list *is* a plugin's authority over the kernel, so it is drawn empirically from
how real plugins use `self.core`, not by taste. Every `Core` member falls into one of
three tiers:

| `Core` member | tier | rationale |
|---|---|---|
| `get_capability` (enforced) | **1 — ambient** | the point of the RFC; now gated on `requires` |
| `tick`, `slip` | **1 — ambient** | lock-free clock reads, no authority |
| `config` | **1 — ambient** | read-only global config |
| `state` | **1 — ambient** | observing core state is benign |
| `events` (EventBus) | **1 — ambient** | broadcast mechanism; ambient *by the §2.3 scope decision* (events are not enforced) |
| `hooks` (HookSystem) | **1 — ambient** | same reasoning as `events` |
| `list` | **1 — ambient, attenuated** | "what exists" is benign; but it returns **descriptive** `PluginView`s only (no `.call` / `.get_object`). Invocation-capable views require the tier-2 grant. |
| `register_plugin`, `unregister_plugin`, `load_plugin`, `get_plugin` | **2 — granted** | graph control / raw-instance handles; reachable only via a declared `kernel.lifecycle` capability |
| `start`, `stop`, `__aenter__`, `__aexit__` | **3 — host-only** | only the embedding application (which constructs the `Core`) may stop or restart the kernel; never on the facet |

- **Tier 1** is a plugin's birthright — present on every `CoreFacet` unconditionally.
- **Tier 2** is what `requires` actually *buys*: graph control becomes a privileged
  capability the kernel itself provides (e.g. `kernel.lifecycle`), so a plugin that
  manipulates the graph must declare it and is auditable in its manifest. The
  reference **supervisor** is the canonical holder (see migration, §7).
- **Tier 3** never appears on the facet at all; it belongs to whoever holds the root
  `Core`.

**Empirical grounding (2026-06-24 pass over `plugins/`, `docs/`, `tests/`):**

- Docs teach plugin authors only tier-1 members: `get_capability`, `events`, and `tick`
  (the tick how-to leans on it heavily). **No doc teaches graph control via `self.core`.**
- The **only** reference plugin reaching tier 2 is `supervisor` — `get_plugin`,
  `register_plugin`, `unregister_plugin` (`plugins/supervisor/supervisor_plugin.py:138,
  143,146,167`) — exactly its restart-the-crashed-plugin job.
- Tier-3 calls (`start`/`stop`) and private-attribute reaches (`_capability_system`,
  `_active_operations`) appear **only in tests acting as the host/harness**, which use the
  real `Core`, not the facet — so they are unaffected.

Net blast radius of `"declared"` on real plugin code: **one plugin** (the supervisor),
which migrates by declaring `requires=["kernel.lifecycle"]`. That containment is itself
evidence the boundary is in the right place.

**Two design items this tier split opens:**

1. **The kernel must *provide* the tier-2 capabilities.** Reclassifying graph control as
   `kernel.lifecycle` means there is a built-in provider for it — either a kernel-internal
   plugin registered at start, or a privileged sub-facet the `CoreFacet` attaches only
   when the holder declared the grant. This is real design work and the main way this RFC
   could grow; specify it before implementing tier 2.
2. **Attenuated discovery.** `list()` stays ambient but must hand back descriptive
   `PluginView`s with `call`/`get_object` removed; the invocation-capable view is gated
   behind the same `kernel.lifecycle` (or a narrower `kernel.discovery`) grant. Decide
   whether to split discovery from lifecycle or fold them.

Mechanically, `CoreFacet` is a thin wrapper holding the real `Core` privately, with
explicit named forwarders for tier 1 and **no `__getattr__` passthrough** (a passthrough
would re-expose everything and defeat the allow-list — the same reason `PluginView`
deliberately omits `__getattr__`).

#### 3.2.3 How each tier is bound, and where the facet is injected

The three tiers are not just permission levels — they are three *kinds of binding*,
escalating in strength:

| tier | bound by | strength |
|---|---|---|
| **1 ambient** | facet *membership* — `CoreFacet` forwards it unconditionally | exposure |
| **2 granted** | a runtime *check* against the manifest's `requires` | a permission (checkable only because authority is data) |
| **3 host-only** | reference *topology* — callable only by something holding the real `Core`, and only its constructor does | **unforgeability by construction** |

Tier 3 has **no check to bypass and no grant to request.** A plugin cannot "qualify"
because qualifying is not a state a plugin can reach — it would have to *be* the code that
called `Core(...)`. The embedding application writes `core = Core(config)` and holds the
only reference to the real kernel; tier 3 is "whoever holds that reference," i.e. the host
by definition. This is strictly stronger than tier 2's check: there is nothing to
misconfigure and no code path to have a bug in.

Two honesty bounds on "impossible":

- It is **impossible-by-reference-topology, not by cryptography.** A plugin that
  *deliberately* reflects (`self._Plugin__core_real`, `facet._CoreFacet__core`, `gc`
  walking) can still climb back to the real core. Name-mangling is privacy against
  accident and convention, not against an in-process adversary — the §3.7 line.
- A plugin can always `from uxok import Core; Core().start()`, but that spawns its
  *own empty* core with zero authority over the host's running kernel or its siblings.
  "A plugin can't start/stop **the** core it runs in" holds; "a plugin can't touch the
  `Core` class" is neither true nor a goal.

**Injection mechanism — decided: (b) inject at register time.** The plugin no longer
receives the core through `__init__`; the kernel attaches it when the instance enters the
kernel, via a single `_attach_core(instance)` helper that:

- stores the real `Core` name-mangled-private and exposes the appropriate handle, and
- **branches on `capability_access`**: under `"open"` it attaches the real `Core`
  (today's behavior, backward compatible); under `"declared"`/`"sealed"` it attaches a
  `CoreFacet`. So the tiering — and tier-3 binding — is a **property of the attenuated
  posture**; choosing `"open"` is choosing to keep ambient authority, by design. This is
  what lets §3.1 keep its "`open` == exactly today" promise.

Consequences and costs (the reason the choice is (b), made explicit):

- **Construction-contract change.** `def __init__(self, core)` becomes coreless; `self.core`
  is available *after* the kernel attaches it (register/start), not during `__init__`.
  This revises the design note at `src/uxok/core/_core.py:480–482` and every doc
  example. It actually *simplifies* reload — the kernel constructs a bare instance from
  the loaded code and attaches the core itself, rather than threading `self` in as the
  core. This is the **largest migration cost in this RFC** — larger than the policy field.
- **Two injection sites, one helper.** Fresh register (`_register_plugin_now`) and reload
  (`_reload_plugin_now → _swap_plugin`) are distinct paths; both call `_attach_core`. In
  the swap path the attach must precede `new_plugin.start()` (`_swap_plugin` step 3,
  `:666`), because `on_start` uses `self.core`.

**Efficiency.** Negligible. One small wrapper object per plugin instance (plugins are
already heavyweight). The alloc happens at register/reload — operations that are already
multi-step async critical sections, so one allocation is noise. Per-call overhead is
avoided where it matters: resolve-once immutable members (`events`, `hooks`, `config`) are
stored as **plain attributes** on the facet (direct access, no indirection); only the
genuinely-changing reads (`tick`, `slip`, `state`) are thin properties, and `get_capability`'s
added work is an O(1), synchronous `requires` set-membership test (preserving the
lock-free invariant, decision record #12). The one hot-loop read is `tick` (tick
scheduling); a `return self.__core.tick` forwarder is sub-microsecond at `tick_rate ≤
10000`, and a plugin can hoist it to a local in a tight loop if ever needed.

**Hot-loading impact.** The decisive property: a `CoreFacet`'s *target is the real
`Core`*, the longest-lived object in the system — it is **not** replaced during a plugin
swap. So unlike the §3.3 capability facets (which point at provider instances that get
swapped and therefore need the §3.4 rebind/revoke protocol), **a `CoreFacet` never needs
rebinding or revocation.** Injecting at register adds exactly one step to the reload
sequence — *construct (bare) → `_attach_core` → `start` → `restore_state`* — at one
`alloc` of cost, against the far larger teardown/rebuild already in a reload. Net: (b)
adds **zero new revocation surface** to hot-loading and a rounding-error of latency.

### 3.3 Provider attenuation — facets (`"sealed"`)

Under `"sealed"`, `get_capability(SomeProtocol)` returns a **facet**: a thin object that
forwards only the protocol's public methods to the live provider and exposes nothing else.

- **Typed only.** A facet is meaningful only when there is a protocol to attenuate to.
  An untyped string resolution (`get_capability("storage")`) has no declared surface, so
  it returns the raw provider even under `"sealed"` — documented as caveat emptor, and a
  nudge toward typed capabilities.
- The facet reuses the structural protocol metadata already computed by
  `_validate_protocol_contract` / `get_protocol_methods` — no new introspection.
- The facet is structurally the protocol (it satisfies the same type), so typed call
  sites and mypy are unaffected. It is **not** a new public symbol in `uxok.__all__`;
  it is an implementation detail that quacks like the protocol. Its *behavior* (notably
  revocation, §3.4) is what gets documented.

### 3.4 Visible revocation / abort protocol

Independent of `capability_access`; improves hot-reload safety in every mode that hands
out a kernel-owned handle, and adds observability for all modes.

On `swap_provider` / `unregister_capabilities_by_plugin`, the kernel publishes capability
lifecycle events on the bus:

- `core.capability.rebound` — a swap installed a new provider for a capability that is
  still provided. Payload: `{capability, old_provider_id, new_provider_id}`.
- `core.capability.revoked` — the last provider for a capability went away with no
  replacement. Payload: `{capability, old_provider_id}`.

Handle behavior (facets, and any kernel-owned handle):

- On **rebind**, the handle transparently resolves to the new provider on its next call
  (same live-resolution pattern `PluginView` already uses — registry is authoritative,
  not a cached weakref).
- On **revoke**, the handle raises `StalePluginError` (existing error) on next use,
  rather than invoking a torn-down instance.
- A consumer holding a **raw** provider (i.e. `"open"`/`"declared"` mode, or an untyped
  resolution) does not get this protection — it gets the *event* but keeps its raw
  reference. Documented explicitly. This is the incentive to use typed, sealed
  capabilities.

This is the xok dialogue: **notify** (event) → **graceful degradation** (handle rebinds /
raises) → **forcible reconciliation** (the registry side, which `swap_provider` /
`unregister` already perform).

### 3.5 Downloaded selection policy (smaller borrow)

When several providers offer the same capability, selection is currently a fixed enum
(`capability_selection` ∈ `first_registered`/`last_registered`) plus `tag=` filtering.
Borrowing "download the policy," add an optional keyword:

```python
get_capability(capability, *, tag=None, selector=None)
```

- `selector: Callable[[list[CapabilityInfo]], CapabilityInfo] | None`. When given, the
  consumer's function chooses among the candidate providers; the kernel exposes the
  candidate set (the names, versions, tags it already has) and steps out of the policy.
- `tag=` remains the common-case sugar; `selector=` is the escape hatch for "the app
  knows best." Default unchanged (`capability_selection`).
- Lowest priority of the items here; include or defer per discussion.

### 3.6 Naming

Keep the **noun** "capability" — once §3.2–3.3 land, the term is *correct*, which resolves
the long-standing discomfort. The discomfort was the unearned implementation, not the
word.

Optional, deferred: introduce **`Plugin.bind(...)`** as the acquisition **verb** (xok's
own term — "secure binding"), with `get_capability` retained as an alias. `self.bind(Greeting)`
reads as "bind to a declared capability" and encodes the semantics at the call site. Not
required for this RFC; flagged as Open Question 6.2 to avoid API churn mid-flight.

### 3.7 Documentation: honest exokernel framing

Add one paragraph to `README.md` and `KERNEL_ARCHITECTURE.md` stating what we borrow and
what we do not, so the name is homage rather than overclaim:

> uxok takes its name and its instincts from the MIT exokernel: a minimal core that
> provides **mechanism, not policy**, with management pushed out into replaceable plugins,
> and resources acquired through **secure bindings** that authorize once and are cheap to
> use. It deliberately does **not** implement hardware-grade protection between mutually
> distrustful principals — plugins share a process and a trust domain. The borrowed ideas
> are the binding discipline, visible revocation, and downloaded policy; the omission is
> the protection boundary.

Also reconcile the contradictory lineage language: `CLAUDE.md` says "inspired by Linux"
(monolithic) while the README says "microkernel." Settle on the exokernel-influenced
framing above and drop the Linux line.

## 4. API.md deltas (concrete)

1. **§2.1 / §7.3 CoreConfig** — add field:

   | `capability_access` | `str` | `"open"` | `"open"`, `"declared"`, `"sealed"` |

2. **§2.2 / §9 `get_capability`** — add optional keyword `selector` to both `Core` and
   `Plugin` signatures:
   `async def get_capability(self, capability: str | type, *, tag: str | None = None, selector: Callable[[list[CapabilityInfo]], CapabilityInfo] | None = None) -> Any`
   New raise: `CapabilityAccessError` when a plugin resolves an undeclared capability
   under `"declared"`/`"sealed"`. Note: facet return semantics under `"sealed"`.

3. **§8 Exceptions** — add `CapabilityAccessError(CapabilityError)` to the hierarchy and
   to `uxok.errors.__all__` / `uxok.__all__`:

   ```
   CoreError
   └── CapabilityError
       ├── MissingCapabilityError   (absent at registration)
       └── CapabilityAccessError    (exists, but not declared by the caller)   ← new
   ```

4. **Events** — document the kernel-published `core.capability.rebound` and
   `core.capability.revoked` events and their payloads in the events section.

5. **§10** — note that under `"sealed"`, typed resolutions return a protocol-limited
   facet whose calls raise `StalePluginError` after the provider is revoked. No new public
   symbol added.

## 5. CHANGELOG entry (draft, lands with implementation)

```markdown
### Added
- Secure capabilities (RFC 0001): `CoreConfig.capability_access`
  (`"open"` | `"declared"` | `"sealed"`). `"declared"` enforces that a plugin
  resolves only capabilities it declares in `requires`; `"sealed"` additionally
  returns an attenuated facet for typed capabilities. New `CapabilityAccessError`.
- Visible capability revocation: `core.capability.rebound` and
  `core.capability.revoked` events on hot-swap/unregister; kernel-owned handles
  transparently rebind or raise `StalePluginError`.
- `get_capability(..., selector=...)` for consumer-supplied provider selection.

### Changed
- Documentation: state exokernel lineage honestly (borrowed: secure binding,
  visible revocation, downloaded policy; not borrowed: protection between
  distrustful principals). Drop the "inspired by Linux" framing.
```

Default remains `"open"`, so nothing in this entry is a breaking change on its own.

## 6. Decisions and open questions

**Decided (recorded so we do not relitigate):**

- **D1 — Caller identity plumbing → resolved by audit (§10).** The resolution path is
  singular (`Plugin.get_capability → Core.get_capability → CapabilitySystem`), so no
  internal path resolves on a plugin's behalf. *But* the audit found `self.core` is the
  unrestricted root, so the enforcement point is not `Plugin.get_capability` alone — it is
  the attenuated `CoreFacet` (§3.2.1). This is now a requirement of the design, not an
  open question.
- **D2 — `bind` verb: deferred to a future RFC.** Keep `get_capability` as the verb for
  now. Rationale: once enforcement lands, the *noun* "capability" is already correct, so
  the rename buys ergonomics, not correctness; doing it mid-flight churns the public API
  while the semantics are still settling. Revisit after `"declared"` ships, as its own
  small RFC, with `get_capability` retained as an alias for one minor.
- **D3 — `selector` (§3.5): split out to RFC 0002.** It is an orthogonal axis (*which
  provider* vs. *may I, and what surface*), adds public API, and has a non-trivial
  interaction with `"sealed"` (what the selector callable is allowed to *see* — raw
  providers would be an attenuation hole). It does not belong in the security story.
  This RFC keeps only a forward pointer; §3.5 stays as motivation for 0002.
- **D4 — core handle injected at register time, not via `__init__` (§3.2.3).** A single
  `_attach_core` helper, mode-aware (`open` → real core, `declared`/`sealed` → `CoreFacet`),
  called from both the register and reload paths. Rationale: makes tier-3 binding
  topological (the plugin instance never retains the real core under an attenuated
  posture) and simplifies reload. Accepted cost: `def __init__(self, core)` becomes
  coreless — the largest migration in this RFC.

**Still open:**

1. **Untyped capabilities under `"sealed"`.** Return raw (proposed) vs. forbid untyped
   resolution entirely in sealed mode (stricter, more breaking). Leaning raw + document,
   especially since §3.2.1's `CoreFacet` already closes the *other* raw-instance routes.
2. **When does the default flip** from `"open"`? Proposed: ship `"open"`, gather usage,
   flip to `"declared"` in a later pre-1.0 minor (a deliberate, announced breaking
   change), and never make `"sealed"` the default (it changes return *types* for untyped
   consumers' mental model). Decide at flip time, not now.
3. **`CoreFacet` surface → resolved into a concrete three-tier allow-list (§3.2.2).** The
   empirical pass is done; what remains are two scoped design items it opened:
   (a) how the kernel *provides* the tier-2 `kernel.lifecycle` capability (built-in plugin
   vs. privileged sub-facet), and (b) whether attenuated discovery is its own
   `kernel.discovery` grant or folded into `kernel.lifecycle`.

## 7. Backward compatibility & migration

- **At defaults (`"open"`):** no behavior change. The new `selector` param is optional;
  the new error class and events are additive.
- **`"declared"`:** breaks plugins that resolve undeclared capabilities — they must add
  the names to `requires` (which also makes their manifest honest and their load order
  correct). This is opt-in and, pre-1.0, an acceptable break when the user chooses it.
  The empirical pass (§3.2.2) puts the real blast radius at **one** reference plugin: the
  **supervisor** migrates by declaring `requires=["kernel.lifecycle"]` to keep its
  `register_plugin`/`unregister_plugin`/`get_plugin` access. It is the canonical worked
  example of "ambient power becomes a declared, auditable grant."
- **`"sealed"`:** additionally, code that reached non-protocol members through a typed
  resolution breaks. Migration: declare those members on the protocol, or use an untyped
  resolution intentionally.

## 8. Philosophy check (CLAUDE.md decision framework)

1. **Framework or product?** Framework — mechanism (kernel mediates grants), policy stays
   in plugins/host. ✅
2. **Adds complexity?** One unified `CoreConfig` field; reuses the existing authority
   graph, protocol metadata, and live-resolution pattern. Not new subsystems. ✅
3. **Opt-in?** Yes — `capability_access` defaults to today's behavior. ✅
4. **Breaks existing code?** Not at defaults. Stricter modes break only when the user
   opts in, pre-1.0. ✅
5. **Simpler way?** A single ordered policy beats several booleans; revocation reuses the
   `PluginView` resolution pattern rather than inventing one. ✅
6. **Core or plugin?** Core — it is the protection/binding mechanism, the one thing only
   the kernel can mediate. The *policies* (selection, supervision) stay in plugins. ✅
7. **Lock-free invariant preserved?** Yes — enforcement is synchronous set membership; no
   `await` added inside any capability-state mutation (decision record #12). ✅

## 9. Suggested implementation order

Each step is independently shippable and non-breaking at defaults:

1. **Revocation events + handle invalidation on swap/unregister** (§3.4). Highest ROI,
   no policy change, makes hot-reload honest. Run the `code-auditor` against this RFC and
   `API.md` after.
2. **`CoreFacet` + `capability_access="declared"`** (§3.2 + §3.2.1–3.2.3) — the
   load-bearing step. Sub-order: (a) `_attach_core` helper + coreless `__init__`,
   mode-aware injection at the register and reload paths (D4); (b) the tier-1/2/3
   `CoreFacet` allow-list; (c) the `requires` gate + `CapabilityAccessError`. Gated behind
   the policy so defaults are untouched. The `__init__` contract change (D4) is the part
   to land carefully — it touches every plugin and doc example.
3. **`"sealed"` facets** (§3.3) — provider attenuation.
4. **Docs disclaimer + lineage reconciliation** (§3.7) — can land anytime, independent of code.

`selector` (§3.5) is **not** in this order — split to RFC 0002 per D3.

## 10. Audit findings (2026-06-24)

Findings from auditing the capability-resolution and plugin↔core paths before committing
to the §3.2 design:

- **Resolution path is singular and clean.** `Plugin.get_capability → Core.get_capability
  → CapabilitySystem.get_capability` is the only chain; `validate_requirements` and
  `register_capabilities` are lifecycle, not resolution. No hidden path resolves providers
  on a plugin's behalf. ✅ for the *narrow* §3.2 check.

- **Ambient authority defeats the narrow check (the rough spot).** `Plugin.__init__` sets
  `self._core = core` (`src/uxok/plugin/_base.py:113`) to the **real, unrestricted
  Core**, re-exposed as the public `self.core` property (`:239`). Consequences:
  - `self.core.get_capability(X)` bypasses any `Plugin`-level `requires` check.
  - `self.core.list()` → `PluginView.call(...)` / `get_object()` reaches **any plugin's
    any method** (the base class itself relies on this at `:51`).
  - `self.core.get_plugin`, `.events`, `.hooks`, `._capability_system`, `._plugin_configs`
    expose full kernel internals and other plugins' scoped config.

  → The enforcement point cannot be `Plugin.get_capability`; it must be an attenuated
  `CoreFacet` (§3.2.1). This is the single most important consequence of the audit and is
  now baked into the design and the implementation order.

- **Architectural observation worth keeping.** The attenuation makes the *plugin's view of
  the kernel* itself a capability — the host holds the root `Core`, plugins hold a facet.
  That is a cleaner, more exokernel-faithful boundary than the current "everyone holds
  root," and it is the change that most moves the architecture toward earning the name.
  It also raises a design question for D3-open-#3: discovery (`list()`) may deserve to be
  a *declared* capability rather than ambient.
