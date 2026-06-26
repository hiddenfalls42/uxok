# RFC 0004 — Transitive Attenuation (Closing the Second-Hop Leaks)

- **Status:** Reconciled into `0005-admission-and-attenuation-spec.md` (the finalized build spec).
  §3–§5 are carried there with corrections from review/verification — including: the return guard
  **refuses** a leaked `Plugin` rather than wrapping it (the capability system can't build a
  `PluginView`), the reflection escape is `_Plugin__core_real` not `_Plugin__core`, and the
  claim-correction sites are RFC 0001 §2.2/§1 + RFC 0002 §7 (not `API.md §12`). Read §4/§5 here as
  the discussion artifact; trust `0005` for what gets built. (Deferred parts — §3.3 detector flag,
  §4.4 arg guard, §6 membrane/lockdown — remain deferred.)
- **Date:** 2026-06-25
- **Affects:** `docs/manifests/API.md` (constitutional, claim wording + caveat),
  `docs/manifests/KERNEL_ARCHITECTURE.md`, `README.md`, `core/_capability_facet.py`,
  `core/_core_facet.py` (`LifecycleFacet` docstring/return), `events/_bus.py` (optional
  debug guard only), `CHANGELOG.md`
- **Builds on:** RFC 0001 (Secure Capabilities), RFC 0002 (Resolution Grants). This is the
  cheap, honesty-first slice of the object-capability path RFC 0002 §7/§10 deferred
  wholesale.
- **Type:** Constitutional API change — but the load-bearing part is a **documentation
  correction**, not new mechanism. The code changes are additive and default-equivalent.
  The accepted version lands its `API.md` / `CHANGELOG.md` edits in the same commit as the
  implementation.

---

## 1. Summary

RFC 0001 attenuates the **first hop** of every authority edge — `CoreFacet` narrows the
kernel handle, `CapabilityFacet` narrows a provider to its protocol surface — and on the
strength of that, RFC 0001 §2.2 and RFC 0002 §7 both make a headline claim:

> the `requires`/`provides` graph **is** the complete "who can reach what." Static review
> means something.

That claim is **not currently true**, for a reason the object-capability literature names
precisely: attenuation that stops at the first hop leaks at the second. A reference can
travel *through* a granted edge — as a **return value**, an **argument**, an **event
payload**, or a **raw lifecycle handle** — and arrive somewhere the manifest never
declared. SES (Hardened JavaScript) earns the same "no ambient authority" claim only
because it makes attenuation **transitive** (membranes wrap everything crossing the
boundary, recursively) and **closes its channels** (pass-by-hardened-copy, `lockdown()`).
We do the first hop; we have no analog for the rest.

This RFC does three things, in descending order of importance and ascending order of cost:

1. **(Honesty — §3) Correct the claim.** Narrow "who can *reach* what" to "who can
   *invoke* what," and document the event bus as a deliberate ambient channel. This is a
   prose change that makes a claim we already publish *true*. It is the most important part
   and it is nearly free.
2. **(Robustness — §4) Membrane-lite return guard.** When a `sealed` `CapabilityFacet`
   method hands back a live `Plugin` instance, wrap it in the descriptive `PluginView` that
   RFC 0001 Q3b already built, instead of returning raw authority. Closes the most likely
   *accidental* second-hop leak in agent-authored code, reusing existing machinery.
3. **(Disclosure — §5) Asterisk the `kernel.lifecycle` escalation.** `LifecycleFacet`
   returns raw plugins by design; say so where the auditable-graph claim is made, so the
   claim carries its one honest exception.

Everything heavier — a full membrane, argument wrapping, per-handle revocation, an
isolation boundary — is **explicitly out of scope** (§6) and kept deferred exactly where
RFC 0002 §10 left it. This RFC is the part that cannot wait, because §3 fixes something
that is *false in the tree today*.

## 2. Motivation

### 2.1 The second-hop thesis

The ocap rule uxok already invokes is **"only connectivity begets connectivity"** — the
only way to obtain a new authority is for someone who holds it to pass it to you. RFC 0001
makes the *grant* edges explicit and gated. But a granted edge is a *channel*, and a
channel carries whatever you put on it. If what you put on it is another live reference,
you have begotten connectivity that no `requires`/`resolves` entry records.

There are exactly four such channels in the current kernel. Three are open; one is
documented-as-full-authority but unasterisked in the claim.

| # | Second-hop channel | Where | Status today |
|---|---|---|---|
| **B** | **Event payload** carries a live reference | `events/_bus.py`; payloads passed to subscribers by reference (concurrent fire-and-forget) | **open** — and the bus is ambient (any plugin ↔ any plugin), so this is the widest leak |
| **A** | **Return value** of a sealed protocol method is a live `Plugin` | `_capability_facet.py:71` returns `getattr(provider, item)(...)` raw | **open** |
| **A′** | **Argument** to a sealed method is a live handle | same forward, `*args/**kwargs` passed raw | **open** (lower severity; see §4.4) |
| **C** | **Raw lifecycle handle** — `get_plugin` returns the live instance | `_core_facet.py:145-155`; documented "full authority by design" (`:138-139`) | **deliberate**, but the §2.2 claim omits the asterisk |

The first three falsify "the graph is the complete who-can-reach-what." The fourth is an
*intended* escalation that the claim simply forgets to mention.

### 2.2 Why B is the headline (and is not a relitigation of RFC 0001 §2.3)

RFC 0001 §2.3 made a deliberate scope decision: **do not enforce event/hook *declarations*
** (you may publish topics you didn't declare), because broadcast loose-coupling is the
bus's whole value. **This RFC does not touch that decision and agrees with it.** The leak
is orthogonal and sharper: it is not about *which topics* you may publish, it is about a
*live object reference riding in a payload*. Plugin `A` does `self.emit("anything",
payload=self)`; any subscriber `B` now holds `A`'s raw instance — every method, every
attribute, `A`'s own `self.core` — with **no** `requires`, **no** `resolves`, **no** facet,
and **no manifest edge**. Because the bus is ambient (the §2.3 decision keeps it that way),
*any* two plugins can rendezvous over it. A `requires`/`provides` graph that omits this is
not "complete who-can-reach-what"; it is "who-can-reach-what *if no one passes a reference
over the bus*" — an unstated and routinely-false precondition.

For the system's actual pitch — *the manifest is the reviewable blast radius of
agent-authored code* — this is the gap that matters most, because an LLM emitting `self`
(or a fat context object holding live handles) in an event payload is a plausible,
silent, manifest-invisible authority leak.

### 2.3 Why the Python ceiling makes this honesty-first, not enforcement-first

SES closes B with `harden()` (deep-freeze the object graph) + pass-by-copy on channels.
**Python has no `harden()`.** There is no deep-freeze primitive; you cannot make an
arbitrary returned object defensively immutable, and you cannot cheaply prove a payload
carries no references. So unlike JS, we *cannot* turn B into an enforced guarantee without
the isolation boundary we have already declined (RFC 0001 §3.7, RFC 0003 §8). That is
precisely why §3 is a **claim correction plus a convention**, not a new gate: the honest
move is to stop claiming a property we can't enforce, document the channel, and offer one
*opt-in, debug-only* detector — not to pretend the bus is sealed.

This is the same honesty discipline RFC 0003 v2 applied to the watchdog ("in-process
Python can't bound sync CPU; don't pretend to a fail-safe"). The pattern here: *in-process
Python can't harden a channel; don't pretend the authority graph covers it.*

## 3. The honesty fix (load-bearing, ~free)

### 3.1 Correct the claim wording

Everywhere the "complete who-can-reach-what" claim appears — RFC 0001 §2.2, RFC 0002 §7
"Auditability improves," `KERNEL_ARCHITECTURE.md`, `API.md` §12/§15 — replace *reach* with
*invoke* and attach the channel caveat:

> Under `capability_access="declared"`/`"sealed"`, the `requires ∪ resolves` grant set is
> the complete statement of **what a plugin may *invoke* by name through the kernel**.
> It is **not** a complete statement of every object a plugin may come to hold: a live
> reference can still cross a granted edge as a return value or an argument, or cross the
> **event bus** in a payload (the bus is a deliberately ambient broadcast channel — RFC
> 0001 §2.3). Treat the grant set as the *invocation* boundary and the *reviewable* surface
> for hallucinated authority, not as an enforced reference-isolation boundary. The latter
> needs the membrane + isolation work that remains out of scope (RFC 0004 §6).

### 3.2 Document the bus convention

Add to the event-bus reference doc and the plugin-authoring how-to:

> **Event payloads should carry data, not live handles.** Pass primitives, dataclasses,
> ids, or capability *names* — not `self`, not another plugin instance, not your
> `self.core`. A live reference in a payload is an authority edge that does not appear in
> any manifest and cannot be reviewed. The kernel does not (and in Python cannot cheaply)
> enforce this; it is a convention, optionally checkable in debug mode (§3.3).

### 3.3 Optional, opt-in, debug-only payload detector

*Default off. No hot-path cost. Deferred behind the self-coding host as its consumer —
this RFC specifies it but does not require shipping it before that consumer exists, per the
constitutional caveat (RFC 0003 §14.4).*

A `CoreConfig.debug_payload_authority_check: bool = False`. When on, `publish()` does a
**shallow** `isinstance(payload, Plugin)` / one-level container scan and logs a warning
(never raises) naming the publisher, topic, and the offending type. Shallow by deliberate
choice: a deep walk on the dispatch path is exactly the hot-path cost we refuse (RFC 0001
decision #12 / RFC 0003 §7 "no per-call interpreters"). This catches the common
accidental case (`payload=self`) and is honest that it catches nothing deeper.

## 4. Membrane-lite return guard (`"sealed"` only)

### 4.1 The mechanism

`CapabilityFacet._forward` (`_capability_facet.py:62-74`) currently returns the provider's
result raw. Add one transitive step on the *return* path: if the awaited result **is a live
`Plugin` instance**, the consumer asked for a narrow protocol and got handed full
authority — so wrap it in the **descriptive-only `PluginView`** RFC 0001 Q3b already built
(`922955d`), which has no `call`/`get_object`. The consumer can read what it is; it cannot
invoke through it. Everything else returns unchanged.

```python
result = getattr(provider, item)(*args, **kwargs)
if inspect.isawaitable(result):
    result = await result
return self.__sys.attenuate_return(result)   # NEW: wrap leaked Plugin handles only
```

```python
# CapabilitySystem.attenuate_return — the entire membrane, deliberately one hop deep.
def attenuate_return(self, value: Any) -> Any:
    if isinstance(value, Plugin):
        return descriptive_view(value)   # reuse the RFC 0001 Q3b PluginView, no .call
    return value                          # data, dataclasses, primitives — untouched
```

- **One hop, by design.** We do not recurse into containers or returned dataclasses. A
  full membrane would; we do not, because (a) Python can't harden the contents anyway
  (§2.3) and (b) the dominant accidental leak is "method returns a plugin," not "method
  returns a list containing a plugin." Scope it to the 90% case and say so.
- **Reuses existing attenuation.** No new public type — the descriptive `PluginView`
  already exists and is already the kernel's answer to "expose what-it-is, not
  what-it-can-do."
- **`"sealed"` only.** Under `"open"`/`"declared"` the consumer already gets raw providers
  by contract; the guard would be a surprise. It belongs only where attenuation is the
  promise.

### 4.2 Why this is worth code and §3.3 is only a flag

Asymmetry of severity. A return value crosses an edge the consumer *deliberately narrowed*
(it resolved a typed capability precisely to get the protocol, not the plugin) — so silently
handing back the whole plugin violates the consumer's own stated intent, and wrapping it is
honoring that intent, not imposing policy. A bus payload crosses an edge that is ambient by
the §2.3 decision; there is no narrowing intent to honor, so a convention + opt-in warning
is the proportionate response. Same leak, different contract, different fix.

### 4.3 Honesty bound

This is robustness, not a boundary (matching RFC 0001 §3.7, RFC 0003 §8). An author who
*wants* to leak can return `[plugin]` (a one-element list dodges the one-hop check), or
reach `self._Plugin__core`. The guard stops the *accidental* "I returned `self` /
`get_plugin(...)` from a sealed method" — the LLM-bug case that is the dominant real risk —
and nothing stronger. The non-recursive boundary is named, not hidden.

### 4.4 Arguments (A′) — deferred

The symmetric case (consumer passes a live handle *into* a sealed method) is left
unguarded. Rationale: passing your own handle in is the *consumer leaking its own
authority*, a choice it is entitled to make, whereas a return value leaks the *provider's*
authority to a consumer that asked for less. Lower severity, and guarding it would mean
wrapping arguments on the way in — the start of a real membrane. Deferred to §6.

## 5. Asterisk the `kernel.lifecycle` escalation (C)

`LifecycleFacet.get_plugin` returns the raw live instance (`_core_facet.py:145-155`),
documented as "a granted tier-2 capability is full authority by design" (`:138-139`) — the
supervisor genuinely needs raw instances to restart real plugins. This RFC does **not**
change that; it changes the *claim* so it stops silently excluding it. Wherever the
auditable-graph claim is stated, add:

> …complete who-may-invoke-what — **with one declared exception: a holder of
> `kernel.lifecycle` obtains raw plugin instances via `get_plugin` and therefore has
> ambient reach to every plugin's full surface.** This is the deliberate tier-2 escalation
> (RFC 0001 §3.2.2); enumerate `kernel.lifecycle` holders (greppable, one per manifest) and
> scrutinize exactly those.

Optional, deferred: a descriptive-by-default `get_plugin` with an explicit `raw=True` for
the supervisor's restart path, so the *common* lifecycle read is attenuated and the raw
reach is opt-in and greppable. Not required; the asterisk is the necessary part.

## 6. Non-goals and deferred (where the ceiling is)

Named explicitly, both to bound the claims above and to mark the ocap path RFC 0002 §10
already pointed at.

- **Full membrane (transitive arg + return wrapping).** The general fix for A/A′/B. Heavy,
  per-call overhead on the resolution path, and blunted by Python's lack of `harden()`.
  Stays deferred exactly where RFC 0002 §10 left it: the ocap migration, surface-by-surface,
  for *untrusted self-generated* capabilities. This RFC takes only the one-hop return slice
  (§4) because it is cheap and reuses existing parts.
- **Per-handle (caretaker) revocation.** Our revocation is registry-driven live
  re-resolution (`_capability_facet.py:65`) — per-capability, and *cleaner than the textbook
  caretaker* for hot-reload (no held switch, registry is the single source of truth). The
  one thing it can't do is revoke *one consumer's* handle while others keep theirs. No
  current consumer needs that; the caretaker pattern is the known extension point if one
  appears. Deferred (YAGNI).
- **`lockdown()` analog (freeze primordials).** The `self._Plugin__core` reflection escape
  (RFC 0001 §3.2.3) is exactly what SES's `lockdown()` closes. Closing it needs a protection
  boundary between in-process principals, declined on simplicity grounds (RFC 0001 §3.7).
  Out of scope, permanently, unless that decision reverses. Document it as the named ceiling.
- **Compartment-of-the-namespace.** SES compartments control module resolution itself (code
  *cannot* `import os`); we attenuate one object (`self.core`), not the language (RFC 0001
  §3.2.3 acknowledges a plugin can `from uxok import Core`). Going further means owning
  the import graph — against the grain, out of scope. The README framing: *"we attenuate the
  kernel handle, not the language."*

## 7. API.md / docs deltas (concrete)

1. **§2.2-equivalent claim sites** (`API.md` §12/§15, `KERNEL_ARCHITECTURE.md`, README):
   apply the §3.1 wording (*invoke* not *reach* + channel caveat) and the §5 asterisk.
2. **Event-bus reference doc + plugin how-to:** add the §3.2 payload convention.
3. **`CoreConfig`:** add `debug_payload_authority_check: bool = False` (§3.3), validated in
   `__post_init__` alongside the other flags. Default off ⇒ zero behavior change.
4. **`"sealed"` semantics note:** document that a sealed typed resolution returns a
   descriptive `PluginView` in place of a leaked live `Plugin` (§4) — behavior, not a new
   public symbol (the `PluginView` already exists).
5. No change to `get_capability`'s signature, the resolution path, `enforce_requires`,
   `requires`/`resolves`, hot-reload, or the `"open"` default.

## 8. CHANGELOG entry (draft, lands with implementation)

```markdown
### Changed
- Docs/constitution (RFC 0004): the `requires ∪ resolves` grant set is documented as the
  complete *invocation* boundary, not a reference-isolation boundary — a live reference can
  still cross a granted edge or the (deliberately ambient) event bus. Corrects the
  "complete who-can-reach-what" claim in RFC 0001 §2.2 / RFC 0002 §7. Holders of
  `kernel.lifecycle` are documented as the one ambient-reach escalation.

### Added
- Membrane-lite return guard (RFC 0004): under `capability_access="sealed"`, a typed
  resolution that returns a live `Plugin` instance now yields the descriptive `PluginView`
  instead of the raw plugin — accidental second-hop authority leaks are attenuated. `"open"`
  and `"declared"` are unchanged.
- `CoreConfig.debug_payload_authority_check` (default `False`): opt-in, shallow,
  warn-only detection of live `Plugin` references in event payloads.
```

Defaults unchanged; no item is a breaking change on its own.

## 9. Philosophy check (CLAUDE.md decision framework)

1. **Framework or product?** Framework — mechanism (attenuate a leaked handle; correct a
   claim), no policy added. ✅
2. **Adds complexity?** §3 is prose. §4 reuses the existing descriptive `PluginView` and adds
   one `isinstance` on the return path. §3.3 is one opt-in flag. No new subsystem. ✅
3. **Opt-in?** §3.3 defaults off; §4 fires only under `"sealed"` (already opt-in); §3/§5 are
   documentation. ✅
4. **Breaks existing code?** Not at defaults. Under `"sealed"`, code that *relied on* a sealed
   method returning a raw plugin breaks — but that reliance *is* the leak this closes, and
   sealed is pre-1.0 opt-in. ✅
5. **Simpler way?** The honesty fix (§3) is the simplest possible response to an unenforceable
   property — stop claiming it. The return guard reuses parts rather than building a membrane. ✅
6. **Core or plugin?** Core — the claim is the kernel's, the facets are the kernel's, the bus
   is the kernel's. The *convention* (§3.2) is guidance to plugin authors. ✅
7. **Lock-free invariant preserved?** Yes — §4's guard is synchronous (`isinstance` + a
   view construction already used by `list()`); §3.3 is shallow and off the mutation path; no
   `await` added inside any capability-state critical section (decision #12). ✅

## 10. Suggested implementation order

Each step is independently shippable and non-breaking at defaults:

1. **§3.1 + §3.2 + §5 (docs/claim correction).** Highest ROI, zero code, makes a published
   claim true. Land first; it has no consumer dependency. Run `code-auditor` against the
   amended `API.md`/`KERNEL_ARCHITECTURE.md`.
2. **§4 (membrane-lite return guard).** Small, reuses `PluginView`; gate behind `"sealed"`.
   Add tests: a sealed method returning a plugin yields a descriptive view that raises on
   `.call`; returning data is untouched.
3. **§3.3 (opt-in payload detector).** Lands with, or after, the self-coding host that
   consumes it — per the constitutional caveat, don't ship default-off mechanism ahead of a
   caller.

## 11. Open questions

- **§4 recursion depth.** One hop (proposed) vs. also scanning one level of returned
  containers (`[plugin]`, `{"p": plugin}`). Leaning one-hop: deeper scanning is membrane work
  with hot-path cost and is dodgeable anyway (§4.3); revisit only if the leak shows up
  wrapped in containers in practice.
- **§3.3 scope.** Warn-only forever, or a `strict` mode that drops/raises on a referenced
  payload? Leaning warn-only — raising changes dispatch semantics and Python can't make the
  check complete, so "strict" would be security theater.
- **§5 `get_plugin`.** Asterisk-only (proposed), or also ship the descriptive-by-default
  `get_plugin(raw=True)` split? Decide with the supervisor's needs in view; the asterisk is
  the part that can't wait.
- **Relationship to the deferred full membrane (RFC 0002 §10).** When the ocap path is taken
  for untrusted self-generated capabilities, §4's one-hop guard should generalize into it
  rather than persist as a parallel mechanism. Flag for that RFC, don't pre-build.
```

