# RFC 0006 — Attenuated `check_plugin`: the admission probe needs a consumer path

- **Status:** Accepted — implemented (kernel half; host migration tracked separately)
- **Date:** 2026-06-25
- **Type:** RFC — kernel boundary correction (small, additive)
- **Motivated by:** the first real consumer of `check_plugin`. While wiring the admission
  probe into the `uxok-host` self-coding pipeline (an admission pre-gate and a loader
  pre-check), the *only* way to reach `check_plugin` under `capability_access="sealed"` turned
  out to be the reflection escape — the un-attenuated real `Core`.
- **Relates to:** RFC 0001 §3.2.2 (facet tiers; ambient vs granted), Spec 0005 §A (`check_plugin`
  + atomic admission), RFC 0004 §D/§E (the enumerable reflection escape; the data-not-handles
  payload convention).
- **Non-goals:** No change to admission *semantics* (0005 §A stands), to `register_plugin`'s
  atomic admission, to the `"open"` default, or to the resolution hot path.

---

## 1. The gap

`check_plugin(candidate) -> AdmissionResult` landed as a `Core` method (Spec 0005 §A): pure,
unlocked, side-effect-free, returning the same verdict `register_plugin` will enforce atomically.

But under `capability_access="declared"` / `"sealed"`, a plugin does **not** hold the real `Core` —
it holds a `CoreFacet` (`core/_core_facet.py`). The two attenuated surfaces a plugin can reach are:

- **`CoreFacet`** — exposes `events`, `hooks`, `config`, `tick`, `slip`, `state`,
  `get_capability` (gated on `requires`), and `list()`. **No `check_plugin`.**
- **`LifecycleFacet`** (the `kernel.lifecycle` grant) — exposes `register_plugin`,
  `unregister_plugin`, `load_plugin`, `get_plugin`. **No `check_plugin`.**

So there is **no attenuated path to the probe.** The only way a plugin can call it under sealed is
to reach past the facet to the name-mangled real `Core` — the documented reflection escape
(`_Plugin__core_real`). A pure, read-only predicate is reachable only through the single path that
bypasses the entire sealed boundary.

**Concrete evidence (the consumer that surfaced this), in `uxok-host`:**

- `src/uxok_host/capabilities/system/admission_gate.py` — the admission pre-gate calls
  `real_core(self).check_plugin(candidate)`.
- `src/uxok_host/base_plugins/plugin_loader.py` (`_load_flow`) — the loader pre-check calls
  `real_core(self).check_plugin(instance)` before a live register.

Both take the maximal-authority escape to perform a minimal-authority operation.

## 2. Why it matters

1. **It is a hole in the sealed story, exactly where a new blessed mechanism lives.** The purpose
   of `check_plugin` is to let consumers — gates, loaders, supervisors — pre-flight a candidate.
   Yet under sealed the only way to *consume* it is to un-attenuate. The feature and its sole
   access path point in opposite directions.
2. **Least authority is violated.** A gate that only wants to ask "would this fit the graph?" must
   first obtain the unrestricted `Core`. A read acquires write-everything authority.
3. **It normalizes the escape.** RFC 0004 §D worked to make the reflection escape a *rare,
   enumerable* asterisk. Routing every admission probe through it pushes `real_core` from "audited
   infra" toward "the normal way to call admission," eroding the property 0004 set out to protect.

## 3. The shape of the fix

`check_plugin` is morally a sibling of `CoreFacet.list()`, **not** of the `LifecycleFacet`
mutations:

- **It is a pure read of graph state.** `list()` already lives on `CoreFacet` as a *"tier-1
  ambient, attenuated"* member, by the RFC 0001 §3.2.2 decision that "what exists" is benign.
  `check_plugin` asks a question of the same kind — "would this candidate fit what exists?" — and
  mutates nothing.
- **Its return is already data, not handles.** `AdmissionResult` is a frozen dataclass of name
  sets and bools — the canonical data-not-handles payload (RFC 0004 §E). Unlike `LifecycleFacet`
  methods, which return *raw live plugin instances* (full authority by design), there is **nothing
  to attenuate on the way out**; the sealed return guard never even has to fire.
- **It discloses no more than `list()` already does.** A yes/no plus fault names over graph state
  that `list()` already exposes ambiently. No new information-disclosure boundary is crossed.

**Recommendation: expose `check_plugin` on `CoreFacet` as a tier-1 ambient, attenuated read,
adjacent to `list()`.** The facet forwards to its private real `Core` (exactly as `list()` does)
and returns the `AdmissionResult` unchanged. No new grant; `self.core.check_plugin(candidate)`
simply works under sealed; the `real_core` requirement disappears for every consumer.

## 4. Alternatives considered

- **(a) Add `check_plugin` to `LifecycleFacet` (under `kernel.lifecycle`).** Simple, and the loader
  already holds that grant. But it **over-grants**: `kernel.lifecycle` is mutate-the-graph authority
  ("a granted tier-2 capability is full authority by design," per the facet's own docstring). A
  probe-only consumer — a gate — would have to take `register`/`unregister` power just to ask a
  question. Wrong tier for a read.
- **(b) A new reserved read-only grant (e.g. `kernel.admission`).** Principled least-authority, but
  **unnecessary ceremony**: `check_plugin` discloses no more than the already-ambient `list()`, so
  gating it adds a grant without adding protection. Reserve a new grant only *if* `check_plugin` is
  later extended to reveal materially more than `list()` does.
- **(c) Status quo — the `real_core` escape.** Rejected: maximal-authority path for a
  minimal-authority operation, and it normalizes the escape (§2.3).

## 5. Acceptance criteria

- Under `sealed`, a plugin with **no special grant** can call `self.core.check_plugin(candidate)`
  and receive an `AdmissionResult`.
- The host's `admission_gate` and `plugin_loader` replace `real_core(self).check_plugin(...)` with
  `self.core.check_plugin(...)`; `real_core` usage shrinks back to its genuine residue (the
  dormant-flow `_attach_core`, the completer's cross-plugin config), keeping the escape *small and
  enumerable* as 0004 §D intends.
- No change to `register_plugin`'s atomic admission, the `"open"` default, `enforce_requires`, or
  the resolution hot path. `CoreFacet` gains one forwarding method; nothing else moves.

## 6. Scope note

This is a **consumer-path correction**, not a re-opening of admission semantics. Spec 0005 §A is
unchanged. It closes the one boundary seam that the first real consumer of the probe exposed: a
read-only kernel predicate should be reachable by a read-only path, not only by the reflection
escape that exists to be the exception.
