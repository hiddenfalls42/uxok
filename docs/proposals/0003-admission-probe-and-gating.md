# RFC 0003 (v2) — Admission Probes and the `validate | commit` Seam

- **Status:** §4.1–4.3 carried into `0005-admission-and-attenuation-spec.md` (the finalized build
  spec) as Feature A, with the `unresolvable_resolves` advisory field **dropped** there. §4.4 (opaque
  interceptor) remains deferred. Trust `0005` for what gets built; this is the design rationale.
- **Date:** 2026-06-25
- **Supersedes:** `0003-downloaded-structural-interpreters.md` (the original draft) as the active
  0003 direction. That draft and `0003-review-kernel-boundary.md` remain in-tree as the
  discussion artifacts this proposal is the synthesis of. The "downloaded interpreter" idea is
  not discarded — it is demoted to an optional, opaque, deferred layer (§4.4) once the kernel
  exposes the seam it would have plugged into.
- **Affects:** `docs/manifests/API.md` (constitutional), `core/_core.py` (registration path),
  `core/_capability_system.py`, `CHANGELOG.md`. **Not** `PluginMetadata` (deliberately — see §7).
- **Builds on:** RFC 0001 (Secure Capabilities), RFC 0002 (Resolution Grants).
- **Type:** Constitutional API change. Additive, default-equivalent. The accepted version lands
  its `API.md` / `CHANGELOG.md` edits in the same commit as the implementation.

---

## 1. Summary

The original 0003 draft proposed letting the host download a *structural interpreter* the kernel
runs at registration — an XN/UDF borrow. Review (`0003-review-kernel-boundary.md`) found that of
everything it bundled, exactly one thing was irreducibly kernel: **a pre-commit point at the
boundary every registration crosses**. Everything with structural *meaning*
(`StructureView`/`Diagnostic`/`Fix`, the derivation, the repair tiers) was host vocabulary that
the kernel must not learn (mechanism-not-policy, the draft's own §9.3 one level up). And the one
genuinely new kernel *power* it asked for — silently rewriting a plugin's frozen, author-owned
`requires`/`resolves` from a derived footprint — broke an invariant and inverted RFC 0002.

This v2 keeps the irreducible kernel atom and drops the rest. It proposes:

1. **Split `validate` from `commit`** inside the registration path — the seam already exists
   implicitly (both validation methods "check up front, then mutate"); this makes it a real
   boundary.
2. **An admission probe** — `Core.check_plugin(candidate)`: run the kernel's *real* pre-commit
   validation against the *live* graph, **commit nothing, start nothing, fire nothing**, and
   return the kernel's own structured faults. This is the side-effect-free check a
   write→check→repair loop needs for *structural* admission (id / provides / contract /
   missing-requires). The nastier *authority-completeness* class — a body that resolves more than
   its manifest declares — is deliberately **not** caught here; it lands with §4.4 or the subcore
   (§4.2 scope boundary). "Admitted" means "fits the graph now," not "its authority is complete."
3. **Atomic admission folded into `register_plugin`** — the §4.1 admission check runs inside the
   lifecycle lock immediately before commit, on *every* registration, closing the structural
   TOCTOU gap that any "test earlier, register later" flow (including the prototype's subcore path)
   inherently opens. There is no second commit door and nothing to code for: admission *is* part of
   `register_plugin`, so any caller that probed (or subcore-tested) earlier gets the at-commit
   re-check for free.
4. **(Optional, deferred — §4.4)** a single opaque pre-commit interceptor slot, blob-in/blob-out,
   so a host *can* later inject the structural-interpreter logic without the kernel learning any
   of its types. Gated behind an actual consumer.

It is explicitly **a complement to the prototype's subcore behavioral testing, not a replacement
for it** (§5). Subcore answers "does it *work* against a constructed environment"; the probe
answers "does it *fit the live graph*, and does it still fit at the instant I commit." Different
axes; both wanted.

## 2. Motivation

### 2.1 The write→check→repair loop needs a *pure* check

A self-coding runtime's expected output distribution is *failing* candidates — malformed,
under-declared, conflicting. Failure is the common path, not the error path. Today the only way
to learn whether a candidate registers is to **attempt it**: `_register_plugin_now`
(`_core.py:267`) mutates the registry, attaches the core, runs `start()`, fires the
`plugin.registered` hook (`:296`), and on failure runs the rollback — publishing
`core.plugin_error` (`:313`) and draining resources (`:327`). Using the live graph as a
scratchpad for a *question* is expensive, stateful, observable by other plugins, and racy.

The loop wants to ask "will this fit?" cheaply and repeatedly, with **no commitment and no
side effects**, and get back a result it can route to repair. That is a primitive only the
kernel can offer, because only the kernel can validate against the authoritative live graph
using the *real* commit-path logic (a host reimplementation would drift from the enforcer — the
classic "the check and the enforcement disagree" bug).

### 2.2 What already exists, and what's missing

The kernel already computes every fault we want to surface — it just only does so mid-commit:

- **Load-order / missing providers** — `validate_requirements` (`_capability_system.py:206`)
  raises `MissingCapabilityError`, which already lists missing + available (`errors.py:106`).
- **Provides collisions** — `register_capabilities` pre-flights collisions under
  `error_on_conflict` (`_capability_system.py:261-265`) before mutating.
- **Protocol-contract validation** — same method, `:253-255`.
- **Id collision** — `_register_plugin_now` (`_core.py:274`).

Both methods are already structured "check up front, then mutate." What's missing is a way to
run the *check* halves **without** the *mutate* halves, against the live graph, returning the
faults instead of raising mid-registration. That is the entire kernel ask.

### 2.3 The prototype already solved a *different* half

In the prototype, side-effect-free validation was obtained by **spawning a subcore with injected
dependencies** and registering the candidate into it. That is strictly more than load
confirmation — it executes the candidate, so it *tests behavior*. We are **not** scrapping it.
But it answers a different question (§5): the subcore validates against an environment *you
constructed*, not the live graph the candidate must actually enter. The probe is the missing
live-graph admission check, not a replacement for behavioral testing.

## 3. What the kernel uniquely owns

The `validate | commit` seam — the gap between `validate_requirements` (`_core.py:285`) and the
commit sequence (`registry.add` `:287` → `register_capabilities` `:293` → `_attach_core_to`
`:294` → `start()` `:295` → `plugin.registered` `:296`). Only the kernel can:

- run validation against the **authoritative live graph** (host code only has a stale view);
- guarantee a check uses the **same logic** as the enforcer (no drift);
- hold the **lifecycle lock** so a check and a commit can be made atomic (§4.3);
- do all of the above **without** the commit side effects, because it owns where those fire.

Nothing structural is in this list. The kernel reports *its own* facts; it interprets nothing.

## 4. Proposal

### 4.1 Split validate from commit

Factor the pre-commit validation out of `_register_plugin_now` into a pure routine that takes a
candidate and the live graph and returns faults (raising nothing). The real register path calls
it and, on `ok`, proceeds to commit exactly as today; the probe calls it and returns. The
mutation halves (`_capabilities[...] = ...`, `registry.add`, `start()`) stay where they are. This
is a refactor, not new logic — default behavior is byte-identical.

### 4.2 The admission probe

```python
class AdmissionResult:
    ok: bool                               # admission verdict (advisory fields below never flip it)
    missing_requires: frozenset[str]       # load-order `requires` with no live provider — the
                                           # reserved grants kernel.lifecycle/kernel.dispatch are
                                           # exempt and never appear here
    id_conflict: bool                      # candidate id already registered
    provides_conflicts: frozenset[str]     # `provides` colliding with the live graph under the
                                           # active capability_collision policy — tag-discriminated
                                           # multi-providers (5x "index", 7x "codec", ...) are NOT
                                           # conflicts; a same-name claim without a distinguishing
                                           # tag is
    contract_failures: frozenset[str]      # typed-capability protocol-contract violations
    unresolvable_resolves: frozenset[str]  # ADVISORY ONLY, low-signal BY CONSTRUCTION — declared
                                           # `resolves` with no current live provider. This is the
                                           # *legitimate* RFC 0002 case (lazy/cyclic/hot-loaded), so
                                           # it fires on correct code as readily as on typos; never
                                           # flips `ok`. Weak typo-suspicion, not a fault.
    interceptor_diagnostics: object | None # opaque; present only if §4.4 ran. Kernel never reads.

async def check_plugin(self, candidate: PluginProtocol) -> AdmissionResult: ...
```

Properties:

- **Side-effect-free.** No registry mutation, no `_attach_core_to`, no `start()`, no
  `plugin.registered` hook, no `core.plugin_error` event, no `drain_plugin_resources` rollback,
  no `_collection_service.invalidate()`. Nothing observes the candidate.
- **Kernel-known faults only.** Every field is something the kernel already computes during a
  real registration. No `Diagnostic`, no `Fix`, no host taxonomy.
- **Against the live graph.** It answers about production reality at call time — which is exactly
  what the subcore's injected environment cannot.
- **Reuses the real path** (§4.1), so the probe can never disagree with the enforcer.

**Scope boundary — admission, not runtime authority.** The probe certifies that the *declared*
manifest *fits* the live graph; it does **not** certify that the manifest is *complete* for what
the body will resolve at runtime. Under RFC 0002, `resolves` is a runtime grant deliberately
**not** validated at registration — so a candidate that resolves `"journal"` at runtime but
omitted it from `resolves` admits cleanly here and fails later as a `CapabilityAccessError` on
whatever path first executes it (a branch an LLM-authored body may never exercise under test).
Catching that *under-declared-resolves* class needs either the subcore to hit the path (§5) or
structural derivation of the footprint — exactly the §4.4 interpreter's job
(`derive_requires`/`derive_resolves`). **"Admitted" means "fits the graph now," not "its authority
is complete."** (`unresolvable_resolves` is a weak typo-suspicion signal, not a completeness check —
and a noisy one: RFC 0002 *legitimizes* provider-absent-at-registration, so it fires on correct
lazy/cyclic/hot-loaded code, not just on typos. The repair loop must weight it accordingly.)

`MissingCapabilityError` (and friends) stay the raising form for the real path; the probe is the
non-raising form of the same checks. The agent reads `AdmissionResult` and **re-authors its own
manifest** (e.g. adds a missing `resolves` per RFC 0002) — the kernel does **not** rewrite it
(§7).

### 4.3 Atomic admission inside `register_plugin` (the TOCTOU guard)

Any "validate/test now, register later" flow opens a gap: the graph can drift between the check
and the commit (another plugin registers a conflicting `provides`, a depended-on provider is
unregistered). This applies to the probe (§4.2) *and* to the prototype's subcore flow (§5) — a
subcore's green result is stale the instant it returns.

The kernel closes the **structural** half of this gap because it holds the lifecycle lock (the
reentrant lock serializing register/load/unregister/swap). Rather than add a second commit door,
**`register_plugin` itself runs §4.1 admission inside that lock, immediately before commit** — it
already validates there; we make that the named, guaranteed contract. The point is that this is
**not something callers code for**: any caller that probed (or subcore-tested) earlier and then
calls the ordinary `register_plugin` gets the at-commit re-admission automatically. `check_plugin`
(§4.2) is therefore purely an *advisory pre-flight* — a cheap "would this fit?" taken **outside**
the lock, never the commit path. One admission routine (§4.1), two entries: the advisory
`check_plugin` and the authoritative `register_plugin`. There is deliberately **no** separate
"atomic register" API to pick wrong.

*Implementer note:* `check_plugin` must read the graph **synchronously — no `await` between
snapshot and verdict** — which is what makes its unlocked read coherent under decision #12
(cooperative-asyncio critical sections) and why it needs no lock. It must **not** acquire the
lifecycle lock; doing so would serialize the advisory pre-flight against live registration for no
benefit. Its result is advisory *precisely because* it is unlocked. (If §4.4's interceptor ever
runs inside it and awaits, the snapshot can tear — acceptable for an advisory pre-flight, never for
the authoritative path, which is why that path stays in `register_plugin`.)

This does **not** close the *behavioral* half (§6) — only that the candidate still structurally
fits at the commit instant.

### 4.4 Optional opaque pre-commit interceptor (deferred)

The salvaged interpreter idea, reduced to what the boundary review said is the only kernel-legal
form: a single registered callback run at §4.1, **blob in, blob out**.

```python
class Candidate:                     # kernel-known facts + one opaque blob
    name: str
    provides: frozenset[str]
    requires: frozenset[str]
    resolves: frozenset[str]
    structure: object                # opaque; the host's payload; kernel never inspects

class InterceptResult:
    ok: bool
    diagnostics: object | None       # opaque; attached verbatim to AdmissionResult / the error

Interceptor = Callable[[Candidate], InterceptResult]
```

The kernel gates *registering* an interceptor (a reserved grant, mirroring `kernel.lifecycle`/
`kernel.dispatch`), runs it at the seam, and folds `ok`/`diagnostics` into the admission result —
treating `diagnostics` as an opaque blob it carries, never a typed tree it understands. The
kernel defines **no** `StructureView` shape, **no** `Diagnostic`/`Fix` vocabulary, and performs
**no** footprint derivation or metadata rewrite. The host owns all of that.

**This is deferred, but *queued* — not speculative.** The consumer already exists: the host runs
`derive_requires` (footprint-from-structure) on its `flow_verifier` path today. §4.4's distinct
value is running that derivation **uniformly at the chokepoint every registration path crosses** —
the watch loop hot-loading a hand-written cap, a direct `register_plugin`, a test — i.e. exactly
the paths that bypass `flow_verifier` and therefore never get their manifest checked for
completeness (the §4.2 scope boundary). That is also where the *under-declared-resolves* gap gets
closed for non-`flow_verifier` registrations. It still ships only when the host companion wires an
interpreter into the slot; building the slot before that wiring exists is the complexity creep
§7/the constitution warns against. §4.2–4.3 stand alone with in-repo consumers today; §4.4 lands
with the host-companion derive phase.

## 5. Relationship to subcore behavioral testing

Keep the subcore. It and the probe are different axes, and the prototype's instinct is right:

| | Subcore + injected deps | Admission probe (§4.2) |
|---|---|---|
| Question | *Does it **work**?* | *Does it **fit the live graph**?* |
| Fidelity | Full — runs `start()` + plugin logic | Structural — no execution |
| Environment | **Constructed** (you inject deps/fakes) | **Live** (real providers, real conflicts) |
| Cost | Core construction + injection | Cheap, pure |
| Drift risk | None (runs the *real* path in a throwaway core) | None (reuses the real validate logic, §4.1) |
| Blind to | Live-graph conflicts not in the injected set | Behavior |

The subcore is, honestly, the *more drift-proof* way to get behavioral side-effect-freedom — it
runs the real registration code in a disposable core, so there is nothing to keep in sync. The
probe must **not** try to be "the behavioral dry-run"; the subcore owns that better. The probe's
irreducible niche is the **live-graph admission bracket**:

```
0. construct      → instantiate + host compose_check:  "does it build & type-compose?"  reject on build
1. check_plugin   → cheap, pure, vs LIVE graph:    "could this ever fit reality?"   reject early
2. subcore        → expensive, behavioral, INJECTED: "does it actually work?"        reject on behavior
3. register_plugin→ re-admit vs LIVE graph + commit: "does it STILL fit, now?"       commit (atomic, §4.3)
```

Step 0 is host-side: construction and typed composition surface before the probe, so `check_plugin`
is not a build check — and note it takes a *constructed* instance, which §6 then discards in favor
of a fresh one for the real `register_plugin`. Step 1 is the cheap gate before the expensive one —
don't spin up a subcore to behaviorally test a candidate with a production id collision. Step 3 is
the **ordinary** `register_plugin`: the atomic at-commit guard (§4.3) is built in, not a separate
call.

## 6. The fresh-register gap this does and does not close

After a subcore test passes, the candidate must enter the live core by **fresh registration** —
re-instantiated, not promoted. This is forced, not optional: plugin instances are one-shot (the
subcore instance already ran `start()` and bound subscriptions/hooks to the *subcore's*
infrastructure), and the tested instance may hold references to injected fakes / a `self.core`
pointing at the subcore. Promoting it would ship an object wired to the test world.

But fresh register **relocates** the gap rather than closing it: you validated instance *A*
against fakes and you ship a fresh instance *B* whose first real `start()` runs **in production,
against the live graph, never previously exercised there**. For LLM-authored bodies — where
construction/`start()` may be environment-sensitive — *B* is not guaranteed to be the thing the
subcore blessed.

What this RFC closes, and what it doesn't:

- **Closes (cheaply):** the *structural* half — §4.3 guarantees *B* still admits against the live
  graph at the commit instant.
- **Does not close:** the *behavioral* residue — whether *B*'s first live `start()` behaves as
  *A*'s subcore `start()` did. The honest mitigations are (a) running the subcore against a
  **faithful mirror of the live graph** rather than hand-injected fakes for self-coded candidates
  (heavier; mirror goes stale on snapshot), or (b) a **probationary register** — fresh instance,
  real graph, real start, quarantined from live traffic until healthy. Both are out of scope, and
  (b) is **deliberately kept out of the kernel**: it is supervision policy, which lives in plugins
  (the supervisor), not core.

## 7. What we deliberately do *not* put in the kernel

- **No frozen-metadata rewrite.** The original §5 (kernel commits a *derived* footprint, ignoring
  the author's declaration) is dropped. `PluginMetadata` stays frozen and author-owned
  (`test_plugin_metadata_immutability`), and RFC 0002's "the author declares; the manifest is the
  only door" stays intact. The agent re-authors its manifest from `AdmissionResult`; if a host
  ever wants kernel-side adoption, it is a separate, evidence-gated proposal — and largely
  substitutable by probe-faults + re-register anyway.
- **No `StructureView`/`Diagnostic`/`Fix` vocabulary in the kernel.** §4.4 carries blobs. The
  kernel never enumerates a `kind` or renders type-error text.
- **No watchdog-as-fail-safe claim.** The original §4 implied a time bound makes a downloaded
  interpreter safe. An in-process watchdog cannot interrupt sync CPU-bound Python; a hanging
  interceptor hangs registration regardless. If §4.4 ships, the honest contract is *either*
  "interceptors must be `async`/cooperative and we apply an `asyncio` timeout" *or* "we document
  purity and accept a hang as a host defect (fail-stop)." We do not pretend to a fail-safe.
- **No per-call (resolution-time) interpreters.** The lock-free resolution path (RFC 0001
  decision #12) is untouched. Schema-checking every hook payload remains a separate proposal
  because of the hot-path cost.

## 8. Security analysis — robustness, not a boundary

Unchanged from the original draft, and it bounds every claim here. The kernel shares an address
space with every plugin; there is no protection boundary (RFC 0001's explicit non-goal). A
*malicious* plugin reaches around the probe, the interceptor, and the lifecycle lock exactly as
it reaches around the RFC 0002 gate (`_Plugin__core_real`). This is a **correctness/robustness**
mechanism against the dominant real risk in self-coding — **bugs and model hallucination** — by
making admission uniform and checkable at the one boundary every extension crosses. It does not
defend against an adversarial plugin. Net change vs. status quo: a question you could only answer
by mutating the graph becomes a pure query, and "still fits at commit" becomes guaranteed rather
than hoped.

## 9. Performance

- The probe runs the same validation a real registration already runs; it just skips the commit.
  It is *cheaper* than today's "register-then-rollback-to-find-out," not an added cost.
- The **resolution path is untouched**; the lock-free invariant holds.
- §4.3 adds one re-validation inside the lifecycle lock at commit. The real path already
  re-validates; the cost is naming it, not new work.
- §4.4 (if it ships) costs interceptor time at registration only, host-controlled, off the hot
  path.

## 10. Backward compatibility

- `check_plugin` is purely additive; no existing call site changes.
- §4.1 is a refactor with byte-identical default behavior; the existing suite is the regression
  gate.
- With no interceptor registered, §4.4 is inert and registration behaves exactly as RFC
  0001/0002 today.

## 11. Migration plan

1. **Kernel:** split validate from commit (§4.1); add `check_plugin` + `AdmissionResult` (§4.2);
   fold the at-commit admission into `register_plugin` under the lifecycle lock (§4.3) — **no new
   commit API**. Update `API.md` + `CHANGELOG.md` in the same commit. In-repo consumers land with
   it: the supervisor probes before a restart, and tests assert "this would fail" without the
   register/rollback dance.
2. **Deferred (kernel, behind a consumer):** the opaque interceptor slot (§4.4) — only when the
   host companion has an interpreter to register.
3. **Host (companion repo):** keep the subcore behavioral test; insert `check_plugin` as the
   cheap pre-flight (step 1) and rely on the ordinary `register_plugin` for the atomic commit
   (step 3 — the guard is built in); route `AdmissionResult` into the self-coding repair loop.
   If/when §4.4 ships, register `compose_check`/`derive_requires` as an interceptor and structure
   its diagnostics there — all host vocabulary, host-side.
4. **Measure:** registrations attempted vs. graph mutations incurred (should drop toward zero for
   failed candidates), and — once the host loop consumes it — LLM round-trips per successful
   self-coded capability.

## 12. Open questions

- **Probe staleness contract — *resolved*.** `check_plugin` is explicitly **advisory/best-effort**:
  it does not hold the lifecycle lock, so its result is stale the instant the graph moves. The
  authoritative atomic check-then-commit lives **inside `register_plugin`** (§4.3); there is no
  separate "atomic register" API. One advisory door, one committing door — no wrong one to pick.
  Document the staleness caveat on `check_plugin` and steer callers to `register_plugin` for the
  guarantee.
- **Interceptor registration authority (§4.4).** Reserved `kernel.interpreter` grant, a
  `CoreConfig` slot, or both? Whatever it is, it must be tightly held — an interceptor shapes what
  may enter the graph. Decide *with* the consumer, not before.
- **Mirror-vs-fakes for self-coded subcore tests (§6).** Worth a kernel-assisted read-only
  live-graph snapshot to make subcore tests faithful, or left entirely to the host? Leaning: host,
  unless a snapshot proves to need kernel cooperation to be consistent.
- **`AdmissionResult` surface.** Keep the *reject* faults to what the real path already checks —
  resist inventing new ones (tags, dependency cycles belong to host policy or a later RFC). The one
  sanctioned *advisory* addition is `unresolvable_resolves` (§4.2): declared `resolves` with no
  current live provider — a **low-signal** typo-suspicion hint that **never** flips `ok`. Its
  false-positive rate is *intrinsic*: RFC 0002 legitimizes exactly this condition
  (lazy/cyclic/hot-loaded), so it fires on correct code, and the repair loop must weight it weakly
  or it will nag. It is borderline whether it earns a slot at all — kept only as an opt-in hint,
  never a fault. And the reserved grants (`kernel.lifecycle`/`kernel.dispatch`) must never surface
  in `missing_requires`.

## 13. Lineage note — what changed from the draft

| Original draft (structural interpreters) | This v2 |
|---|---|
| Kernel runs a downloaded interpreter that *describes/derives/explains* | Kernel exposes the `validate \| commit` seam; the interpreter becomes an optional, opaque, deferred §4.4 |
| Kernel learns `StructureView`/`Diagnostic`/`Fix` | Kernel carries blobs; defines none of them |
| Kernel rewrites frozen metadata from a derived footprint | Dropped; agent re-authors its manifest from probe faults (RFC 0002 invariant kept) |
| Watchdog presented as a fail-safe | Honest: in-process Python can't bound sync CPU; fail-stop or cooperative-async, no pretense |
| Headline value = derive-don't-trust | Headline value = a *pure* admission check + an *atomic* commit guard, with in-repo consumers today |
| Silent on the prototype's subcore | Explicitly complements it — different axes; the probe is the live-graph bracket around behavioral testing |

The xok borrow survives where it is faithful (the kernel runs host-supplied interpretation at a
boundary it controls and keeps the decision itself — §4.4), and is dropped where our model can't
honor it (no verified determinism, no protection boundary, no authority to forge a manifest). The
genuinely valuable half turned out not to be the exotic one: it is making "will this fit?" a pure
question and "does it still fit?" an atomic guarantee — the two things only the kernel can offer,
and the two things the write→check→repair loop actually hits every iteration.
