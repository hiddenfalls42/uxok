# Spec 0005 — Reconciled Implementation Spec: Admission Probe + Transitive Attenuation

- **Status:** Finalized for implementation
- **Date:** 2026-06-25
- **Type:** Implementation spec, not a new proposal. It merges the *accepted, now-shippable*
  surface of two RFCs into one build plan and records the decisions taken on their open forks.
- **Implements:**
  - RFC 0003 v2 (`0003-admission-probe-and-gating.md`) §4.1–4.3 — the `validate | commit` split,
    `check_plugin`, atomic admission folded into `register_plugin`.
  - RFC 0004 (`0004-transitive-attenuation.md`) §3 (claim correction + convention), §4 (return
    guard), §5 (lifecycle asterisk).
- **Explicitly defers** (each behind a missing consumer): RFC 0003 §4.4 opaque interceptor; RFC
  0004 §3.3 payload-detector flag, §4.4 argument guard, §6 full membrane / caretaker / lockdown /
  compartment; the `get_plugin(raw=True)` split.
- **Constitution:** `API.md` + `CHANGELOG.md` edits land in the same commit as the code (pre-1.0
  breaking-change policy). No change to the `"open"` default, the resolution hot path,
  `enforce_requires`, `requires`/`resolves`, or hot-reload.

---

## 0. Decisions recorded (the forks, resolved)

| Fork | Decision | Basis |
|---|---|---|
| Return-guard breadth (0004 §4) | **Refuse leaked `Plugin` *and* kernel handles (raise).** *(Revised from "wrap Plugin → view" by the verification pass — see §0.1.)* | Plugin-only missed the `return self.core` crown-jewel leak (0004 §2.2); wrapping is not implementable where the guard must live, and raising is the louder signal for a repair loop |
| `unresolvable_resolves` field | **Drop** | Low signal by construction — RFC 0002 legitimizes provider-absent-at-registration, so it flags correct lazy/cyclic/hot-load code as readily as typos |
| `kernel.lifecycle` raw reach (0004 §5, chan C) | **Asterisk-only**, no `raw=True` split | The mature host uses `get_plugin` as its graph-walk-and-dispatch primitive (introspects concrete type + private attrs); descriptive-default is the rare case, and the split attenuates `kernel.lifecycle` against its own definition (full authority by design, RFC 0001 §3.2.2) |
| Payload detector (0004 §3.3, chan B) | **Convention now (bus + hooks), defer the flag** | Host's hand-written payloads are already data; the only real consumer is the future self-coding loop. The convention carries the value; the flag would be an orphan |
| Opaque interceptor (0003 §4.4) | **Defer** | No in-repo consumer; lands with the host derive phase |

---

## 0.1 Verification pass (pre-implementation) — what was checked, what changed

Every load-bearing claim was checked against the code before this spec was declared buildable.

**Verified ✓**
- `register_plugin` wraps `_register_plugin_now` in `async with self._lifecycle_lock` (`_core.py:252`) — so A.3's "validates under the lifecycle lock immediately before commit" is correct.
- The validation atoms exist and are extractable: `validate_requirements` (`_capability_system.py:206`, missing + dep-collection), collision pre-flight (`:261-265`), `_validate_protocol_contract` (`:90`, raises — wrap per-capability in `_admit`), id collision (`_core.py:274`).
- `CapabilityFacet` is constructed at exactly one site, gated on `capability_access == "sealed"` *and* a typed resolution (`_capability_system.py:181-184`). So Feature C is inherently sealed-only — **no mode branch needed**, confirmed.
- The real `Core.get_capability` is the **ungated root**: "the consumer-side `requires` gate runs upstream… before delegating to this root" (`_core.py` docstring). So the reflection escape genuinely bypasses the gate (Feature D claim #2 stands).

**Corrected (3) — the spec was not airtight as first written:**

1. **Feature C cannot wrap — it must refuse.** `CapabilitySystem.__init__` holds **only** a `policy` (`_capability_system.py:46`) — no registry, no collection service, no core. But a `PluginView` is built by `_build_view(plugin, order, used_by, registry)` (`_plugin_collection_service.py:88`), needing load-order, registry-wide `used_by`, and the registry. So `attenuate_return` (which lives on `CapabilitySystem`, called from `CapabilityFacet._forward`) **cannot construct a `PluginView`**. Wrapping would require coupling `CapabilitySystem` to the registry/collection — against its deliberately minimal single-dependency design. **Resolution:** refuse a leaked `Plugin` by raising, exactly like the kernel handles. This eliminates the `PluginView` dependency entirely, and a raised error is the *louder, more catchable* signal for a self-coding repair loop than a silently-downgraded view. (Alternative considered and rejected: inject the collection service into `CapabilitySystem` — real coupling increase for one guard.)
2. **Reflection escape is `_Plugin__core_real`, not `_Plugin__core`.** The plugin stores `__core_real` (always the real ungated `Core`) and `__core_view` (the `CoreFacet` under sealed) (`plugin/_base.py:268-275`). There is **no** `__core` attribute — the original citation would have been a dead reference. Fixed in Feature D claim #2 and C.4.
3. **The "reach" claim is not in `API.md`/`KERNEL_ARCHITECTURE.md`.** It is made in **RFC 0001 §2.2 (`:81`), RFC 0001 §1 (`:18`), and RFC 0002 §7 (`:260`)**. `API.md §12` is "Framework event contracts" (no such claim); §15 / `KERNEL_ARCHITECTURE.md` don't carry it either. So Feature D *corrects* the claim in those three RFC sites and *adds* the invocation-boundary caveat to `API.md`'s `get_capability`/`capability_access` section as new constitutional semantics — it does not "correct" a claim that isn't there.

---

## 1. Scope & the unifying principle

Two RFCs, one discipline: **stop claiming and pretending properties in-process Python cannot
enforce; build the cheap, real mechanisms; document the ceiling honestly.** RFC 0003 applied it to
the registration watchdog ("can't bound sync CPU — don't pretend a fail-safe"); RFC 0004 applies it
to channel closure ("can't `harden()` — don't claim the graph covers references"). This spec ships
the mechanisms that *are* enforceable and corrects the claims that aren't.

Three buildable things plus a docs correction:

- **A — Admission** (0003): a side-effect-free `check_plugin` + an atomic at-commit re-admission,
  built by splitting validate from commit.
- **C — Return guard** (0004 §4): under `sealed`, a typed resolution that returns a leaked
  authority handle (a live `Plugin`, or `Core`/`CoreFacet`/`LifecycleFacet`) is **refused (raise)**.
- **D — Claim correction** (0004 §3.1 / §5): *invoke* not *reach*, with the reference, reflection,
  and `kernel.lifecycle` caveats. The load-bearing, near-free part.
- **E — Convention** (0004 §3.2): payloads/hook-args carry data, not handles. Docs only.

A and C are independent code paths (registration vs. sealed-resolution return) and do not interact;
both must preserve the lock-free invariant (decision #12). They share no code (the verification pass
removed C's dependence on `PluginView` — see §0.1).

---

## 2. Feature A — `validate | commit` split, `check_plugin`, atomic admission

### A.1 Extract one pure admission routine

Factor the pre-commit checks out of `_register_plugin_now` (`_core.py:267`) into a single pure
routine that **reads the live graph synchronously, raises nothing, mutates nothing**, and returns
faults. The faults it must compute already exist mid-commit:

| Fault | Source today | Note |
|---|---|---|
| `missing_requires` | `validate_requirements` (`_capability_system.py:206`, raises `MissingCapabilityError`) | reserved grants (`kernel.lifecycle`/`kernel.dispatch`) are exempt and must never appear |
| `id_conflict` | `_register_plugin_now` (`_core.py:274`) | candidate id already in registry |
| `provides_conflicts` | `register_capabilities` collision pre-flight (`_capability_system.py:261-265`) | only under the active `error_on_conflict` policy; tag-discriminated multi-providers are **not** conflicts |
| `contract_failures` | `register_capabilities` protocol check (`:253-255`) | typed-capability protocol-contract violations |

Provide one `_admit(candidate) -> AdmissionResult`. The real register path and `check_plugin` both
call it — **no drift possible** (the check is the enforcer). `register_plugin` raises the existing
errors when `not result.ok`; `check_plugin` returns the result.

Note one split detail: `validate_requirements` today *both* checks missing requires *and* returns
the provider dependency-id set that `registry.add` consumes (`_core.py:288`). `_admit` is
faults-only; the **dependency-id collection stays in the commit path**, computed after admission
passes (it is meaningless when requires are missing). So `validate_requirements` becomes: compute
missing (→ `_admit`) and, only when clean, collect dep-ids (→ commit). No behavior change.

### A.2 `check_plugin` (the advisory probe)

```python
class AdmissionResult:
    ok: bool
    missing_requires: frozenset[str]      # load-order requires with no live provider (reserved-exempt)
    id_conflict: bool
    provides_conflicts: frozenset[str]    # provides colliding with the live graph
    contract_failures: frozenset[str]     # typed-capability protocol-contract violations

async def check_plugin(self, candidate: PluginProtocol) -> AdmissionResult: ...
```

- **Side-effect-free:** no registry mutation, no `_attach_core_to`, no `start()`, no
  `plugin.registered` hook, no `core.plugin_error`, no `drain_plugin_resources`, no
  `_collection_service.invalidate()`.
- **Synchronous read, no `await` between snapshot and verdict** — that is what makes the unlocked
  read coherent under decision #12 and why it takes **no lock**. Do **not** acquire the lifecycle
  lock in `check_plugin`; that would serialize the advisory pre-flight against live registration for
  no benefit. Its result is advisory *because* it is unlocked.
- **Scope boundary (carry verbatim into the docstring):** admission certifies the *declared*
  manifest *fits* the live graph; it does **not** certify the manifest is *complete* for what the
  body resolves at runtime. Under RFC 0002, `resolves` is deliberately not registration-validated,
  so an under-declared `resolves` admits cleanly and fails later as `CapabilityAccessError`.
  Catching that needs the subcore (host §5) or the deferred §4.4 derivation. **"Admitted" means
  "fits the graph now," not "its authority is complete."**

### A.3 Atomic admission inside `register_plugin` (the TOCTOU guard)

No new API. `_register_plugin_now` already runs validation under the reentrant lifecycle lock
immediately before commit; route it through `_admit` so the at-commit re-admission is the named,
guaranteed contract. Any caller that probed (or subcore-tested) earlier and then calls the ordinary
`register_plugin` gets the at-commit re-check for free. **One admission routine, two entries:** the
advisory `check_plugin` (unlocked) and the authoritative `register_plugin` (locked). There is
deliberately **no** separate "atomic register" API.

This closes the *structural* TOCTOU half only — not the behavioral residue of fresh-register
(0003 §6), which stays out of kernel (supervision policy).

### A.4 Tests

- `check_plugin` returns each fault without mutating the graph (assert registry/capability tables
  unchanged, no events/hooks fired).
- `resolves`-only candidate with absent provider **admits** (`ok == True`) — contrast a `requires`
  miss which yields `missing_requires` (and still raises via `register_plugin`).
- Reserved grants never surface in `missing_requires`.
- Probe-then-`register_plugin` where the graph drifts between (e.g. a conflicting `provides`
  registers in between) → `register_plugin` rejects at commit though the earlier probe passed.
- Drift-freedom: a single `_admit` body is exercised by both paths (one test asserts identical
  verdict for the same candidate via `check_plugin` and via a `register_plugin` failure).

### A.5 Docs / API.md

- `API.md` §3.x: document `Core.check_plugin` + `AdmissionResult` (advisory, side-effect-free,
  scope-boundary note, "use `register_plugin` for the guarantee").
- Note the at-commit admission as a named property of `register_plugin`; no signature change.

---

## 3. Feature C — Return guard under `sealed` (refuse leaked authority handles)

### C.1 Where

`CapabilityFacet._forward` (`_capability_facet.py:62-74`) returns the provider result raw at `:74`.
`CapabilityFacet` is constructed at exactly one site, gated on `capability_access == "sealed"` *and*
a typed resolution (`_capability_system.py:181-184`, verified), so the guard is inherently
sealed-scoped — **no mode branch needed**. Insert one transitive step on the return:

```python
result = getattr(provider, item)(*args, **kwargs)
if inspect.isawaitable(result):
    result = await result
return self.__sys.attenuate_return(result)   # NEW
```

### C.2 The guard (refuse, one hop, by decision)

`attenuate_return` lives on `CapabilitySystem`, which holds **only** a `policy` (no registry, no
collection service, no core — verified §0.1). It therefore cannot *build* a `PluginView`, so a
leaked `Plugin` is **refused**, not wrapped — identically to the kernel handles:

```python
def attenuate_return(self, value: Any) -> Any:
    # _LEAK_TYPES resolved lazily (see C.3): (Plugin, Core, CoreFacet, LifecycleFacet)
    if isinstance(value, _LEAK_TYPES):
        raise CapabilityAccessError(
            ..., message="a sealed capability method returned a live authority handle "
                         "(plugin or kernel handle); this is a manifest-invisible authority "
                         "leak and is refused. Return data, ids, or capability names instead."
        )
    return value   # data, dataclasses, primitives, ambient bus/hooks, already-descriptive views
```

Decision detail — **what is refused vs. passed:**

- **Refuse (raise):** a live `Plugin`, and the kernel handles `Core` / `CoreFacet` /
  `LifecycleFacet`. A returned `Plugin` hands over full authority the consumer narrowed away; a
  returned `CoreFacet` carries the *provider's* grant gate (its owner, not the consumer's); a real
  `Core` is the ungated root. All are second-hop escalations with no safe descriptive form
  reachable from where the guard runs. Raising is also the *louder, more catchable* signal for a
  self-coding repair loop than a silent downgrade.
- **Pass through:** the event bus and hook system (**ambient by the RFC 0001 §2.3 decision** — the
  consumer already holds them via its own `CoreFacet.events`/`.hooks`, so returning them leaks
  nothing); already-descriptive `PluginView`/`PluginCollection`/`CapabilityFacet` (themselves
  attenuated); and all data.

### C.3 Implementation notes

- `_LEAK_TYPES` must be resolved **lazily** (a cached module-global tuple, or a deferred import
  inside `attenuate_return`) — `Plugin` lives in `plugin/`, `Core`/`CoreFacet`/`LifecycleFacet` in
  `core/`; a top-level import into `_capability_system.py` risks an import cycle.
- Synchronous: a single `isinstance` against a tuple, evaluated *after* the awaited result. No
  `await` added; the lock-free invariant (decision #12) holds.
- No `PluginView` dependency — the refuse-not-wrap decision removed it (§0.1 correction 1).

### C.4 Honesty bound (carry into the docstring + §4.3 of RFC 0004)

Robustness, not a boundary. An author who *wants* to leak can return `[plugin]` (a one-element list
dodges the one-hop check), or reach `self._Plugin__core_real` (the unattenuated `Core` the plugin
stores at `plugin/_base.py:268`). The guard stops the **accidental** "returned `self` /
`get_plugin(...)` / `self.core` from a sealed method" — the dominant LLM-bug case — and nothing
stronger. The non-recursive, type-enumerated boundary is named, not hidden. (One-hop only: we do
not recurse into containers — Python can't harden the contents anyway, and the dominant accidental
leak is a bare handle, not a wrapped one.)

**Coverage is typed-resolution-only.** `CapabilityFacet` — and therefore this guard — exists solely
for a *typed* (Protocol) resolution under sealed (C.1). A capability resolved **by string name**
returns the raw provider with no facet, so a method leaking a handle on that path is unguarded. In a
predominantly string-resolving consumer — e.g. uxok-host, whose entire RFC-0002 `resolves` surface
is string names — the guard's reach is correspondingly narrow: it closes the typed-path accidental
leak, and is **not** a claim that sealed methods cannot leak handles. (The deeper item — that string
resolution returns the unattenuated provider at all, sidestepping typed attenuation wherever both
forms are available — is an RFC 0001 scope question, out of band here; see the Rust-port note.)

### C.5 Tests

- Sealed typed resolution whose method returns a live `Plugin` → **raises** `CapabilityAccessError`.
- Sealed method returning `self.core` (CoreFacet) / the real `Core` / a `LifecycleFacet` → **raises**
  `CapabilityAccessError`.
- Sealed method returning the bus / hooks / a `PluginView` / a `CapabilityFacet` / a dataclass /
  primitives → **unchanged** (passes through).
- `open`/`declared` modes: no `CapabilityFacet`, so behavior is unchanged (regression guard).

### C.6 Docs / API.md

- `"sealed"` semantics note: a sealed typed resolution **refuses** (raises) when a provider method
  returns a live `Plugin` or a kernel handle (`Core`/`CoreFacet`/`LifecycleFacet`) — a
  manifest-invisible second-hop leak. Behavior, no new public symbol.

---

## 4. Feature D — Claim correction + asterisks (the load-bearing honesty fix)

**Correct** the over-strong "complete who-can-reach-what" claim where it is actually made —
verified to be **RFC 0001 §2.2 (`0001-…:81`)**, **RFC 0001 §1 (`:18`)**, and **RFC 0002 §7
(`0002-…:260`)** — replacing *reach* with *invoke* and attaching **all three** caveats below. (It
is *not* in `API.md §12` — that is "Framework event contracts" — nor in `KERNEL_ARCHITECTURE.md`;
§0.1 correction 3.) Separately, **add** the same caveat to `API.md`'s `capability_access` /
`get_capability` section (§3.2) as new constitutional semantics, and to `README.md` if it makes the
pitch. (The original 0004 §3.1 carried only caveat 1; findings #2/#3 add 2 and 3.)

> Under `capability_access="declared"`/`"sealed"`, the `requires ∪ resolves` grant set is the
> complete statement of **what a plugin may *invoke* by name through its kernel handle** — the
> *reviewable* surface for hallucinated authority. It is **not** an enforced reference-isolation
> boundary, for three reasons, all out of scope here (RFC 0004 §6):
> 1. **References cross edges.** A live handle can ride a return value, an argument, an **event or
>    hook payload** (those channels are deliberately ambient — RFC 0001 §2.3) and arrive somewhere
>    no manifest records.
> 2. **Reflection reaches past the gate.** A plugin can reach `self._Plugin__core_real` — the
>    *unattenuated* `Core` it stores even under sealed (`plugin/_base.py:268`), whose
>    `get_capability` is the ungated root (the `requires` gate runs upstream, not here) — and invoke
>    by name **outside its grant set**. So even the *invoke* claim holds only *modulo the reflection
>    escape* (RFC 0001 §3.2.3); closing it needs the lockdown-analog that stays out of scope.
> 3. **`kernel.lifecycle` is a declared full-authority escalation.** A holder obtains raw plugin
>    instances via `get_plugin` and therefore has ambient reach to every plugin's full surface.
>    This is the deliberate tier-2 grant (RFC 0001 §3.2.2). Enumerate `kernel.lifecycle` holders
>    (greppable, one per manifest) and scrutinize exactly those.

This is the part that makes a published claim *true*. It ships first and has no consumer dependency.

---

## 5. Feature E — Payload convention (docs only; covers bus **and** hooks)

Add to the event-bus reference, the hook reference, and the plugin-authoring how-to:

> **Event payloads and hook arguments carry data, not live handles.** Pass primitives, dataclasses,
> ids, or capability *names* — not `self`, not another plugin instance, not your `self.core`. A live
> reference on a broadcast channel is an authority edge that appears in no manifest and cannot be
> reviewed. The kernel does not (and in Python cannot cheaply) enforce this; it is a convention.

Note the one in-kernel exception honestly: the `plugin.registered` hook emits the live `Plugin`
(`_core.py:296`) so subscribers know which plugin registered. Document it as an intended,
enumerable exception to the convention (the same class as the `kernel.lifecycle` asterisk), not a
leak to fix — and **not** something this spec's deferred detector would later flag as a defect.

The `CoreConfig.debug_payload_authority_check` flag and the `publish()`/hook-dispatch scan are
**deferred** to land with the self-coding host that consumes the warnings (per the constitutional
default-off-needs-a-consumer rule).

---

## 6. Reconciliation notes

- **No code-path conflict.** A touches the registration path + a new `Core.check_plugin`; C touches
  the sealed-resolution return in `CapabilityFacet`. Disjoint.
- **No shared code.** The first draft had C reuse the Q3b `PluginView`; the verification pass
  (§0.1) removed that — C now refuses rather than wraps, so A and C share nothing but the lock-free
  discipline.
- **Lock-free invariant (decision #12) preserved by both:** `check_plugin` is a synchronous unlocked
  read; `attenuate_return` is a synchronous post-await `isinstance`. Neither adds an `await` inside a
  capability/registry critical section.
- **Honesty framing is shared** (§1) — state the principle once; D and the 0003 watchdog note are
  the two instances.
- **Forward-compat with the deferred interceptor (0003 §4.4):** when it lands, it runs inside
  `_admit` and folds an opaque `diagnostics` blob into `AdmissionResult`; design `_admit` so a later
  optional interceptor hook is a clean addition, but **do not** add the field now (no consumer).

---

## 7. Out of scope (the ceiling, named)

Deferred exactly where the source RFCs left it: the opaque interceptor (0003 §4.4); the
payload-detector flag (0004 §3.3); the argument guard A′ (0004 §4.4); the full transitive membrane,
per-handle caretaker revocation, `lockdown()`-analog for the reflection escape, and
compartment-of-the-namespace (0004 §6 — the membrane work RFC 0002 §10 already points at); the
`get_plugin(raw=True)` split (§0). The behavioral residue of fresh-register (0003 §6) stays plugin
/ supervision policy, not kernel.

*SES/E lineage precision (for any prose that cites the borrow):* the reflection escape maps to a
**missing membrane** (you hold the real object), not `lockdown()`; `lockdown()`'s analog is the
ambient-import/primordial reach handled under *compartment*. And channel B's true ceiling is the
absence of a **marshalling boundary** on the in-process bus (pass-by-copy / wrap-on-cross), not the
absence of `harden()` — `harden()` makes shared *cap* references tamper-proof, it does not sever a
reference riding a data channel. Get these mappings right if the lineage table is reproduced.

---

## 8. Philosophy check (CLAUDE.md framework)

1. **Framework or product?** Framework — mechanisms (admit; attenuate a leaked handle) + a claim
   correction. No policy. ✅
2. **Adds complexity?** A is a refactor (extract one routine) + one method; C is one `isinstance`
   branch reusing an existing view; D/E are prose. No new subsystem. ✅
3. **Opt-in?** C fires only under `sealed` (already opt-in); A's `check_plugin` is additive; D/E are
   docs. `"open"` default untouched. ✅
4. **Breaks existing code?** Not at defaults. Under `sealed`, code that *relied on* a sealed method
   returning a raw plugin/kernel handle breaks — but that reliance *is* the leak being closed, and
   sealed is pre-1.0 opt-in. ✅
5. **Simpler way?** D (stop claiming the unenforceable) is the simplest response to an unenforceable
   property; A reuses the real validators; C reuses the existing view. ✅
6. **Core or plugin?** Core — registration, the facets, and the claim are the kernel's. The
   payload convention (E) is guidance to plugin authors. ✅
7. **Lock-free invariant?** Preserved (§6). ✅

---

## 9. Implementation order (each step independently shippable, non-breaking at defaults)

1. **D + E (docs / claim correction).** Highest ROI, zero code, makes a published claim true. No
   consumer dependency. Correct the claim in RFC 0001 §2.2/§1 and RFC 0002 §7 (the verified sites);
   add the caveat to `API.md` §3.2. Run `code-auditor` against the amended `API.md`.
2. **A (split + `check_plugin` + atomic admission).** Refactor `_register_plugin_now` to route both
   paths through `_admit`; add `check_plugin` + `AdmissionResult`. In-repo consumers land with it
   (supervisor pre-restart probe; tests asserting "would fail" without register/rollback).
3. **C (return guard).** Add `attenuate_return`; gate is implicit in `CapabilityFacet` being
   sealed-only. Tests per C.5.

Each step updates `API.md` + `CHANGELOG.md` in its own commit. Coverage stays ≥ 91.5%.

---

## 10. CHANGELOG (draft, split across the commits above)

```markdown
### Changed
- Docs/constitution (RFC 0004): the `requires ∪ resolves` grant set is documented as the complete
  *invocation* boundary, not a reference-isolation boundary — a live reference can cross a granted
  edge or the (deliberately ambient) event/hook channels, reflection (`_Plugin__core_real`) reaches
  the ungated Core, and `kernel.lifecycle` holders obtain raw plugins. Corrects the
  "complete who-can-reach-what" claim in RFC 0001 §2.2 / RFC 0002 §7.

### Added
- `Core.check_plugin(candidate) -> AdmissionResult` (RFC 0003 v2): a side-effect-free admission
  probe that validates a candidate against the live graph without committing — the advisory
  pre-flight for write→check→repair loops. Authoritative atomic admission is built into
  `register_plugin` (no separate API).
- Return guard (RFC 0004 §4): under `capability_access="sealed"`, a typed resolution that returns a
  live `Plugin` or a kernel authority handle (`Core`/`CoreFacet`/`LifecycleFacet`) is now refused
  (raises `CapabilityAccessError`) — accidental second-hop authority leaks fail fast.
  `"open"`/`"declared"` unchanged.
```

Defaults unchanged; no item is a breaking change on its own.
```
