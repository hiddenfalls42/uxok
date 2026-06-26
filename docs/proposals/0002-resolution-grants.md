# RFC 0002 — Resolution Grants

- **Status:** Accepted (kernel half implemented; host companion §5 separable)
- **Date:** 2026-06-25
- **Affects:** `docs/manifests/API.md` (constitutional), `Plugin` / `PluginMetadata`,
  `enforce_requires` (`core/_core_facet.py`), `validate_requirements`
  (`core/_capability_system.py`), `RESERVED_CAPABILITIES`, `CHANGELOG.md`
- **Builds on:** RFC 0001 (Secure Capabilities) — this refines the consumer-side gate it
  introduced
- **Type:** Constitutional API change — the accepted version lands its `API.md` and
  `CHANGELOG.md` edits in the same commit as the implementation. This document is the
  discussion artifact that precedes that edit.

---

## 1. Summary

RFC 0001 made `requires` do double duty: it is **both** the load-order dependency list
(every name must already be registered when the plugin registers —
`validate_requirements`) **and**, under `capability_access="sealed"`, the runtime
resolution allow-list (`get_capability(name)` is rejected unless `name ∈ requires` —
`enforce_requires`).

Those are two independent facts, and fusing them makes sealed mode unsatisfiable for any
runtime that resolves capabilities **lazily**, **cyclically**, or **after the resolver
itself has registered** — i.e. for any dynamic, self-extending host. A capability you
resolve at runtime is not necessarily a capability that must *exist when you register*,
and sometimes it provably cannot (registration cycles; capabilities that hot-load later).

This RFC **separates the two meanings**:

1. `requires` — keeps its current meaning: hard load-order dependencies, validated at
   registration. Unchanged.
2. `resolves` — **new**: the set of capabilities a plugin is authorized to resolve at
   runtime. Checked by `enforce_requires`; **not** validated at registration.

Plus one reserved grant — `kernel.dispatch` — for the single legitimate "resolve anything
by runtime-supplied name" surface (a control plane / dispatcher), mirroring how RFC 0001's
`kernel.lifecycle` already carved out graph control.

The gate stays an **exact-name set-membership test** — no tags, wildcards, or namespaces
are added (see §9.3 for why they turned out to be unnecessary). The change is
backward-compatible at defaults (`resolves` defaults to empty; existing plugins behave
identically) and has no measurable runtime cost.

## 2. Motivation

### 2.1 What RFC 0001 established

Under `capability_access="sealed"`, a plugin's `self.core` is an attenuated `CoreFacet`,
and `enforce_requires` (`core/_core_facet.py`) gates resolution:

```python
def enforce_requires(capability, owner, mode) -> str:
    name = derive_capability_name(capability) ...
    if mode == "open":
        return name
    if name not in owner.metadata.requires:        # the gate
        raise CapabilityAccessError(...)
    return name
```

Separately, at registration, `validate_requirements`
(`core/_capability_system.py`) reads the **same** field:

```python
missing = [c for c in plugin.metadata.requires
           if c not in RESERVED_CAPABILITIES and not self._capabilities.get(c)]
if missing:
    raise MissingCapabilityError(missing, phase="register")
```

So `plugin.metadata.requires` is the sole input to both a **load-order** check and a
**runtime-authority** check.

### 2.2 The conflation

These two checks answer different questions:

| Question | Mechanism | When |
|---|---|---|
| "Must this provider exist before I can be registered?" | `validate_requirements` | registration |
| "Am I allowed to resolve this provider at runtime?" | `enforce_requires` | every `get_capability` under sealed |

A capability can be a **yes** to the second and a **no** to the first: I am authorized to
resolve `flow_runner` during a turn, but it need not exist at the instant I register —
indeed it may register *after* me, or hot-load minutes later. `requires` cannot express
that distinction; declaring the name to satisfy the gate also imposes the load-order
constraint.

### 2.3 Three patterns this breaks (consumer evidence)

From the reference host (`uxok-host`), all three are load-bearing, not incidental:

1. **Late / out-of-order resolution.** `assistant` resolves `"flow_runner"` inside
   `dispatch_flow` (a per-turn method), but the host registers `assistant` *before*
   `flow_runner`. Putting `"flow_runner"` in `assistant.requires` to satisfy the gate makes
   `validate_requirements` reject `assistant`'s registration. The name is perfectly static;
   the load-order rider is the problem.

2. **Registration cycles.** Two orchestration plugins that each resolve the other at
   runtime have **no** registration order that satisfies both `requires` lists, even though
   neither is a true load dependency of the other.

3. **Dynamic / hot-loaded providers.** The host loads capabilities asynchronously via a
   watch loop and *synthesizes new ones at runtime*. A plugin routinely resolves a provider
   that is not registered at the plugin's own registration moment. `requires`
   (must-exist-now) structurally contradicts a graph whose premise is that capabilities
   come and go.

### 2.4 The existence proof: `kernel.lifecycle`

RFC 0001 already discovered that one authority — **graph control** — could not be expressed
as a normal `requires` entry, and carved out a reserved capability
(`RESERVED_CAPABILITIES = {"kernel.lifecycle"}`) that is (a) registration-exempt and (b)
resolved directly by the kernel (`core/_core.py`, the `kernel.lifecycle` intercept). That is
the same shape of problem this RFC generalizes: **authorization that is not a load
dependency**. RFC 0001 solved it for one tier; `resolves` solves it for capability
resolution at large.

### 2.5 What is explicitly *not* the problem

It is tempting to blame naming. The host models some "role with variants" capabilities by
putting the variant in the **name** (`provides="disk_backend"`, `tags={"vfs_backend"}`) and
resolving the variant name dynamically — which *looks* like "the name can't be declared."
But the host already models the same shape correctly elsewhere
(`provides="inference_backend"`, `tags={"ollama"|"mock"|...}`, resolved by tag), proving the
name *can* be static. Normalizing those few inverted capabilities (§5) is a host concern and
removes the apparent dynamism. **It does not remove the §2.2 conflation**, which is
independent of naming and lives in the kernel. This RFC fixes the kernel half; §5 records the
host half as a companion, non-normative migration.

## 3. Proposal

### 3.1 Add `resolves` to plugin metadata

A new optional declaration, sibling to `requires`:

```python
class MyPlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="assistant",
            requires={"prompt_store"},          # must exist at registration (unchanged)
            resolves={"flow_runner", "catalog", "cancellation"},  # may resolve at runtime
        )
```

Semantics:
- `requires` — unchanged. Hard load-order dependencies; validated at registration.
- `resolves` — the set of capability names the plugin is **authorized to resolve** at
  runtime under sealed/declared. **Not** validated at registration; a name here that has no
  provider is simply unresolvable until one appears (the resolution itself raises the normal
  `CapabilityError`, exactly as today).

A capability that is *both* a load dependency *and* resolved at runtime may appear in both
sets; the union is the runtime grant (see §3.2). Authors are encouraged to put a name in
`requires` only when they genuinely need it present at registration.

### 3.2 `enforce_requires` checks the union

```python
def enforce_requires(capability, owner, mode) -> str:
    name = derive_capability_name(capability) ...
    if mode == "open":
        return name
    grants = owner.metadata.requires | owner.metadata.resolves
    if name in grants:
        return name
    if "kernel.dispatch" in grants:      # reserved broad grant (§3.4)
        return name
    raise CapabilityAccessError(name, owner.metadata.name, sorted(grants))
```

Still a synchronous set-membership test — no `await`, preserving the lock-free
capability-mutation invariant (RFC 0001 decision #12). The `CapabilityAccessError` message
reports the union so the remedy ("add it to `resolves`") is obvious.

### 3.3 `validate_requirements` ignores `resolves`

No change beyond *not* reading the new field. It continues to validate `requires` only
(minus `RESERVED_CAPABILITIES`). `resolves` imposes no registration constraint — that is the
entire point.

### 3.4 Reserved grant: `kernel.dispatch`

Add `"kernel.dispatch"` to `RESERVED_CAPABILITIES`. A plugin that declares it
(`resolves={"kernel.dispatch"}`) may resolve **any** capability by name. This authorizes the
one surface that legitimately needs it — a control plane / dispatcher that resolves
capabilities named by an incoming request (in the reference host, `http_server`'s `/call`).

Like `kernel.lifecycle`, it is registration-exempt and grants nothing but the authorization
itself; resolution still goes through the live capability system and returns whatever the
registry holds. It is deliberately coarse and deliberately explicit: a `kernel.dispatch`
grant is a single, greppable, auditable declaration that "this plugin is a dispatcher,"
replacing the alternative of every dispatcher reaching around the facet to the raw core.

### 3.5 The gate stays exact-name

No tag-, wildcard-, or namespace-matching is added to `enforce_requires`. The earlier draft
of this idea proposed `resolves={"tag:index"}` / `"index.*"` to cover factories that resolve
a *class* of capabilities. That machinery is **unnecessary** once role-with-variant
capabilities are modeled with the role as `provides` and the variant as a `tag` (§5): the
resolver then resolves a single static name (`"vfs_backend"`), which an exact-name `resolves`
covers. Keeping the gate a plain set test preserves its O(1), lock-free, trivially-auditable
character. If a future need for class-grants emerges that naming cannot absorb, it can be a
separate, additive RFC.

## 4. API changes (constitutional)

- `Plugin.__init__` gains a keyword-only `resolves: set[str] | frozenset[str] | None = None`,
  normalized identically to `requires` (Protocol types accepted and reduced to names).
- `PluginMetadata` gains a frozen `resolves: frozenset[str]` field (default empty).
- `enforce_requires` signature unchanged; behavior per §3.2.
- `RESERVED_CAPABILITIES` gains `"kernel.dispatch"`.
- `API.md` §3 (Plugin construction) and §12/§15 (capability access) document `resolves` and
  the new reserved grant.

No change to `get_capability`, the resolution path, hot-reload, events, or the open-mode
default.

## 5. Host companion (non-normative)

For completeness, the reference host adopts two changes that are *not* part of the kernel
contract but make the kernel change land cleanly:

1. **Declare `resolves`.** Each plugin lists the capabilities it resolves at runtime; the
   self-coding loader derives a synthesized flow's `resolves` from its steps (extending the
   existing `derive_requires`). The `kernel.lifecycle` grants from the RFC-0001 migration
   fold into `resolves`.
2. **Normalize inverted factories.** `disk_backend`/`memory_backend`/index/codec providers
   move the variant from `provides` to `tags` (matching `inference_backend`): e.g.
   `provides="vfs_backend", tags={"disk"}`. Resolvers then resolve the static role name with
   a tag selector, and the dispatcher/control-plane surface declares
   `resolves={"kernel.dispatch"}`. This deletes the host's `real_core` gate-bypasses that
   were standing in for the missing `resolves`/`kernel.dispatch` mechanisms.

## 6. Backward compatibility

- `resolves` defaults to empty. A plugin that declares only `requires` behaves **exactly**
  as today in all three modes.
- Under `capability_access="open"` (the default), `enforce_requires` short-circuits before
  consulting either set — zero behavioral change for existing deployments.
- `validate_requirements` is unchanged in effect.
- The new reserved grant only matters to a plugin that opts into it.

## 7. Security analysis

What this changes, honestly:

- **No loss.** Open mode is unchanged. Under sealed, a plugin's runtime reach is still a
  declared, finite allow-list; `resolves` does not widen authority, it relocates the
  *non-load-order* part of it out of `requires`.
- **Auditability improves.** Today, to seal a dynamic host you must either reorder the
  graph or bypass the gate (raw-core access), and bypasses are invisible to static analysis.
  With `resolves`, what a plugin may *invoke by name* at runtime is `requires ∪ resolves ∪
  ({all} if kernel.dispatch)` — readable from the manifest, greppable, lintable. (This is the
  invocation boundary, not a reference-isolation boundary: a live handle can still cross a
  return/argument/payload edge or be reached by reflection — see `API.md` §3.2 and spec 0005
  §4.) Replacing ad-hoc bypasses with a declared grant is a net auditability gain.
- **The dispatch hole is explicit.** `kernel.dispatch` is broad by nature (a control plane
  can reach anything). This RFC does not shrink that authority — it *names* it. A reviewer
  can enumerate every `kernel.dispatch` holder and scrutinize exactly those. That is strictly
  better than the status quo, where the same breadth is achieved invisibly via raw-core
  access.
- **Still ambient-within-set.** `resolves` is a coarse, lifetime, all-methods grant — the
  same authority model RFC 0001 established, not finer. Per-instance / per-argument
  attenuation and least-authority for *untrusted* (self-generated) capabilities are out of
  scope and belong to the object-capability path (§10).

## 8. Performance

Effectively zero. `enforce_requires` remains a synchronous set-union membership test (the
union can be precomputed once per plugin at `_attach_core` if even that shows up, which it
will not). No new awaits, no per-call allocation, no change to the resolution path, no impact
on the lock-free tick/resolution invariants.

## 9. Alternatives considered

### 9.1 Reorder registration so every resolved name exists first
Works only for the acyclic, statically-loaded subset. Fails on registration cycles
(§2.3.2) and on capabilities that hot-load after the resolver (§2.3.3). A fragile patch for
a structural problem.

### 9.2 Make `validate_requirements` non-fatal
Fixes boot but throws away load-order dependency validation — a real feature that catches
"you forgot to provide a hard dependency." The split preserves both guarantees.

### 9.3 Tag / namespace / wildcard grants in the gate
Considered and dropped. Once role-with-variant capabilities are named correctly (§5), the
resolver resolves a static name and an exact-name grant suffices. Adding pattern matching
would complicate the gate to solve a problem that better naming dissolves. Left as a possible
future additive RFC if a genuine non-namable class-grant need appears.

### 9.4 Object capabilities (now)
Deferred — see §10. The right long-term target for *attenuation and least-authority*, but a
paradigm shift (resolution by held reference, not by name) with a large host migration and
per-call membrane overhead. `resolves` is the cheaper, non-breaking step that also makes the
ocap migration *easier*, not harder.

## 10. Forward compatibility with object capabilities

`resolves` is deliberately shaped to be an ocap stepping stone, not a dead end. Once every
plugin *declares* the capabilities it resolves, the kernel has, per plugin, the exact set of
authorities it would otherwise be *handed as references*. A future ocap kernel can therefore
migrate surface-by-surface: keep declarative grants for trusted, kernel-adjacent
infrastructure, and hand **attenuated handles** (path-scoped, method-scoped, revocable) to
*untrusted* self-generated capabilities — exactly where least-authority and precise
revocation pay off. `resolves` is the analysis work ocap requires, done once, cheaply, and
usable immediately.

## 11. Migration plan

1. Kernel: add `resolves` (Plugin/PluginMetadata), update `enforce_requires` (§3.2), add
   `kernel.dispatch` to `RESERVED_CAPABILITIES`, update `API.md` + `CHANGELOG.md` in the same
   commit (per the versioning policy). Default-empty `resolves` keeps every test green.
2. Host (companion, separable): declare `resolves` per plugin; normalize the inverted
   factories (§5); give the control plane `kernel.dispatch`; remove the `real_core`
   resolution bypasses; flip `capability_access="sealed"` and validate via the end-to-end
   tests.
3. Optional follow-up: `derive_resolves` in the self-coding loader so synthesized
   capabilities are sealed honestly.

## 12. Open questions

- Should `resolves` accept Protocol types (like `requires`) for symmetry, or names only?
  (Leaning: accept both, normalize identically.)
- Should the kernel warn when a name appears in `requires` that is only ever resolved at
  runtime (i.e., a `requires` that should be a `resolves`)? A lint, not a hard rule.
- Is one `kernel.dispatch` grant sufficient, or do we want a way to scope a dispatcher to a
  capability *tag* (e.g. "may dispatch anything tagged `tool`")? Defer unless a second
  dispatcher with a narrower remit appears.
