# RFC 0007 — The constitution declares the public surface; explanation lives elsewhere

- **Status:** Draft / Proposed
- **Date:** 2026-06-25
- **Type:** RFC — constitutional form (documentation boundary) + one internal method
- **Motivated by:** Wiring the admission probe (RFC 0006) and looking for the sanctioned way to
  reach the real `Core` surfaced that `API.md` §3.2 *documents the reflection escape* —
  `self._Plugin__core_real`, a non-public, name-mangled attribute. Pulling that thread shows the
  escape is not alone: §3.2 has accumulated a block of caveats and boundary-strength commentary.
  None of it is the public contract. The constitution has been mixing **reference** (what the
  public API *is*) with **explanation** (why it is shaped this way, what its limits mean).
- **Relates to:** RFC 0001 §3.2 (the attenuated `CoreFacet`), RFC 0004 §D/§E (the invocation-vs-
  reference-isolation caveats; the data-not-handles convention), RFC 0006 (the consumer that
  surfaced this). Diátaxis reference/explanation split (the project's documentation blueprint).
- **Non-goals:** No mechanism change. No new boundary. This does **not** alter `capability_access`
  semantics, the facet tiers, the resolution path, or what any plugin can reach — in a shared
  process there is no protection boundary, and that is unchanged. It changes **what the
  constitution contains**, plus adds one internal forwarding method.

---

## 1. The principle

`docs/manifests/API.md` is the constitution: it declares the **public surface, exactly — no more,
no less.** "Public surface" is the public *contract*, not merely a list of names: each public
member's signature, types, and **observable behavior** (what it does, what it raises). A caller
depends on all of that, so all of that is constitutional.

What is **not** constitutional, and does not belong in `API.md`:

- **Non-public symbols.** Anything `_`-prefixed, name-mangled, or absent from `__all__` has no
  public contract to declare. It cannot appear in a document that is "exactly the public surface" —
  its presence is a category error, not a feature.
- **Explanation, rationale, qualification.** *Why* the surface is shaped this way, what a boundary
  "means," how strong it is, how one might get around it — all of it is the explanation layer's
  job (`docs/explanation/`, `FRAMEWORK_PHILOSOPHY.md`, a security note). A constitution declares;
  it does not editorialize, and so it needs no caveats walking back claims it never makes.

The one legitimately non-member content is the **preamble** — governance about the document itself
(the pre-1.0 breaking-change policy, "single source of truth"). That is the document's own terms,
not commentary on the surface, and it stays.

## 2. Where the constitution currently violates this

`API.md` §3.2 (the `Plugin` capability surface) mixes explanation into the reference doc in three
places:

1. **It names a non-public symbol.** Caveat 2 of the "invocation boundary" block instructs the
   reader to read `self._Plugin__core_real` (`API.md:354`). A name-mangled internal cannot be in
   the public surface by definition.
2. **It carries a pure-interpretation bullet.** "The grant set is an *invocation* boundary, not
   reference isolation" with its three numbered reasons (`API.md:344-359`) describes what the
   capability system *means* for who-can-reach-what. That is not the behavior of any public
   member; it is explanation.
3. **It mixes editorial into a behavioral bullet.** The sealed-return-guard bullet
   (`API.md:333-343`) correctly declares behavior — a typed facet *raises* `CapabilityAccessError`
   when a provider method returns a live handle; data and attenuated views pass through — but then
   editorializes: "robustness, not a boundary … one hop only … the dominant *accidental* leak, not
   a determined one." The behavior is contract; the editorial is explanation.

**How it got here:** RFC 0004 §D found the constitution *over-claiming* ("the manifest is the
complete who-can-reach-what") and corrected it — by adding caveats that walk the claim back. Right
honesty, wrong document: a declarative constitution should have stated neither the over-claim nor
its retraction. RFC 0006 then added the reflection escape in the same spot. The caveat apparatus is
accreted explanation, and the reflection escape is one entry in it.

## 3. The fix

One principle, applied in three moves. Two are documentation; one is a small method.

### 3.1 Keep only the contract in §3.2

For each public member, declare its observable behavior and stop:

- **`get_capability`** (`API.md:319-332`) — keep as is. It declares behavior: the `requires ∪
  resolves` gate, the typed-vs-untyped return, the sealed protocol-limited facet,
  `AttributeError` / `StalePluginError`. All observable contract.
- **Sealed return guard** (`API.md:333-343`) — keep the behavior: under `"sealed"`, a typed facet
  raises `CapabilityAccessError` when a provider method returns a `Plugin`/`Core`/`CoreFacet`/
  `LifecycleFacet`; data, dataclasses, primitives, ambient bus/hooks, and attenuated views pass
  through; `"open"`/`"declared"` build no facet. **Strike** the "robustness, not a boundary / one
  hop / accidental not determined" editorial — it moves to explanation (§3.2 below).
- **The "invocation boundary, not reference isolation" bullet** (`API.md:344-359`) — **strike
  entirely**, including the `_Plugin__core_real` reflection caveat. It is interpretation end to
  end; no public member's contract is lost by removing it.

The result: §3.2 reads as a flat declaration of the capability surface — what each member does —
with no caveats, no non-public names, no boundary philosophy.

### 3.2 Relocate the explanation (nothing is lost)

The struck material is true and worth saying — it just belongs in the explanation layer, beside the
data-not-handles convention RFC 0004 §E already put there:

- The **invocation-vs-reference-isolation** reasoning and the **boundary-strength** statement
  (hygiene boundary, not security; in-process, no protection boundary; the grant set is the
  *reviewable* surface for hallucinated authority, not an enforced wall) → `FRAMEWORK_PHILOSOPHY.md`
  (or a dedicated security note it links).
- The sealed-return-guard's **one-hop-robustness** nuance → the same note / the plugin-architecture
  explanation, with the other transitive-attenuation caveats.

This is a recategorization, not a reversal: RFC 0004's honesty stays stated, filed where
explanation lives.

### 3.3 The escape gets an internal name (and so stays out of the constitution)

The legitimate "reach the real `Core` from infrastructure" path gets one internal, greppable name
instead of N reinvented reflections. Add to `Plugin`:

```python
def _escape_to_core(self) -> Core:
    """INTERNAL — leave the attenuated surface, get the real ungated Core.

    Under "declared"/"sealed", ``self.core`` is an attenuated CoreFacet by design;
    this returns the real Core. For kernel infrastructure operating below the plugin
    abstraction (a host loader/supervisor/admission gate doing graph surgery the
    granted surface does not expose). Ordinary plugins want a capability, a grant, or
    an ambient facet member — not this. Single-underscore and absent from API.md on
    purpose: it is not public, so the constitution does not declare it. Under "open"
    it returns the same object as ``self.core``.
    """
    return self.__core_real
```

It forwards to the existing `__core_real` (`_base.py:268`) — no new state, no new mechanism. The
single underscore is load-bearing: it keeps the method non-public, so `tests/test_api_constitution.py`
(which checks the documented surface against the public API) neither requires nor permits an
`API.md` entry. The constitution staying silent about `_escape_to_core` is not an omission — it is
the principle: it is not public, so it is not declared. The raw `_Plugin__core_real` remains the
mechanism it forwards to; both are invisible to anyone reading only the public surface.

## 4. What this is, mechanically

Adds one internal method (`Plugin._escape_to_core`, ~3 lines + docstring) and a small test that it
returns the real `Core` under `declared`/`sealed` and `is self.core` under `"open"`. Everything
else is moving prose from `API.md` to the explanation layer. No public symbol is added; no
behavior changes; `__core_real`, `_attach_core`, `CoreFacet`, and the (non-)boundary are exactly as
before. The fold-vs-split question from earlier review **dissolves**: striking the §3.2 caveats is
not *reversing* RFC 0004, it is *recategorizing* its explanation, so there is no 0004 decision to
re-litigate — it is one coherent recategorization pass.

## 5. Alternatives considered

- **(a) Consecrate a *public*, documented `escape_to_core()`.** A public method would, correctly,
  belong in `API.md` — but documenting an escape puts a break-glass lever on the public surface,
  where the readers least equipped to judge when it is warranted will find it. Auditability needs a
  *name* (to grep), not *documentation*; a non-public name gives the audit hook without the
  advertisement. Rejected in favor of the internal `_escape_to_core`.
- **(b) A property `self._real_core`.** Reads as an ordinary attribute access; an escape should look
  like a deliberate act at the call site. A verb method is louder and greppable as one token.
- **(c) Leave §3.2 as is and only rename the escape.** Rejected: that fixes one bullet and leaves
  the rest of the explanation mislodged in the constitution — the half-measure that motivated this
  RFC.
- **(d) Actually seal the reflection path.** Out of scope and not honestly achievable in a shared
  process; the philosophy is explicit that there is no protection boundary between plugins.

## 6. The teeth (why one internal name matters)

A single canonical symbol — public or not — is what makes "rare and enumerable" (RFC 0004 §D)
checkable:

- **Greppable chokepoint.** `_escape_to_core(` is one token; a consumer repo can carry an
  **allowlist test** — the set of call sites must match a known list, each with a one-line reason.
  A new escape fails CI until removed or blessed.
- **Convergence.** `uxok-host`'s private `real_core(self)` becomes a thin alias for — or is
  replaced by — `plugin._escape_to_core()`; every consumer shares the audited symbol.
- **Future instrumentation, if ever justified.** One chokepoint is the precondition for emitting a
  hook/counter on escape later. Deferred (it has cost and cannot be made mandatory in-process), but
  this RFC establishes the seam.

## 7. Acceptance criteria

- `Plugin._escape_to_core() -> Core` exists, single-underscore, returns the real ungated `Core`
  under every mode (`is self.core` under `"open"`), with an internal docstring directing ordinary
  plugins away from it. It is **absent** from `API.md`, and `tests/test_api_constitution.py` passes
  *because* it is undocumented.
- `API.md` §3.2 contains only the capability surface's behavioral contract: no `_Plugin__core_real`
  (or any escape), no "invocation boundary, not reference isolation" bullet, no boundary-strength
  or robustness editorial. The struck content is relocated to `FRAMEWORK_PHILOSOPHY.md` / the
  relevant `docs/explanation/` notes, intact.
- RFC 0001/0004 references to `self._Plugin__core_real` as *the documented escape* are annotated
  (post-implementation note) to point at the internal `_escape_to_core`, with neither in the public
  surface.
- `CHANGELOG.md` records it: an internal `_escape_to_core()` added; §3.2 reduced to the public
  contract with explanation relocated. Same commit as the doc change (constitution policy).
- **Follow-up, tracked not blocking:** `uxok-host` migrates `real_core(self)` →
  `plugin._escape_to_core()` and adds the call-site allowlist test (§6). Lands in the host repo.

## 8. Scope note

The value is **form**: the constitution declares the public surface and nothing else; explanation
lives where explanation belongs; the escape has one internal, auditable name and no public
advertisement. The kernel's mechanism — and the honest fact that the attenuated surface is a
hygiene boundary, not a wall — are both unchanged; only *where each is written down* moves.
