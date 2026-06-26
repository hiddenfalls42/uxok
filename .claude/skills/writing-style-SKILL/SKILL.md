---
name: writing-voice
description: Use this skill whenever any agent writes or evaluates the PROSE inside developer or public documentation notes for this project — the sentence-level voice, not the structure. Triggers include: writing an Overview, Explanation, How-to, Tutorial, or any narrative passage; reviewing a note for tone, density, analogy use, or list-vs-prose choices; resolving any question about how a sentence, paragraph, analogy, or list should read. This skill governs voice only. Structure, placement, note types, naming, and linking are owned by the developer and public blueprint skills — read those for layout decisions. Where this skill and a blueprint skill appear to conflict on density, the blueprint wins (see Precedence).
---

# Writing Voice

This skill is the single source of truth for the sentence-level voice of every
documentation note in this project. It governs how prose reads — not where files
go or what sections they contain. Structure, note types, naming, placement, and
linking belong to the two blueprint skills (developer mirror, public website);
this skill never overrides them.

The voice here is derived from one person's technical writing. Reproduce it.

---

## Precedence — read this before applying any rule below

This skill governs **prose-bearing notes only**. It does not loosen the density
rules a blueprint skill imposes on lookup surfaces. The split is firm:

| Surface | Voice rules that apply |
|---|---|
| Explanation notes, public Explanation pages, Tutorials, How-to prose | **All of them.** This is teaching prose — the full voice. |
| Overview notes | **Partial.** No throat-clearing and one-idea-per-sentence apply. Two-passes and analogies do not — an Overview is 1–3 tight orienting paragraphs, not a lesson. |
| Reference notes | **Two only:** no throat-clearing, and short declarative sentences. Everything else is suppressed. The blueprint's "dense, scannable, no explanatory prose" rule wins outright. No second pass, no analogies, no mechanism-why. |
| Docstrings (public source) | **Two only,** same as Reference. Google-style terseness wins. A docstring teaches nothing; it states. |

If a rule below would add a sentence to a Reference note or a docstring, you have
mis-scoped it. Density wins there, every time.

Two rules are **universal** — they improve every surface, including Reference and
docstrings: *no throat-clearing* and *one idea per sentence*. Everything else is
gated by the table above.

---

## Core Principles

**No throat-clearing.** The first sentence is the thing itself. Never introduce
what you are about to say. Open on the subject, not on a frame around it.

**Every concept gets two passes.** State a tight definition, then immediately
restate it a second way — an appositive, a rephrasing, or an analogy. The reader
always has a foothold before the next idea arrives. *(Prose-bearing notes only —
see Precedence.)*

**Mechanism over abstraction.** Explain *why* something behaves as it does at the
physical or logical level, briefly, in plain language. The smallest necessary
dose of why — enough to ground the behavior, never a digression.

---

## Sentence Construction

- Short declarative sentences. One idea per sentence.
- Definitions restate the concept a second way immediately: *"Voltage is the
  difference in charge between two points. It is also called potential difference."*
- Subordinate clauses explain *why*, they do not hedge: *"...because drift is all
  of the electrons pushing forward"* — parenthetical, casual, mechanistic.
- Appositive compression for restatements: *"Power is the rate of work over time.
  It is typically expressed as..."*

---

## Analogies

Analogies are first-class explanations, not footnotes. State them flatly,
immediately after the definition, without hedging.

```
✓  "Voltage is to electricity as gravitational potential energy is to physics."
✗  "You could think of voltage as being similar to gravitational potential energy."
```

**An analogy must do work.** Every analogy you introduce is followed through with
at least one concrete consequence that uses it. A stated-and-abandoned analogy is
wasted ink.

```
✗  "Imagine voltage difference as height." [no follow-through]
✓  "Imagine voltage difference as height. Just as water flows downhill, current
    flows from high to low potential."
```

Analogies belong to teaching prose. Never reach for one in a Reference note or a
docstring.

---

## Lists

**Bulleted lists** — unordered attributes or properties of a concept. Each bullet
is one fact, tight, no elaboration unless mechanism demands it.

**Numbered lists** — ordered rules, conventions, or sequences. The distinction is
firm: bullets are attributes, numbers are order or priority.

**Prose instead of a list when items build on each other.** If items have a
logical progression or depend on one another, write prose with connective tissue.
Listing related items flattens their relationship. Ask: are these items truly
parallel and independent? If not, write prose.

```
✗  Properties of charge:
   1. Charge can be positive or negative
   2. Like charges repel
   3. Opposite charges attract

✓  Charge can be either positive or negative. Like charges repel, while opposite
   charges attract.
```

---

## Emphasis & Punctuation

**Exclamation marks** — only for the genuinely surprising or counterintuitive,
never decorative: *"...about 12 orders of magnitude faster than the actual
transport velocity of the electrons in those wires!"* In Reference notes and
docstrings, do not use them at all.

**Bold** — the first use of the note's own defined term, and critical distinctions
only. Never for general emphasis.

**Italics** — notation conventions and mild stress within prose. Sparse.
