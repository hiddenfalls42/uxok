---
name: public-document-blueprint
description: Use this skill whenever any agent needs to know the canonical structure, folder conventions, naming rules, note formatting standards, or writing principles for the public-facing documentation website in this project. Triggers include: creating or editing any file under the public Diátaxis sections (tutorials/, how-to/, explanation/, reference/), writing index pages, resolving where a public-facing file belongs or how it should read, or auditing the public layer for conformance. For the live build wiring (mkdocs.yml, the reference generator, CI), this skill points at the real files — those are the source of truth, not a copy here. Always read this skill before any decision about public documentation structure, placement, or formatting.
---

# Public Documentation Blueprint

This skill is the single source of truth for the **structure, placement, and
writing standards** of the public documentation website. It governs what an agent
authors: where a page goes, what it contains, how its prose reads.

It is **not** the source of truth for build infrastructure. The live `mkdocs.yml`,
`scripts/gen_ref_pages.py`, `.github/workflows/docs.yml`, and the `[docs]` extra in
`pyproject.toml` are authoritative for their own contents. This skill points at
them and states only the invariants an author must not break. Never trust a copy of
those files reproduced anywhere — read the real file.

The public layer and the internal developer mirror layer coexist inside `docs/`.
This skill governs only the public layer. For the internal mirror (file-folder
atomic notes, wikilinks, agent handoffs), read the developer documentation
blueprint skill.

---

## Two Layers in One `docs/` Tree

```
docs/
├── index.md            # Public landing page
├── tutorials/          # Diátaxis: learning-oriented   ┐
├── how-to/             # Diátaxis: task-oriented        │ public website
├── explanation/        # Diátaxis: understanding         │ (markdown links)
├── reference/          # Diátaxis: generated API surface ┘
├── uxok/              # Internal mirror of src/uxok/ (wikilinks; excluded from build)
├── DOCS-CHANGELOG.md
└── agent_data/         # Agent artifacts (excluded from build)
```

**Public layer** — `index.md` and the four Diátaxis sections. These become the
website, written for external developers building *with* the framework. Standard
markdown links.

**Internal mirror layer** — `uxok/` subfolders mirroring `src/uxok/`. Atomic
notes with wikilinks, for maintainers. Listed in `exclude_docs` and absent from the
built site.

Never co-locate public content inside the mirror folders, and never place internal
atomic notes inside the public Diátaxis sections.

### Scope: kernel only

This website documents the kernel (`src/uxok/`) — the reference generator scans
`src/` and nothing else. This repo is the uxok kernel alone; any application built
on uxok lives in its own repository with its own docs and is **out of scope**.
Never add application tutorials, how-tos, explanation pages, or reference targets
here, and never point the reference generator at non-kernel source.

---

## Folder Structure and What Each Section Holds

### `index.md` — site landing page

Orients first-time visitors: what the project is, what it does, where to go. No
more than 400 words, no API detail. Must contain a one-paragraph project
description, links to all four Diátaxis sections, and a single quick-start snippet.

### `tutorials/` — learning-oriented

Leads a new user from zero to a working, verifiable result through one concrete
task. Assumes no prior knowledge. Every tutorial produces something the reader can
see or run.

- Files in `kebab-case`, one learning goal each. Split anything past ~1500 words.
- `getting-started.md` is the first tutorial and comes first in nav.

### `how-to/` — task-oriented

Assumes the reader already understands the project and wants one specific goal
done. One task per file. No theory — link to explanation instead.

- Filenames follow `how-to-<task>.md` in `kebab-case`, specific enough to be
  meaningful in isolation (e.g. `how-to-publish-events.md`).

### `explanation/` — understanding-oriented

Design decisions, architecture, tradeoffs, concepts. Discursive prose. No steps
(link to how-to), no API detail (link to reference). One concept per file,
`kebab-case`.

**This layer is the canonical home for system-level rationale** — why a primitive
or concept exists at all, the cross-cutting tradeoffs, the mental model a consumer
needs. When the same "why" could live here or in an internal mirror explanation
note, it belongs here and only here (see the altitude rule below).

#### Altitude: the two explanation layers do not overlap

Both this public layer and the internal mirror contain "explanation" content. They
are separated by **altitude**, not duplicated:

| Altitude | Layer | Owns | Audience |
|---|---|---|---|
| System | Public `explanation/` (this layer) | Why a primitive/concept exists at all; cross-cutting tradeoffs; the consumer's mental model | Builders *with* the framework |
| File | Internal `… — explanation.md` | Why one source file's internals are built this way: data structures, invariants, concurrency, policy enums | Maintainers *of* the kernel |

**The non-restatement rule:** content at one altitude appears **zero times** at the
other. A public explanation page never documents a single module's internal
mechanics; an internal note never restates the system-level "why."

This is one-directional by necessity. The internal mirror is in `exclude_docs` and
is not part of the built site, so a public page *cannot* link to an internal note —
the target does not exist on the website. The public layer is therefore canonical
for system-level rationale and stands alone. Internal notes may *name* a public
concept for orientation, but never point inward.

### `reference/` — information-oriented, fully generated

The complete API surface for every public module, class, function, and method.
**This folder is generated at build time — never create or edit `.md` files in it.**
The pages and their nav (`reference/SUMMARY.md`) are emitted on every build and are
not tracked in git.

The only input that drives the reference is **source docstrings**. The section is
exactly as complete as they are (see [Reference generation](#reference-generation)).

### Section index pages

Every `*/index.md` is a landing page, not content. It opens with a one-paragraph
orientation, lists every page in the section with a one-sentence description, and
contains no tutorial steps, API detail, or explanatory prose of its own.

---

## Authored vs Generated

Knowing what is generated prevents wasted effort.

| Content | Authored by | Updated when |
|---|---|---|
| Page *bodies* in `index.md`, `tutorials/`, `how-to/`, `explanation/` | Human | Manually, as features and designs change |
| `reference/` (pages + `SUMMARY.md`) | `gen_ref_pages.py`, at build time | Automatically — editing source docstrings is the only input |
| The whole-site **navigation** (`SUMMARY.md`) | `gen_ref_pages.py`, at build time | Automatically — discovered from the filesystem |
| The built site | CI on push to `main` | Automatically |

**Navigation is generated, not hand-written.** There is no `nav:` block in
`mkdocs.yml`. To add a page to the nav, create the Markdown file in its section and
give it an `# H1` — the generator titles the nav entry from that H1 and places it
automatically. The only manual lever is ordering: `gen_ref_pages.py` pins a few
stems to the front of a section (e.g. `getting-started` in tutorials) and sorts the
rest by title. Never expect to edit nav by hand.

**The reference section likewise requires zero manual maintenance.** Add a module
and its page appears; rename a function and the page follows. The whole human
obligation is keeping docstrings complete and accurate.

---

## Build Infrastructure (pointers — the real files are authoritative)

Do not reproduce these files' contents from memory or from any copy. When a fact
about the build is needed, read the real file.

- **`mkdocs.yml`** (repo root) — the complete build config: theme, plugins,
  `mkdocstrings` options, `exclude_docs`, and `validation`. Invariants an author
  relies on:
  - There is **no `nav:` block** — the whole nav is generated into `SUMMARY.md` by
    `gen_ref_pages.py` and read by `literate-nav`. Never add or hand-edit nav here.
  - `mkdocstrings` uses `docstring_style: google` and `filters: ["!^_"]`, so private
    names (leading `_`) never appear. Do not document privates — make the interface
    public or leave it out.
  - `validation:` makes broken nav references and broken links hard errors; `--strict`
    amplifies the rest. Author with working links.
  - The internal mirror folder and `agent_data/` are in `exclude_docs`.
- **`scripts/gen_ref_pages.py`** — the single source of truth for navigation. It
  generates both the reference pages and the site-wide `SUMMARY.md`
  ([Reference generation](#reference-generation)); its code is the file.
- **`.github/workflows/docs.yml`** — CI. A `--strict` build gate on every PR, plus a
  Pages deploy job that fires on `main`. Read it for what actually runs before
  asserting anything about deployment.
- **`pyproject.toml`** — the `[docs]` extra (install with `pip install -e ".[docs]"`)
  and the docstring gates `[tool.interrogate]` / `[tool.pydoclint]` that guard the
  reference's quality.

---

## Writing Standards for Public Docs

Public docs are read by external developers on a website. Standard markdown links,
never wikilinks.

> **Sentence-level voice is owned by the voice skill**
> (`.claude/skills/writing-style-SKILL/SKILL.md`). This blueprint sets structure,
> links, and per-section density; the voice skill governs how the prose reads
> (no throat-clearing, two passes per concept, analogies that do work). It applies
> in full to Tutorials and Explanation; for the generated `reference/` the density
> rule wins. Read the voice skill before writing any prose.

### Links

Standard markdown links for every cross-reference within the public layer:

```markdown
See the [architecture overview](../explanation/architecture-overview.md) for design rationale.
```

Never use wikilinks (`[[...]]`) in a public-layer file. Wikilinks are the internal
mirror's convention only.

### Voice and tense

- Present tense, active voice throughout.
- Second person (`you`) for tutorials and how-to guides.
- Third person for reference and explanation.

### Density by section type

| Section | Density | Length |
|---|---|---|
| Tutorial | Moderate — context helps learners | 500–1500 words/page |
| How-to | Tight — reader knows what they want | 200–600 words/page |
| Explanation | Discursive — depth is the point | As long as needed |
| Reference | Maximum — scanning, not reading | One object per heading |

### What belongs where

| Content | Section |
|---|---|
| Step-by-step guide for newcomers with a complete outcome | Tutorial |
| Numbered steps to accomplish one specific task | How-to |
| Why the system works the way it does, or when not to use a feature | Explanation |
| Parameters, return types, exceptions, signatures | Reference |

### Code blocks, admonitions, headings

- Always specify a language identifier; use Python for Python examples.
- Material admonitions (`!!! note`, `!!! warning`, `!!! tip`) for callouts, kept rare.
- `#` for the page title only (first line); `##` major sections; `###` subsections.
  Never skip levels. Sentence case: `## Error handling`, not `## Error Handling`.

---

## Reference generation and site nav

Three plugins cooperate: `gen-files` runs the script at build time, `literate-nav`
reads the generated `SUMMARY.md` files for navigation, and `section-index` binds each
`__init__` module's docs (and each section's `index.md`) to its section heading
instead of a sub-page.

`scripts/gen_ref_pages.py` is the engine and the canonical implementation — read the
real file before changing the pattern, and update this behavior list if it changes.
It does two things:

**Reference pages:**

1. Walks every `.py` file under `src/`.
2. Writes `reference/<module/path>.md`, each containing a single `:::` mkdocstrings
   directive.
3. Skips `__main__` modules entirely.
4. Collapses `__init__` modules onto their parent section (section-index behavior).
5. Writes `reference/SUMMARY.md` as the nested literate-nav input.

**Whole-site nav:** it then discovers the authored sections (`tutorials/`, `how-to/`,
`explanation/`) on disk, titles each page from its `# H1`, orders them (pinned stems
first, then by title), and writes the root `SUMMARY.md` that drives the entire
navigation — with `reference/` linked by trailing slash so the API nav nests in.

None of these files are written to disk or tracked in git; they exist only during
the build.

### Docstrings are the whole input

Every public function, class, method, and module **must** have a complete
Google-style docstring. This is a hard requirement.

```python
def publish(self, event: Event) -> None:
    """Publish an event to all registered subscribers.

    Args:
        event: The event to publish. Must have a non-empty `type` attribute.

    Raises:
        ValueError: If `event.type` is empty.

    Example:
        ```python
        await bus.publish(Event(type="user.created", data={"id": 42}))
        ```
    """
```

A public interface with no docstring produces a bare page showing only the
signature — a gap equivalent to having no reference page at all. A bare one-line
docstring on a public interface is also a gap, not a completion.

---

## Local Build

```bash
mkdocs serve            # live reload; re-runs gen_ref_pages.py on every change
mkdocs build            # static build to site/
mkdocs build --strict   # treats warnings as errors — the pre-merge gate
```

`mkdocs build --strict` is what catches broken cross-references, missing nav
entries, and mkdocstrings failures that `serve` may not surface. Run it before
merging public-doc changes.

---

## Conformance Checklist

For auditing structural and formatting conformance of the public layer. Items about
build wiring check the **real files**, never a copy.

**Structure**
- [ ] `docs/index.md` exists and links all four sections
- [ ] `tutorials/index.md`, `how-to/index.md`, `explanation/index.md` all exist
- [ ] Every authored page has exactly one `# H1` (the generator titles its nav entry from it)
- [ ] `reference/` contains no manually authored `.md` files
- [ ] No public content inside the internal mirror folder, and vice versa

**Build wiring (verify against the live files)**
- [ ] `mkdocs.yml`: `gen-files`, `literate-nav`, `section-index`, `mkdocstrings`
      present; **no `nav:` block** (nav is generated); mirror folder and
      `agent_data/` in `exclude_docs`; `filters: ["!^_"]` set; `validation:` present
- [ ] `scripts/gen_ref_pages.py` matches the behavior above (reference pages +
      site-wide `SUMMARY.md`)
- [ ] `pyproject.toml` `[docs]` extra installs the toolchain; `[tool.interrogate]`
      and `[tool.pydoclint]` present
- [ ] `mkdocs build --strict` completes without errors

**Docstrings**
- [ ] Every public function, class, method, and module has a Google-style docstring
- [ ] No public interface has a bare or one-line docstring lacking `Args`/`Returns`
- [ ] Docstring examples are correct and runnable

**Writing**
- [ ] No wikilinks anywhere in the public layer; markdown links for cross-references
- [ ] Present tense, active voice throughout
- [ ] Code blocks specify a language; headings use sentence case; `#` only for the title
- [ ] Section index pages are orientation + list only, no content of their own
- [ ] Every tutorial produces a concrete, verifiable result
- [ ] How-to files use numbered steps, one action per step
- [ ] Reference content carries no explanatory prose; explanation carries no steps or API detail
