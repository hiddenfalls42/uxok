export const meta = {
  name: 'doc-update',
  description:
    'Change-driven UPDATE of the Clave public docs: map changed source files to the doc pages that document them, then reconcile each page with minimal edits (preserve correct content and H1), verify, and report. Surgical and cheap — the counterpart to the full doc-regenerate.',
  whenToUse:
    'Use after editing src/clave to bring the affected docs back into line — not for a from-scratch rebuild (that is doc-regenerate). WRITING IS OPT-IN: pass {apply: true} to edit pages; without it (or if args fail to thread) it is a safe dry-run that reports the plan and writes nothing. Args {apply?: bool, changedSources?: ["src/clave/..."], since?: "<git-ref>", pages?: [{filename,title,changedSources}]}. If changedSources/pages are omitted, the Map agent derives the change set with git (vs `since`, default the working tree against HEAD). Workflow scripts have NO filesystem access — git and disk reads happen inside agents.',
  phases: [
    { title: 'Map', detail: 'one agent maps changed source files -> the doc pages that document them (schema list)' },
    { title: 'Reconcile', detail: 'pipeline: per affected page, technical-writer makes minimal edits -> verifier checks -> 1 retry' },
  ],
}

// --- args + guards ---
// In this runtime the `args` global arrives as a JSON STRING, not the parsed
// object the tool contract implies. Parse defensively — tolerate a string, an
// already-parsed object, or nothing. (This was the root cause of the workflow
// ignoring apply/changedSources on earlier runs.)
const A = typeof args === 'string' && args.trim() ? JSON.parse(args) : args && typeof args === 'object' ? args : {}

const seededPages = Array.isArray(A.pages) ? A.pages : null
const changedSources = Array.isArray(A.changedSources) ? A.changedSources : null
const since = (typeof A.since === 'string' && A.since) || null

// Fail-safe: writing is OPT-IN — a missing/unthreaded flag must never default to
// editing files. Without apply:true this is a dry-run.
const apply = A.apply === true
const dryRun = !apply
log(`doc-update: mode=${apply ? 'APPLY (edits pages)' : 'dry-run (no writes)'}; changedSources=${changedSources ? changedSources.length : 0}, seededPages=${seededPages ? seededPages.length : 0}`)

const safePath = (p) => typeof p === 'string' && p.startsWith('docs/') && !p.includes('..') && !p.startsWith('-')
const safeSrc = (p) => typeof p === 'string' && p.startsWith('src/') && !p.includes('..') && !p.startsWith('-')
if (changedSources) for (const s of changedSources) if (!safeSrc(s)) throw new Error(`Unsafe changedSources entry ${JSON.stringify(s)} — must be a src/ path`)
if (since && (since.includes('..') || since.startsWith('-'))) throw new Error(`Unsafe since ref ${JSON.stringify(since)}`)

const SKILLS =
  'the PUBLIC blueprint .claude/skills/public-document-blueprint-SKILL/SKILL.md and the voice skill .claude/skills/writing-style-SKILL/SKILL.md'

// ---------------------------------------------------------------- schemas

const MAP_SCHEMA = {
  type: 'object',
  required: ['pages'],
  properties: {
    pages: {
      type: 'array',
      items: {
        type: 'object',
        required: ['filename', 'title', 'changedSources', 'focus'],
        properties: {
          filename: { type: 'string', description: 'repo-relative path under docs/ of an affected page' },
          title: { type: 'string', description: 'the EXISTING # H1 text — must be preserved so the generated nav is unchanged' },
          changedSources: { type: 'array', items: { type: 'string' }, description: 'the changed src/ files this page documents' },
          focus: { type: 'string', description: 'what specifically to re-check on this page given the change (API/signature/behavior)' },
        },
      },
    },
    changeSet: { type: 'array', items: { type: 'string' }, description: 'the changed src/ files considered' },
    notes: { type: 'string', description: 'pages deliberately not included and why, or "none"' },
  },
}

const UPDATE_SCHEMA = {
  type: 'object',
  required: ['pagePath', 'changed', 'ok'],
  properties: {
    pagePath: { type: 'string' },
    changed: { type: 'boolean', description: 'true if any edit was made; false if the page was already accurate' },
    edits: { type: 'array', items: { type: 'string' }, description: 'one phrase per change made' },
    changelogLine: { type: 'string', description: 'the DOCS-CHANGELOG line appended, or "" if nothing changed' },
    docstringsEdited: { type: 'array', items: { type: 'string' } },
    ok: { type: 'boolean', description: 'false if the change could not be reconciled (e.g. source ambiguous)' },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['conforms', 'issues'],
  properties: {
    conforms: { type: 'boolean' },
    issues: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'what'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'minor'] },
          what: { type: 'string' },
          fix: { type: 'string' },
        },
      },
    },
  },
}

// ---------------------------------------------------------------- directives

const mapDirective = () => {
  const sourceClause = changedSources
    ? `The changed source files are:\n  ${changedSources.join('\n  ')}`
    : `Determine the changed source files yourself with git: run \`git diff --name-only ${since ? since + ' ' : ''}-- src/clave\` (and \`git diff --name-only --cached -- src/clave\` for staged) to list what changed${since ? ` since ${since}` : ' in the working tree vs HEAD'}. Consider only src/clave/*.py.`
  return `Read-only mapping. WRITE NOTHING.

${sourceClause}

For each changed source file, decide which PUBLIC doc pages document it and therefore
need re-checking. Map source -> pages (the inverse of the page->source mapping):
- a subsystem dir (events/, hooks/, plugin/, timing/, core/, registry/) -> its explanation
  page and the how-to pages for tasks in that subsystem;
- a change to the public surface (src/clave/__init__.py __all__, a public signature/default)
  -> docs/index.md quick-start and any tutorial/how-to that calls it.
Only include pages whose CONTENT could be affected. For each, read its current '# H1'
(preserve it verbatim) and state in 'focus' exactly what to re-verify given the change.
Exclude the generated reference/ (docstrings drive it), the internal docs/clave/ mirror,
and docs/manifests/. Return the affected pages; if nothing public is affected, return an
empty pages array and say so in notes.`
}

const updateDirective = (p, fixHint) => `You are the technical-writer. UPDATE exactly ONE existing public page — do NOT rewrite it from scratch.

Step 1 — load ${SKILLS}. This is the PUBLIC layer.
Step 2 — read the CURRENT page ${p.filename} AND the changed source it documents:
  ${(p.changedSources || []).join('\n  ')}
What to re-verify on this page: ${p.focus}

Step 3 — reconcile with MINIMAL edits:
  - Change only what the source change makes inaccurate or newly relevant. Preserve every
    sentence that is still correct, the page's structure, and its '# H1' (keep it identical
    to "${p.title}" — the nav is generated from the H1).
  - Keep the page conformant: markdown links only, language id on every code block,
    sentence-case headings, present tense, the blueprint's density for this page type.
  - You MAY edit Google-style docstrings on the public surface (docstrings ONLY).
  - If the page is ALREADY accurate against the changed source, change NOTHING and report changed:false.

Step 4 — if (and only if) you changed something, append one line to docs/DOCS-CHANGELOG.md
(create if absent) per the blueprint format.
${fixHint || ''}
Re-read the page to confirm. Return the structured result.`

const verifyDirective = (p) => `REVIEW ONLY. WRITE NOTHING.

Check ${p.filename} against ${SKILLS} and the changed source (${(p.changedSources || []).join(', ')}).
Re-verify specifically: ${p.focus}. Mark a 'blocker' for: H1 changed from "${p.title}"; any wikilink;
a code block missing a language id; remaining factual drift from the changed source; or a voice
violation. conforms=true only if zero blockers. Give a concrete 'fix' for each issue.`

// ---------------------------------------------------------------- per-page loop

const MAX_RETRIES = 1

async function reconcilePage(p) {
  let lastVerify = null
  let updateResult = null
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const fixHint =
      lastVerify && lastVerify.issues
        ? '\nThis is a RETRY. The verifier flagged blockers — fix exactly these:\n' +
          lastVerify.issues.filter((i) => i.severity === 'blocker').map((i) => `- ${i.what}${i.fix ? ' -> ' + i.fix : ''}`).join('\n')
        : ''

    updateResult = await agent(updateDirective(p, fixHint), {
      agentType: 'technical-writer',
      schema: UPDATE_SCHEMA,
      phase: 'Reconcile',
      label: `update:${p.filename}#${attempt}`,
    })

    if (!updateResult) return { page: p, status: 'update-failed', detail: 'writer returned null', attempts: attempt + 1 }
    if (updateResult.ok === false) return { page: p, status: 'update-failed', detail: 'could not reconcile', updateResult, attempts: attempt + 1 }
    if (updateResult.changed === false) return { page: p, status: 'unchanged', updateResult, attempts: attempt + 1 }

    lastVerify = await agent(verifyDirective(p), { schema: VERIFY_SCHEMA, phase: 'Reconcile', label: `verify:${p.filename}#${attempt}` })
    const blockers = lastVerify && lastVerify.issues ? lastVerify.issues.filter((i) => i.severity === 'blocker') : [{ what: 'verify returned null' }]
    if (lastVerify && lastVerify.conforms && blockers.length === 0) {
      return { page: p, status: 'updated', updateResult, attempts: attempt + 1 }
    }
  }
  return { page: p, status: 'nonconforming', updateResult, blockers: lastVerify ? lastVerify.issues : [], attempts: MAX_RETRIES + 1 }
}

// ---------------------------------------------------------------- run

// Phase: Map (or trust an explicit page list)
let worklist
if (seededPages) {
  worklist = seededPages.filter((p) => p && safePath(p.filename))
  log(`Using ${worklist.length} page(s) seeded from args (Map skipped)`)
} else {
  const mapped = await agent(mapDirective(), { schema: MAP_SCHEMA, phase: 'Map', label: 'map-changes' })
  if (!mapped || !Array.isArray(mapped.pages)) throw new Error('Map returned no result')
  worklist = mapped.pages.filter((p) => safePath(p.filename))
  if (mapped.changeSet) log(`Change set: ${mapped.changeSet.join(', ') || '(none)'}`)
  if (mapped.notes) log(`Map notes: ${mapped.notes}`)
}

if (worklist.length === 0) {
  return { mode: dryRun ? 'dry-run' : 'update', affectedPages: 0, message: 'No public doc pages affected by the change set.' }
}
log(`Affected pages: ${worklist.length} — ${worklist.map((p) => p.filename).join(', ')}`)

if (dryRun) {
  return { mode: 'dry-run', affectedPages: worklist.length, pages: worklist }
}

// Phase: Reconcile
const results = (await pipeline(worklist, (p) => reconcilePage(p))).filter(Boolean)

return {
  mode: 'update',
  affectedPages: worklist.length,
  updated: results.filter((r) => r.status === 'updated').map((r) => r.page.filename),
  unchanged: results.filter((r) => r.status === 'unchanged').map((r) => r.page.filename),
  nonconforming: results.filter((r) => r.status === 'nonconforming').map((r) => ({ page: r.page.filename, blockers: r.blockers })),
  updateFailed: results.filter((r) => r.status === 'update-failed').map((r) => ({ page: r.page.filename, detail: r.detail })),
  changelogSummary: results.flatMap((r) => (r.updateResult && r.updateResult.changelogLine ? [r.updateResult.changelogLine] : [])),
  docstringsEdited: [...new Set(results.flatMap((r) => (r.updateResult && r.updateResult.docstringsEdited) || []))],
  nextSteps: 'Run `mkdocs build --strict`, then `git diff docs/ src/clave/` to review before committing.',
}
