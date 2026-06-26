---
name: development-documentation-blueprint
description: Use this skill whenever any agent needs to know the canonical structure, folder conventions, naming rules, handoff placement rules, note formatting standards, or documentation writing principles for developer-facing documentation in this project. Triggers include: setting up a docs structure, auditing docs for structural conformance, placing any agent-generated artifact into the docs tree, writing or evaluating any documentation note, or resolving any question about where a file belongs or how it should be written. Always read this skill before making any decision about documentation structure, placement, or formatting.
---

# Development Documentation Blueprint

This skill defines the canonical structure, formatting standards, and writing
principles for the **internal mirror layer** of developer-facing documentation
in this project. It is the single source of truth for folder layout, naming
conventions, note types, formatting rules, and linking conventions for that
layer.

---

## Two-Layer Documentation System

This project maintains two distinct documentation layers inside `docs/`. This
skill governs only the internal mirror layer. Agents working on the public
layer must read the other skill instead.

| Layer | Location | Governed by | Audience | Links |
|---|---|---|---|---|
| Internal mirror | `docs/<package>/` | This skill | Internal developers | Wikilinks |
| Public website | `docs/tutorials/`, `docs/how-to/`, `docs/explanation/`, `docs/reference/` | Public blueprint skill | External developers | Markdown links |

**Internal mirror layer** (this skill) — Atomic file-folder notes that mirror
the source tree one-to-one. Written as an internal knowledge base for
developers working on the codebase. Not built into the public website. Uses
Obsidian-style wikilinks. Agent handoff files (`agent_data/`) live here.

**Public layer** (separate skill) — The MkDocs website source, structured
using top-level Diátaxis sections. Written for external developers building
with the framework. Built and deployed as a public website. Uses standard
markdown links. Governed entirely by the public blueprint skill at:
`.claude/skills/public-document-blueprint-SKILL/SKILL.md`

The two layers are strictly separated. Never place public-layer content inside
a file-folder, and never place internal atomic notes inside the public Diátaxis
sections. Co-location of layers in the same folder is a structural
non-conformance in both blueprints.

---

## Documentation Root

All developer-facing documentation **for the kernel** lives under `./docs/` at
the repository root. This is the only canonical location for kernel docs. Kernel
documentation found outside this root is a structural non-conformance.

### Scope: kernel only

`docs/` documents the kernel (`src/uxok/`) and nothing else. This repo is the
uxok kernel alone; there is no application or prototype co-located here. Any
application built on uxok lives in its own repository with its own docs. Never
place application docs under `docs/`, and never mirror non-kernel source into the
`docs/` tree. The mirroring rule below applies only to kernel source under `src/`.

---

## Folder Structure

The `docs/` tree mirrors the source tree exactly, with one folder created per
source file. Every source file has a dedicated documentation folder. All
documentation concerning a given source file lives inside that folder.

### Mirroring Rule

The mirror starts at the first semantically meaningful directory level.
Leading path components that are generic, single-purpose containers are
stripped before mirroring begins.

**Strip** any leading directory whose sole purpose is to contain source files
with no domain, feature, or ownership meaning of its own.

Common examples of directories to strip: `src/`, `lib/`, `app/`, `source/`

**Keep** the first directory whose name reflects domain, feature, component,
or ownership. When ambiguous, err toward keeping the directory.

Stripping applies only to the leading path component — do not strip
generic-sounding names that appear deeper in the path.

**Examples:**

| Source file | Docs folder |
|---|---|
| `src/auth/handlers/login.py` | `docs/auth/handlers/login.py/` |
| `lib/utils/parser.js` | `docs/utils/parser.js/` |
| `auth/handlers/login.py` | `docs/auth/handlers/login.py/` |
| `src/app/billing/invoice.py` | `docs/app/billing/invoice.py/` |

In the last example: `src/` is stripped as a generic container, but `app/`
is retained — stripping applies only to the leading component.

### File-Folder Naming

The documentation folder takes the exact name of the source file, including
its extension. This makes the mapping unambiguous and scriptable without
language-specific logic.

```
src/auth/handlers/login.py   →   docs/auth/handlers/login.py/
src/billing/invoice.js       →   docs/billing/invoice.js/
src/core/engine.ts           →   docs/core/engine.ts/
```

---

## Agent Data Subfolder

Every file-folder may contain an `agent_data/` subfolder. This is the
reserved location for all agent-generated artifacts in this project.

```
docs/auth/handlers/login.py/
└── agent_data/
    └── <agent-name>/
        └── ...
```

The structure beneath `agent_data/` is not defined by this skill. Each agent
declares and owns its own subfolder structure within `agent_data/`, defined
in that agent's instruction file. This keeps the system extensible — new
agents can be added without any changes to this blueprint.

---

## Agent Artifact Placement

Agent artifacts are placed under `agent_data/<agent-name>/` within the
appropriate file-folder. The scope rules for placement are:

**Narrow scope** (single file or module): place inside the file-folder of
the primary subject.
```
docs/auth/handlers/login.py/agent_data/<agent-name>/...
```

**Broad scope** (multiple files, whole module, or entire codebase): place
inside the `agent_data/` folder of the highest-level folder that contains
all subjects. For a whole-codebase scope:
```
docs/agent_data/<agent-name>/...
```

The structure beneath `<agent-name>/` is defined entirely in that agent's
own instruction file. The blueprint makes no assumptions about it.

---

## Docs Changelog

`docs/DOCS-CHANGELOG.md` sits directly in the `docs/` root. It is a flat,
append-only log of every documentation change made by the technical writer.
It is not a note and does not follow the file-folder structure.

### Format

```markdown
# Docs changelog

## YYYY-MM-DD

### <scope — e.g. module name, file-folder path, or "housekeeping">

- **Added** `<path/to/note.md>` — <one-line description of what was written>
- **Updated** `<path/to/note.md>` — <one-line description of what changed and why>
- **Removed** `<path/to/note.md>` — <one-line description of why it was removed>
```

### Rules

- One date block per writing session. If multiple scopes were touched in a
  single session, use multiple `###` scope headings under the same date block.
- Entries are prepended — newest date block at the top.
- Use exactly three entry verbs: `Added`, `Updated`, `Removed`. No others.
- The path in each entry is the path relative to the repository root.
- One line per note changed. Do not group multiple notes into a single entry.
- Do not log agent artifact changes (handoff files, `agent_data/` contents) —
  only log notes that a developer would read.
- If no `DOCS-CHANGELOG.md` exists, create it before writing any notes.

---

## Conformance Checklist

When evaluating structural and formatting conformance against this blueprint:

**Structure**
- [ ] `docs/` exists at the repository root
- [ ] Every source file has a corresponding folder in `docs/` following the
      mirroring rule
- [ ] Folder names match source filenames exactly, including file extension
- [ ] No documentation exists outside `docs/`
- [ ] All agent artifacts are placed inside `agent_data/<agent-name>/` within
      the appropriate file-folder
- [ ] No user-facing and developer-facing documentation is co-located within
      the same file-folder

**Note types**
- [ ] Every file-folder contains an `overview.md`
- [ ] No note covers more than one distinct concern
- [ ] Note filenames follow the prescribed naming patterns
- [ ] How-to filenames describe a specific task

**Linking**
- [ ] All internal cross-references use `[[wikilinks]]`, not markdown links
- [ ] Every sibling note is linked from `overview.md` at least once
- [ ] No note duplicates content that exists in another note — links instead
- [ ] No heading contains a wikilink

**Writing**
- [ ] Present tense and active voice throughout
- [ ] Reference notes contain no explanatory prose — links to explanation instead
- [ ] Explanation notes contain no API detail — links to reference instead
- [ ] Explanation notes cover file-level rationale only — they do not restate
      system-level "why" owned by the public `explanation/` layer (altitude rule)
- [ ] How-to steps are numbered and each step contains one action
- [ ] Code blocks specify a language identifier
- [ ] Headings use sentence case
- [ ] `#` used only for the note title; body sections begin at `##`

Each file-folder contains atomic notes. Every note covers exactly one
concern and is self-contained enough to be read and understood independently.
Notes are typed using the Diátaxis framework. Four types are recognised:

### Overview
**Filename:** `<subject> — overview.md`
**One per file-folder. Required.**

The entry point for the file-folder. A short orientation note that states
what the source file does, why it exists, and how it fits into the broader
system. Links out to all other notes in the folder and to related file-folders
across the vault. Contains no API detail — that belongs in Reference.

Readers: anyone encountering this file for the first time.
Length: 1–3 paragraphs. Never longer.

### Reference
**Filename:** `<subject> — reference.md` 
**One or more per file-folder. Written as needed.**

Complete, factual description of the API surface: public functions, classes,
methods, parameters, return types, exceptions thrown, and any side effects.
Written in the present tense. No prose explanation of why — that belongs in
Explanation. Scannable above all else; dense is appropriate here.

Readers: developers actively implementing against the interface.
Structure: one section per public entity. Consistent heading hierarchy.

### Explanation
**Filename:** `<subject> — explanation.md`
**One or more per file-folder. Written as needed.**

The reasoning behind design decisions — why the code works the way it does,
what tradeoffs were made, what alternatives were considered. Discursive and
conceptual. Never duplicates Reference content; links to it instead.

**File altitude only.** This note explains why *this one source file's*
internals are built the way they are: its data structures, invariants,
concurrency model, policy enums, the specific tradeoffs of this implementation.
It must **not** restate system-level rationale — why the primitive or concept
exists in the framework at all — which is owned canonically by the public
`explanation/` layer. Orient the reader by *naming* the concept ("the hook
system exists to provide priority-ordered extension points; this note covers how
`_system.py` caches resolved hook chains"), but never restate the system-level
"why." Restating it is a conformance violation, because the same rationale then
drifts in two places. The public layer cannot link inward (it excludes this
mirror from its build), so the non-restatement boundary is what keeps the two
layers from diverging.

Readers: developers trying to understand intent, preparing to modify or
extend the code.
Length: as long as needed. Depth is the point here.

### How-To
**Filename:** `how-to — <task>.md`
**Zero or more per file-folder. Written as needed.**

Task-oriented guides for specific goals a developer might have when working
with this file. Each how-to covers exactly one task. Assumes the reader has
read Overview and Reference. Numbered steps. No theory — link to Explanation
for that.

Readers: developers trying to accomplish a specific outcome.
Example titles: `how-to — add a new auth provider.md`,
`how-to — override default timeout.md`

---

## Atomic Note Principles

Every note in the vault — regardless of type — follows these rules:

**One idea per note.** If a note covers two distinct concerns, split it.
The test: can this note be linked to meaningfully from another note? If the
title would be ambiguous without reading the whole note, it is too broad.

**Self-contained.** A note must be readable and useful without requiring
the reader to first read another note. Context that is essential to
understanding the note belongs in the note, even if it is also stated
elsewhere. Context that is supplementary belongs as a link.

**Descriptive title.** The filename is the note title and the link target.
Titles must be specific enough to be meaningful in isolation. Avoid generic
titles like `notes.md` or `misc.md`.

**No duplication.** If information exists in another note, link to it —
do not repeat it. Duplication creates drift. The only exception is essential
orienting context that makes the note self-contained.

**Link liberally.** Whenever a note references a concept, component, or
decision documented elsewhere in the vault, link to it. Links are the
primary mechanism for navigating the vault and for surfacing relationships
between components.

---

## Linking Conventions

All cross-note references use Obsidian-style wikilinks. Markdown links are
not used for internal vault references.

### Basic wikilink
```
[[note title]]
```
The note title is the filename without the `.md` extension.

### Wikilink with display text
Use when the filename alone would read awkwardly in prose:
```
[[login.py — reference|the login reference]]
```

### Linking to a specific heading
```
[[login.py — reference#Parameters]]
```

### Wikilink placement rules
- Link on first meaningful mention of a concept within a note
- Do not link the same target more than once per note
- Do not link from headings — link from body text only
- Links in Overview notes serve as the canonical index of the file-folder;
  ensure every sibling note is linked from Overview at least once

### Naming convention for link targets
Filenames must be stable and unambiguous because they are link targets
across the vault. Follow this pattern:

| Note type | Filename pattern |
|---|---|
| Overview | `<subject> — overview.md` |
| references | `<subject> — reference.md` |
| explanations | `<subject> — explanation.md` |
| How-to | `how-to — <task>.md` |

Use an em dash (`—`) as the separator, not a hyphen. This keeps filenames
scannable and visually distinct from ordinary hyphenated words.

---

## Writing Standards

> **Sentence-level voice is owned by the voice skill**
> (`.claude/skills/writing-style-SKILL/SKILL.md`). This section sets tense,
> person, and per-note density; the voice skill governs how the prose itself
> reads. Where the two meet on density, this blueprint wins (see the voice
> skill's Precedence table). Read the voice skill before writing any prose.

### Voice and tense
- Present tense throughout: "the function returns" not "the function will return"
- Active voice: "the handler validates the token" not "the token is validated"
- Second person for how-to notes: "call `validate()` with the token"
- Third person for reference and explanation notes

### Density by note type
- **Reference:** maximum density. Every word earns its place. No throat-clearing
  or scene-setting. The reader is looking something up.
- **Explanation:** discursive is appropriate. Prose paragraphs. Reasoning and
  nuance matter more than brevity.
- **Overview:** tight. 1–3 paragraphs. No detail.
- **How-to:** step-driven. Numbered lists. One action per step. Minimal prose.

### What belongs where
| Content | Belongs in |
|---|---|
| What a function does | Reference |
| Why this file's internals are built this way | Explanation (file altitude) |
| Why the primitive/concept exists at all | Public `explanation/` (system altitude) — not here |
| What parameters it accepts | Reference |
| What tradeoffs this implementation made | Explanation (file altitude) |
| How to accomplish a task | How-to |
| How this fits into the system | Overview |
| Exceptions thrown | Reference |
| When not to use this | Explanation |

### Code blocks
Always specify the language for syntax highlighting. Use the actual language
identifier, not a generic label.

```python
# correct
def validate(token: str) -> bool:
    ...
```

### Headings
- `#` is reserved for the note title only (first line of the file)
- `##` for major sections
- `###` for subsections
- Never skip levels
- Sentence case always: `## Error handling` not `## Error Handling`

### Admonitions
Use blockquote-style callouts for important notices. Keep them rare — overuse
destroys emphasis.

```markdown
> **Note:** this behaviour changed in v2.0. See [[changelog — explanation]].

> **Warning:** calling this without a valid session will raise `AuthError`.
```

---

## Full File-Folder Example

```
docs/auth/handlers/login.py/
├── login — overview.md
├── login — reference.md
├── login — explanation.md
├── how-to — add a new auth provider.md
└── agent_data/
    └── doc-auditor/
        └── handoffs/
            └── audit-2024-11-14-001.md
```

`overview.md` links to every sibling note and to related file-folders such
as `[[docs/auth/validators/TokenValidator.py/overview]]`. Reference and
Explanation link to each other where relevant but do not duplicate content.