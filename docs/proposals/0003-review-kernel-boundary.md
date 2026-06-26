# RFC 0003 Review — What Genuinely Belongs in the Kernel

- **Reviews:** `0003-downloaded-structural-interpreters.md` (Draft)
- **Date:** 2026-06-25
- **Lens:** the kernel boundary (`src/uxok/` is the kernel and nothing else) + mechanism-not-policy.
  The question is not "is RFC 0003 a good idea" (the companion analysis covers that) but
  "of what it proposes, which atoms are *irreducibly* kernel-resident, and which are host
  policy wearing kernel clothes."

---

## 1. The test applied

A component belongs in the kernel only if **no host/plugin code can provide it**, because it
needs one of: (a) the single boundary every registration crosses, (b) authority over what is
actually committed to the graph, or (c) the kernel's data model / exception as a transport.
Everything that has *structural meaning* — the type algebra, the diagnostic taxonomy, the
derivation, the repair tiers — is downloaded by §9.3's own logic: the moment the kernel
understands a `Diagnostic.kind` or a `Fix.confidence`, it has learned the host's layout, which
is exactly the borrow's prohibition.

RFC 0003 mixes the two freely. The rich types it sketches (`StructureView` with a meaningful
`structure`, `Diagnostic` with `kind`/`severity`/`locus`, `Fix` with `confidence`/`patch`) are
**host vocabulary**. Pulling their *shapes* into the kernel would be the same
mechanism-not-policy violation as §9.3's rejected "hardcode the type system," one level up. The
kernel can carry them; it must not define them.

## 2. Partition

| Component | Verdict | Why |
|---|---|---|
| **Pre-commit interception point** — run a registered callback between `validate_requirements` (`_core.py:285`) and `registry.add` (287), honor a veto | **Kernel — irreducible** | The existing `plugin.registered` hook fires *post-commit* (`_core.py:296`) and cannot reject. A guaranteed pre-commit veto at the boundary every path funnels through (`register_plugin`, `load_plugin`, watch loop, tests, direct calls all reach `_register_plugin_now`) can only be the kernel's. This is the one strong kernel claim. |
| **Opaque structure transport** — a field on `PluginMetadata` carrying the host's `structure` blob, kernel never reads it | **Kernel — irreducible, but only as `object`** | The callback needs the candidate's structure at the chokepoint; the kernel must carry it there uniformly. But it carries it *opaquely* — typed `object`/`Mapping[str, Any]`, never interpreted. |
| **Structured-error channel** — an optional `diagnostics` payload on the registration exception | **Kernel — irreducible, but only as an opaque blob** | The kernel is what raises at the boundary, so only the kernel can make the error *uniformly* carry diagnostics from every path. But the kernel attaches an opaque payload; it does not define `Diagnostic`. |
| **Committed-footprint substitution** — kernel commits `derived_requires/resolves` from the verdict instead of the declared sets | **Kernel — but *questionable*; see §4** | Only the kernel controls what reaches `registry.add` / `register_capabilities`, so a *silent* substitution must be kernel. But this is largely replaceable by veto + patch + host re-register. |
| **Interpreter registration authority** — who may install a callback | **Kernel — irreducible** | Installing a graph-shaping callback is the most powerful grant in the system (more than `kernel.lifecycle`). Gating it is necessarily a kernel operation, mirroring the reserved-grant pattern. |
| **Watchdog** (time-bound the callback) | **Kernel-resident *if kept*, but weak** | Only the caller can bound a callee, so if interpreters run kernel-side the bound is kernel-side. But it cannot interrupt sync CPU-bound Python (no thread kill, signals are main-thread/between-bytecode only). It fail-safes a *slow* interpreter, not a hanging one. Not load-bearing; see §5. |
| **Determinism cache** (verdict keyed by structure-hash) | **Kernel-resident *if kept*, not essential** | An optimization plus a non-determinism *detector*. Useful, but the mechanism works without it. Defer. |
| `StructureView` field *shapes*, `structure` semantics | **Host** | The kernel passes name/provides/requires/resolves (which it knows) plus the opaque blob. The *meaning* of `structure` (ports/steps/schema) is downloaded. |
| `Diagnostic` taxonomy (`kind`, `severity`, `locus`, `detail`) | **Host** | Pure policy. The kernel must not enumerate `kind`s or render type-error text (§6 says this; the type table must follow it). |
| `Fix` tiers, `patch`/`hint`, auto-patch application, repair loop | **Host** | §6/§11.2 is host self-coding policy end to end. The kernel never applies a patch. |
| The interpreter logic — `compose_check`, `derive_requires`, derivation | **Host** | §9.3. The whole borrow is that the kernel does not learn the algebra. |
| Adoption policy *choice* (authoritative vs advisory, per origin) | **Host decides; kernel obeys a flag** | The *decision* is host policy. The kernel needs only a boolean per verdict/interpreter: "adopt derived, or lint on divergence." |

## 3. The irreducible kernel core (minimal mechanism)

Stripped to what only the kernel can do, RFC 0003 is **a type-agnostic registration
interceptor with opaque transport** — not a "structural interpreter system." The kernel-minimal
surface is roughly:

```python
# Kernel-side. Note the deliberate absence of StructureView/Diagnostic/Fix shapes.

class Candidate:                       # kernel-known facts + one opaque blob
    name: str
    provides: frozenset[str]
    requires: frozenset[str]
    resolves: frozenset[str]
    structure: object                  # opaque; kernel never inspects

class InterceptResult:
    ok: bool
    footprint: tuple[frozenset[str], frozenset[str]] | None  # (requires, resolves), opaque names
    adopt: bool                        # True → commit footprint; False → lint only
    diagnostics: object | None         # opaque; kernel attaches to the error verbatim

Interceptor = Callable[[Candidate], InterceptResult]
```

The kernel: gates interceptor registration (reserved grant), runs each registered interceptor
at the pre-commit point, rejects on `not ok` (raising an exception that carries the opaque
`diagnostics`), and — *if* `adopt` — commits `footprint` instead of the declared sets. That is
the entire kernel mechanism. Everything with structural meaning lives behind `structure: object`
and `diagnostics: object`.

This is **more faithful to xok than the RFC's own framing**: xok's kernel doesn't know what a
diagnostic is either. It runs the UDF and gets back blocks + ownership, not a typed error tree.
RFC 0003 should carry blobs, not vocabulary.

## 4. The questionable kernel item: committed-footprint substitution

The silent metadata rewrite (§5 authoritative) is the only "kernel" item I'd actively contest,
on two grounds:

1. **It breaks a current invariant.** `PluginMetadata` is frozen and author-owned
   (`test_plugin_metadata_immutability`). Committing a footprint the author didn't write means
   `list()` / `PluginView` / the descriptive surface (RFC 0001 Q3b) report derived-not-declared
   — a new debugging-confusion class — and it inverts RFC 0002's "the manifest is the only
   door" into "the kernel forges the manifest." Defensible for generated code, but a
   constitutional shift in *who authors authority*, not a refinement.

2. **It is largely substitutable by mechanism already in the partition.** Veto (irreducible) +
   structured patch in the opaque `diagnostics` (irreducible) + host re-register gets you the
   same committed footprint *without* the kernel mutating frozen metadata. The only things
   silent substitution buys over veto-plus-patch are: one fewer re-registration round-trip, and
   *mandatory* (rather than cooperative) adoption. Those are an enforcement-strength /
   optimization choice — not an irreducible kernel need.

**Recommendation:** ship the mechanism *without* silent substitution first. Let the verdict
carry a derived footprint as an opaque patch in `diagnostics`; the host applies it and
re-registers (tier-1 auto-patch, no LLM round-trip, §6's headline metric is still met). Add
kernel-side `adopt` only if a measured re-register cost or a "host ignored the patch" failure
justifies promoting frozen-metadata mutation into the kernel. This keeps the frozen-metadata
invariant until there is evidence to break it.

## 5. The watchdog is kernel-resident but cannot deliver what §4 implies

If interpreters run in-kernel, bounding them is kernel-side by definition — but the bound is
weak and the RFC oversells it. A watchdog cannot interrupt synchronous CPU-bound Python; a
`while True:` in a downloaded interpreter hangs registration regardless. The determinism cache
catches *non-determinism*, not *non-termination*. Two honest options, both of which keep the
kernel surface small:

- **Mandate cooperative interpreters** — the interceptor is `async` and the kernel applies an
  `asyncio` timeout. Bounds a *slow* interpreter; a tight CPU loop still hangs, but the contract
  is now enforceable at await points.
- **Don't pretend to bound it** — document the purity/termination contract, run the interpreter
  inline, and accept that a hanging interpreter is a host defect that hangs registration
  (fail-stop, not fail-safe). Honest, and matches "disciplined, not verified."

Either way, drop the claim that the watchdog is a general fail-safe. Real isolation
(subinterpreter/process) is the only thing that delivers it, and §7 already declines that.

## 6. Recommended split for landing

1. **Kernel PR (small, additive, default-off, can land here):** the interception point + reserved
   registration grant + opaque `structure` field on `PluginMetadata` + opaque `diagnostics` on
   the registration exception. No `StructureView`/`Diagnostic`/`Fix` types in the kernel — blobs
   only. No silent substitution. No determinism cache. `API.md` + `CHANGELOG.md` same commit.
   With no interceptor registered, behavior is identical to today.
2. **Host companion (separate repo, the actual consumer):** define `StructureView`/`Diagnostic`/
   `Fix`, register `compose_check`/`derive_requires` as interceptors, structure diagnostics, and
   wire the repair loop. This is where every type with structural meaning lives.
3. **Deferred, evidence-gated:** kernel-side silent footprint substitution (§4) and the
   determinism cache — add only when the host companion shows a concrete cost they remove.

The constitutional caution stands: even the kernel PR has **no in-repo consumer** (the supervisor
doesn't need it). A default-off mechanism with no in-tree caller is speculative core surface.
The minimal blob-only version at least keeps that surface as thin as the boundary genuinely
requires — and refuses to import host vocabulary into the kernel to get there.
