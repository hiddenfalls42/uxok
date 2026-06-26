# RFC 0003 — Downloaded Structural Interpreters

- **Status:** Superseded by `0003-admission-probe-and-gating.md` (v2). Decision record in §14.
- **Date:** 2026-06-25
- **Affects:** `docs/manifests/API.md` (constitutional), `docs/manifests/KERNEL_ARCHITECTURE.md`,
  the registration path (`core/_core.py`, `core/_capability_system.py`), `PluginMetadata`,
  a new kernel registration point, `CHANGELOG.md`
- **Builds on:** RFC 0001 (Secure Capabilities), RFC 0002 (Resolution Grants)
- **Type:** Constitutional API change introducing a new opt-in kernel mechanism. The accepted
  version lands its `API.md` / `CHANGELOG.md` edits in the same commit as the implementation;
  this document is the discussion artifact that precedes that edit.

---

## 1. Summary

The kernel manages an opaque plugin graph. It is **structurally blind**: it sees capability
names, hooks, and `requires`/`resolves`/`provides` as flat strings, but it knows nothing of
the host's *structural algebra* — the typed-port contracts (`consumes`/`produces`), flow
composition, schemas, or capability ontology that the host (the libOS analog) layers on top.
Because of that blindness, the kernel cannot enforce any **structural invariant** about its
own graph; it trusts host-layer gate code (`flow_verifier`, `compose_check`) to have checked.

For an ordinary app that is fine. For a **self-modifying runtime that writes its own
capabilities at runtime** it is the central risk: the thing generating the structure is a
language model, the gate that checks it is ordinary (bypassable, fallible) host code, and a
malformed or authority-overclaiming extension that slips through corrupts the live graph.

This RFC borrows the MIT exokernel's **XN/UDF** mechanism — *let the kernel protect a layout
it does not understand by running a downloaded, deterministic, authority-free interpreter
over it* — and applies it to plugin structure. A host registers a **structural interpreter**;
the kernel invokes it at the registration chokepoint to do three things, all the same act of
*understanding what the kernel cannot*:

1. **Describe** — interpret the candidate's opaque structure (its `ports`/steps/schema) so the
   kernel can enforce a host-defined invariant (e.g. "this flow type-composes").
2. **Derive** — compute the candidate's true authority/dependency footprint from its
   *structure*, so the kernel need not trust a **self-declared** (LLM-authored) manifest.
3. **Explain** — when the candidate is rejected, return a **structured, tiered repair
   diagnostic** that a coding agent (or a deterministic auto-patcher) consumes directly.

It is opt-in (no interpreter registered ⇒ today's behavior exactly), confined to the
registration path (not the resolution hot path), and — honestly — a **robustness mechanism,
not a protection boundary** (§7). It is the natural completion of the exokernel borrow that
RFC 0001/0002 began.

## 2. Motivation

### 2.1 The exokernel idea, revived for agentic coding

xok's XN stores data at block granularity while **knowing nothing about any file system's
on-disk layout**. A libFS downloads a **UDF** — an untrusted, *deterministic*, *bounded*
function that, given a metadata block, returns the blocks it points to and their ownership.
The kernel runs the UDF to interpret a structure it does not understand, then makes the
protection decision itself. The principle is a separation few systems make:

> **Untrusted, app-specific *interpretation* of a structure is split from trusted, generic
> *enforcement* of policy over it.** The downloaded interpreter *describes*; the kernel
> *decides*. The interpreter is deterministic, bounded, and carries no authority.

In 1995 the "untrusted downloaded code" was a performance play (let apps interpret their own
disk/network layouts in-kernel). Here the untrusted downloaded code's *subject* is the
**agent's own output**: the kernel borrows an interpreter for the host's type system so it can
make guarantees about capabilities the agent **wrote at runtime**. The exokernel's discipline
— mechanism in the kernel, interpretation downloaded, kernel keeps the decision — turns out to
be exactly the shape a self-coding runtime needs to stay safe from itself.

### 2.2 What the kernel is blind to today

The reference host builds, entirely above the kernel:

- **Typed-port contracts** — `TypedCapability.ports`, each a `Hook(consumes=…, produces=…)`.
- **Flow composition** — `TypedFlow` + `compose_check`: does a flow type-compose against the
  live ports? The kernel cannot answer this; only host code can.
- **Authority footprint derivation** — `flow_build.derive_requires` already computes a flow's
  real dependencies from its steps, *because the host explicitly does not trust the
  generator's declaration* (`flow_verifier`: "never trust the model for dependencies").

All of this is host policy the kernel cannot see, so the kernel cannot hold any invariant over
it. The registration chokepoint — the one place every capability must pass — enforces only
flat `requires` presence (RFC 0001/0002).

### 2.3 Why a kernel-held invariant beats host policy *here*

For most invariants, host policy is enough. Two properties make this one worth a kernel
mechanism:

- **The agent writes the structure at runtime.** A malformed/overclaiming capability is not a
  rare bug; it is the expected output distribution of a language model. The defense must sit at
  the boundary every extension crosses, not in one of several host code paths that might
  register a plugin (`run_spec`, the watch loop, a test, a direct `register_plugin`).
- **The declaration cannot be trusted.** A self-coded capability's `requires`/`resolves` is
  authored by the same model that wrote the (possibly wrong) body. The only trustworthy
  footprint is one **derived from the structure**, which is precisely the xok UDF move:
  determine ownership by interpreting the layout, not by believing the claim.

## 3. Proposal

### 3.1 A structural interpreter

A **structural interpreter** is a deterministic, authority-free callable the host registers
with the kernel. The kernel invokes it at plugin registration with a **read-only structure
view** of the candidate and obtains a **verdict**.

```python
# Illustrative shapes (final names land in API.md).

class StructureView:                 # read-only; opaque to the kernel, meaningful to the interp
    name: str
    provides: frozenset[str]
    declared_requires: frozenset[str]
    declared_resolves: frozenset[str]
    structure: Mapping[str, Any]     # the host's opaque payload: ports / steps / schema / kind

class Diagnostic:
    kind: str                        # machine-routable, e.g. "produces_mismatch"
    severity: Literal["reject", "warn"]
    locus: Mapping[str, Any]         # capability / hook / port / step
    detail: Mapping[str, Any]        # declared vs derived/returned, etc.
    fix: Fix | None                  # see §6
    message: str                     # rendered human-readable

class Verdict:
    ok: bool
    derived_requires: frozenset[str] | None   # footprint computed from structure (§5)
    derived_resolves: frozenset[str] | None
    diagnostics: list[Diagnostic]

Interpreter = Callable[[StructureView], Verdict]   # PURE. deterministic. no I/O. no core access.
```

Registration of an interpreter is itself a kernel operation (a reserved
`kernel.interpreter` grant, or a `CoreConfig`/`register_interpreter` entry — bikeshed in §12),
available only to suitably-granted host infrastructure.

### 3.2 What the kernel does with the verdict

At plugin registration, **after** building metadata and **before** committing the plugin to
the graph, the kernel runs every registered interpreter over the candidate's `StructureView`:

- **`ok == False`** → the registration is rejected. The kernel raises a registration error that
  **carries the structured `diagnostics`** (envelope in §6). Nothing enters the graph.
- **`ok == True` with `derived_*`** → per the adoption policy (§5), the kernel may **replace or
  augment** the candidate's `requires`/`resolves` with the derived footprint before committing
  — so the live metadata reflects what the structure *actually* uses, not what the author
  claimed.
- **`warn` diagnostics** → surfaced (and logged) but non-fatal.

The interpreter only ever **describes and proposes**; the kernel makes the commit/reject
decision and owns the resulting metadata. (xok invariant preserved: the UDF cannot act.)

### 3.3 Confined to registration

Interpreters run at **registration only** — a rare, off-hot-path event — not on every
`get_capability`. The lock-free resolution path (RFC 0001 decision #12) is untouched. Per-call
structural enforcement (e.g. schema-checking every hook payload) is a *possible* later
extension and is explicitly out of scope (§9.2).

## 4. The interpreter is pure (and why that is load-bearing twice)

xok's UDFs are safe because a restricted language/loader **verifies** termination and
memory-safety. In our in-process Python world we **cannot verify** that; we substitute
discipline plus a watchdog:

- **Contract:** an interpreter must be a pure function of its `StructureView` — deterministic,
  no I/O, no clock/RNG, no `core` access, bounded time.
- **Watchdog:** the kernel runs it under a time bound; an interpreter that exceeds it is treated
  as `reject` with a `kind="interpreter_timeout"` diagnostic (fail-safe).
- **Determinism cache:** the kernel keys verdicts by a hash of the `StructureView`. A cache hit
  that disagrees with a fresh run flags non-determinism (`kind="interpreter_nondeterministic"`)
  and is treated as a defect in the interpreter, not the candidate.

Determinism does **double duty**, which is the elegant part: it is required for the *safety*
invariant (a non-deterministic gate is no gate), **and** for the *repair* signal (non-
deterministic diagnostics send the agent — or the auto-patcher — chasing ghosts). One
constraint, both payoffs. We should state plainly that this is *disciplined*, not *verified*:
weaker than xok, and the most likely thing to bite.

## 5. Derived footprint — don't trust the generator

The highest-value use, and the closest xok parallel (UDF derives ownership; our interpreter
derives authority). The host already does this in user space (`derive_requires`); this promotes
it to a kernel-held invariant.

Adoption policy (per interpreter, or per candidate **origin**):

- **Authoritative** (recommended for *self-coded* capabilities): the kernel commits the
  **derived** `requires`/`resolves`, ignoring the author's declaration. A synthesized capability
  therefore *cannot* register claiming less authority than its structure uses, nor grant a child
  more than is derivable — the manifest cannot lie.
- **Advisory** (recommended for *hand-written*, trusted capabilities): the kernel keeps the
  declared footprint and emits a `warn` diagnostic on divergence (a lint).

This is what makes RFC 0002 trustworthy for code the agent wrote itself: 0002 says *authority is
declared*; 0003 says *for generated code, the declaration is **derived from structure by the
kernel**, not asserted by the author.*

## 6. Errors as a repair protocol

Because the interpreter understands the structure, it is the only component that can explain a
structure-level failure — and because it is host code, the explanation can be **structured**,
not prose. The kernel provides the **channel** (a registration error / `load_failed` payload
that carries `diagnostics`); the interpreter provides the **content**. (Mechanism in the kernel,
policy downloaded — do **not** let the kernel hardcode type-error text.)

A `Fix` is tiered by confidence, and this tiering is the point:

```python
class Fix:
    confidence: Literal["derived", "speculative"]
    patch: Mapping[str, Any] | None    # a concrete, applyable change (derived only)
    hint: str | None                   # natural-language guidance (speculative)
```

- **`derived` + `patch`** → ground truth. The host MAY apply it and re-attempt with **no LLM
  round-trip**. (E.g. "body resolves `journal`, declared `resolves={}`" → patch adds `journal`.)
- **`speculative` + `hint`** → a guess for the coding agent; surfaced via `loader.load_failed`.
  Never dress speculation as derivation, or auto-repair amplifies hallucination instead of
  cutting it.

The resulting repair tiers, cheapest first:

1. **Deterministic auto-patch** — interpreter derived the answer; no model call.
2. **Structured agent guidance** — typed diagnostic steers the model to the exact locus.
3. **Generic error** — today's fallback, all the kernel can do alone.

In a self-coding loop the dominant cost is LLM round-trips; collapsing an entire failure class
(under-declared authority, missing deps, wrong derived footprint) from tier 3 to tier 1 is the
concrete payoff. Worked example — one structural pass, two faults, only one needs the agent:

```python
class Summarize(TypedCapability):
    provides = "summarize"
    ports = {"summarize": Hook(consumes={"text": str}, produces={"summary": str})}
    requires = {}                                    # forgot
    async def _go(self, view, **kw):
        j = await self.core.get_capability("journal") # derived: resolves needs "journal"  → tier 1
        return {"digest": ...}                         # contract promised "summary"        → tier 2
```

Carry **both** a structured payload (for the agent / auto-patcher) and a rendered `message`
(for a human debugging the host). Optimize for the machine; keep the human readable.

## 7. Security analysis — robustness, not a boundary

Stated plainly, because it bounds every claim above:

- Our "kernel" shares an address space with every plugin; there is **no protection boundary**
  (RFC 0001's explicit non-goal). A *malicious* plugin reaches around any interpreter exactly as
  it reaches around the RFC 0002 gate (`_Plugin__core_real`). A kernel-derived footprint, a
  type-composition gate, a schema check — all are **bypassable by hostile in-process code**.
- Therefore this is a **correctness/robustness** mechanism, not a **security** one. It defends
  against the dominant real risk in self-coding — **bugs and model hallucination** — by making
  invariants kernel-held and uniform at the one chokepoint. It does **not** defend against an
  adversarial plugin. The RFC must not overstate this; closing it requires the ocap/membrane
  path **and** an actual isolation boundary (subinterpreters/processes), both declined so far on
  simplicity grounds.
- Net change vs. status quo: invariants move from "hopefully some host gate ran" to "the kernel
  ran the host's interpreter at the boundary every extension crosses." That is a real, auditable
  robustness gain, correctly scoped.

## 8. Performance

- Interpreters run at **registration** (rare; hot-reload is the busiest case) under a time bound,
  with verdicts cached by structure-hash — so a re-register of unchanged structure is free.
- The **resolution path is untouched.** No per-`get_capability` cost; the lock-free invariant
  holds.
- Cost is bounded by interpreter complexity (host-controlled) + the watchdog ceiling. A path_
  ological interpreter degrades registration latency only and fails safe via the timeout.

## 9. Alternatives considered

### 9.1 Keep it host policy (status quo)
Works, and is where the host is today (`flow_verifier`/`compose_check`). The gap it leaves:
the invariant is not kernel-held, so a buggy or bypassed host gate admits bad structure, and
errors are not produced uniformly at the boundary. RFC 0003 promotes the *same host logic* to a
kernel-invoked interpreter — little new code, a uniform chokepoint, and the derive/explain tiers.

### 9.2 Per-call (resolution-time) interpreters / schema enforcement
The same mechanism applied to every hook payload. Higher payoff in theory, but it lands on the
hot path and the lock-free invariant. Deferred; registration-time is the high-value, low-risk
slice.

### 9.3 Hardcode the type system in the kernel
Violates mechanism-not-policy and cannot scale to "every host's algebra." The whole point of the
xok borrow is that the kernel must **not** learn the layout.

### 9.4 Statically verify interpreters (xok-grade)
Not feasible in Python without a restricted language/sandbox. We accept discipline + watchdog +
determinism cache (§4) and say so honestly.

## 10. Backward compatibility

- No interpreter registered ⇒ registration behaves exactly as RFC 0001/0002 today.
- `derived_*` is only consulted when an interpreter returns it under an *authoritative* adoption
  policy; *advisory* is a pure lint.
- The structured-diagnostic envelope is additive: existing error consumers see the rendered
  `message`; new consumers read `diagnostics`.

## 11. Migration plan

1. Kernel: add the interpreter registration point, the `StructureView`/`Verdict`/`Diagnostic`
   types, the registration-path invocation + watchdog + determinism cache, and the structured-
   error envelope. Update `API.md` + `CHANGELOG.md` in the same commit. Default-off keeps the
   suite green.
2. Host (companion): register `compose_check` as a *describe* interpreter and `derive_requires`/
   `derive_resolves` as a *derive* interpreter (authoritative for synthesized capabilities,
   advisory for hand-written). Emit structured diagnostics from both. Route the kernel's
   structured `load_failed` into the existing self-coding repair loop, applying `derived` patches
   without an LLM round-trip and surfacing `speculative` hints to the agent.
3. Measure: LLM round-trips per successful self-coded capability, before/after. The tier-1
   collapse (auto-patch) is the headline metric.

## 12. Open questions

- **Adoption policy granularity.** Per interpreter, per candidate origin (synthesized vs.
  hand-written), or a per-plugin flag? Leaning: by origin — the loader knows what it synthesized.
- **Interpreter registration authority.** A reserved `kernel.interpreter` grant, a `CoreConfig`
  list, or both? Whatever it is, it must be tightly held — an interpreter shapes what may enter
  the graph.
- **Multiple interpreters.** Ordering and conflict (one `ok`, one `reject`)? Proposed: reject
  wins; diagnostics merge; derived footprints union (with a divergence warning).
- **Interpreter failure** (raises / times out / non-deterministic): treat as `reject` with a
  diagnostic — fail safe, never fail open.
- **Determinism enforcement strength.** Is the cache-drift check + timeout enough, or do we want
  a declared-pure sandbox (e.g. restricted builtins) as a later hardening?
- **Resolution-time interpreters (§9.2).** If/when schema enforcement at hook boundaries is
  wanted, it is a separate proposal because of the hot-path cost.

## 13. Lineage note

| xok mechanism | RFC 0003 analog |
|---|---|
| UDF: app downloads a deterministic interpreter of a layout the kernel can't read | host registers a structural interpreter of plugin structure the kernel can't read |
| Kernel runs the UDF to *derive ownership*, never trusting the app's claim | kernel runs the interpreter to *derive the authority footprint*, never trusting the generator's manifest |
| UDF *describes*; kernel *decides*; UDF carries no authority | interpreter returns a verdict; kernel commits/rejects; interpreter is authority-free |
| UDFs are verified deterministic/bounded by a restricted loader | interpreters are *disciplined* deterministic/bounded by contract + watchdog + cache (weaker; in-process) |
| Protection enforced across a hardware boundary | **no boundary** — robustness against bugs/hallucination, not security against malice |

The borrow is faithful where it counts (downloaded interpretation, derive-don't-trust,
describe-not-decide) and honestly weaker where our model lacks xok's protection boundary. What is
new — and, in a self-modifying runtime, possibly the more valuable half — is routing **repair
intelligence** through the same downloaded interpreter: the errors are not a side effect of
enforcement but the substrate of the write→check→repair loop that lets an agent safely extend
its own kernel.

---

## 14. Review / Decision

**Decision: this RFC is not accepted as framed. It is decomposed, and the active direction
continues in `0003-admission-probe-and-gating.md` (v2).** The full per-component analysis is in
`0003-review-kernel-boundary.md`; this section records the disposition.

### 14.1 Verdict

The exokernel borrow is faithful in spirit and §7 is admirably honest, but the RFC over-bundles
three different things under one banner with very different cost/value ratios, and it pulls **host
vocabulary into the kernel** — the same mechanism-not-policy violation this RFC rejects in §9.3,
one level up. Stripped to what only the kernel can provide, the valuable atom is not "a downloaded
structural interpreter"; it is the **`validate | commit` seam** the interpreter would have plugged
into. v2 exposes that seam directly (a side-effect-free admission probe + an atomic pre-commit
guard) and keeps the interpreter as an optional, opaque, deferred layer.

A second finding reshaped the scope: the prototype already obtains side-effect-free *behavioral*
validation by **spawning a subcore with injected dependencies**. That is the more drift-proof way
to get a behavioral dry-run (it runs the real path in a throwaway core). So the kernel addition
must **not** try to be the dry-run; its irreducible niche narrows to the **live-graph admission
bracket** around the subcore — which the subcore's *constructed* environment structurally cannot
provide.

### 14.2 The split

| Component of this RFC | Disposition | Where it goes |
|---|---|---|
| A pre-commit point at the boundary every registration crosses | **Accept (irreducibly kernel)** | v2 §3, §4.1 — the `validate \| commit` seam |
| A side-effect-free way to ask "will this fit the live graph?" | **Accept — and it is the headline, not a footnote** | v2 §4.2 `check_plugin` / `AdmissionResult` |
| Guaranteeing "still fits" at the commit instant (TOCTOU) | **Accept (only the kernel holds the lock)** | v2 §4.3 atomic probe-then-commit |
| Running a downloaded interpreter at the seam | **Defer, blob-only, behind a consumer** | v2 §4.4 — opaque `Interceptor`; no kernel-defined types |
| `StructureView` / `Diagnostic` / `Fix` *shapes* | **Reject as kernel surface (host vocabulary)** | host companion repo |
| The interpreter logic (`compose_check`, `derive_requires`) | **Reject as kernel surface (policy, §9.3)** | host companion repo |
| Silent rewrite of frozen `requires`/`resolves` from a derived footprint (§5 authoritative) | **Reject** | dropped — agent re-authors its manifest from probe faults; RFC 0002 invariant preserved (v2 §7) |
| Watchdog presented as a fail-safe (§4) | **Reject the claim** | in-process Python cannot bound sync CPU; v2 §7 states the honest contract |
| Per-call / resolution-time interpreters (§9.2) | **Out of scope** | separate proposal; lock-free path untouched |

### 14.3 Why the rejections, briefly

- **Metadata rewrite** breaks the frozen, author-owned `PluginMetadata` invariant
  (`test_plugin_metadata_immutability`) and inverts RFC 0002's "the author declares; the manifest
  is the only door." It is also largely substitutable by probe-faults + re-register, so it buys
  one round-trip and mandatory-vs-cooperative adoption at the cost of a constitutional change — not
  worth it without measured evidence.
- **Host vocabulary in the kernel** is the §9.3 violation re-applied to error/structure *types*.
  The kernel must carry blobs (`structure: object`, `diagnostics: object`), not learn a `kind`
  enum or render type-error text.
- **Watchdog-as-fail-safe** is unachievable in-process: a hanging interceptor hangs registration
  regardless of a time bound. Honesty here matters because the original §4 leaned on it.

### 14.4 The constitutional caveat (carried forward)

Even the accepted kernel atom is default-off mechanism. v2's §4.2–4.3 clear the "is there a
consumer?" bar with *in-repo* uses that exist regardless of any self-coding host (the supervisor
probing before restart; tests asserting "would fail" without the register/rollback dance). v2's
§4.4 does **not** clear that bar and is therefore explicitly deferred until the host companion has
an interpreter to register. Build the seam now; build the interceptor with its consumer.

### 14.5 Resulting actions

1. Continue in `0003-admission-probe-and-gating.md` (v2); this document remains as the discussion
   artifact and is not implemented as written.
2. Implement v2 §4.1–4.3 in the kernel (`validate | commit` split, `check_plugin`, atomic commit),
   updating `API.md` + `CHANGELOG.md` in the same commit.
3. Leave v2 §4.4 (opaque interceptor) and any derived-footprint adoption unbuilt until a host
   consumer and measured cost justify them.
