export const meta = {
  name: 'doc-regenerate',
  description:
    'Full regenerate of the Clave public docs: scout the existing information architecture, then per-page overwrite (write -> verify -> bounded retry) by the technical-writer, then one documentation-auditor handoff.',
  whenToUse:
    'Invoked headless via scripts/regen_docs.py (claude -p), or directly with the Workflow tool. WRITING IS OPT-IN: pass {apply: true} to overwrite pages; without it (or if args fail to thread) the run is a safe dry-run that writes nothing. Optional args {apply?: bool, sections?: ["how-to"...], pages?: [{section,filename,title,pageType,sourceRefs}]}. Workflow scripts have NO filesystem access: the work-list comes from the Scout agent (or args.pages), never from disk here.',
  phases: [
    { title: 'Scout', detail: 'one read-only agent enumerates the existing public pages -> schema work-list (IA preserved)' },
    { title: 'Regenerate', detail: 'pipeline: per page, technical-writer overwrites -> verifier checks -> up to 2 retries' },
    { title: 'Audit', detail: 'one documentation-auditor pass over the public layer -> archived handoff path' },
  ],
}

// --- args + guards (mirror the path-safety in the official portfolio-assess.js) ---
const ALLOWED_SECTIONS = ['root', 'tutorials', 'how-to', 'explanation']

// In this runtime the `args` global arrives as a JSON STRING, not the parsed
// object the tool contract implies. Parse defensively — tolerate a string, an
// already-parsed object, or nothing. (This was the root cause of the first run
// ignoring the dry-run flag and overwriting every page.)
const A = typeof args === 'string' && args.trim() ? JSON.parse(args) : args && typeof args === 'object' ? args : {}

const sections = A.sections || null
const seeded = Array.isArray(A.pages) ? A.pages : null

// Fail-safe: writing is OPT-IN. If apply is not explicitly true (or args did not
// thread), run a safe dry-run rather than a destructive full regenerate. Never
// let a missing flag default to overwriting every page.
const apply = A.apply === true
const dryRun = !apply
log(`doc-regenerate: mode=${apply ? 'APPLY (overwrites pages)' : 'dry-run (no writes)'}; sections=${sections ? sections.join(',') : 'all'}, seededPages=${seeded ? seeded.length : 0}`)

if (sections) {
  if (!Array.isArray(sections)) throw new Error('args.sections must be an array of section names')
  for (const s of sections) {
    if (!ALLOWED_SECTIONS.includes(s)) throw new Error(`Unknown section ${JSON.stringify(s)} (allowed: ${ALLOWED_SECTIONS.join(', ')})`)
  }
}

// Filenames land inside agent prompts and are written to. Reject traversal and
// flag-shaped values, and confine every target to the public docs tree.
const safePath = (p) =>
  typeof p === 'string' && p.startsWith('docs/') && !p.includes('..') && !p.startsWith('-')

const PUBLIC_LAYER = 'docs/index.md, docs/tutorials/, docs/how-to/, docs/explanation/'
const SKILLS =
  'the PUBLIC blueprint .claude/skills/public-document-blueprint-SKILL/SKILL.md and the voice skill .claude/skills/writing-style-SKILL/SKILL.md'

// ---------------------------------------------------------------- schemas

const WORKLIST_SCHEMA = {
  type: 'object',
  required: ['pages'],
  properties: {
    pages: {
      type: 'array',
      items: {
        type: 'object',
        required: ['section', 'filename', 'title', 'pageType', 'sourceRefs'],
        properties: {
          section: { type: 'string', enum: ['root', 'tutorials', 'how-to', 'explanation'] },
          filename: { type: 'string', description: 'repo-relative path under docs/, e.g. docs/how-to/how-to-publish-events.md' },
          title: { type: 'string', description: 'the EXISTING # H1 text — must be preserved so the generated nav is unchanged' },
          pageType: { type: 'string', enum: ['root-index', 'section-index', 'tutorial', 'how-to', 'explanation'] },
          sourceRefs: { type: 'array', items: { type: 'string' }, description: 'src/clave/ paths (or README/manifest) this page is regenerated from' },
        },
      },
    },
    notes: { type: 'string', description: 'any IA anomaly found (orphan page, missing H1, etc.)' },
  },
}

const WRITE_SCHEMA = {
  type: 'object',
  required: ['pagePath', 'written', 'changelogLines', 'ok'],
  properties: {
    pagePath: { type: 'string', description: 'the page it overwrote (echo of the directive target)' },
    written: { type: 'array', items: { type: 'string' }, description: 'every file path written/edited (the page + any docstring source files)' },
    changelogLines: { type: 'array', items: { type: 'string' } },
    docstringsEdited: { type: 'array', items: { type: 'string' } },
    candidateWork: { type: 'array', items: { type: 'string' } },
    assumptions: { type: 'string' },
    ok: { type: 'boolean', description: 'false if the source was too unclear to document truthfully (no page written)' },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['conforms', 'issues'],
  properties: {
    conforms: { type: 'boolean', description: 'true only if the page is publishable as-is against both skills and the source' },
    issues: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'what'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'minor'] },
          what: { type: 'string' },
          fix: { type: 'string', description: 'the concrete correction the writer should make on retry' },
        },
      },
    },
  },
}

const AUDIT_SCHEMA = {
  type: 'object',
  required: ['handoffPath'],
  properties: { handoffPath: { type: 'string', description: 'absolute path to the handoff file you wrote' } },
}

// ---------------------------------------------------------------- directives

const scoutDirective = () => `Read-only enumeration. WRITE NOTHING, create no files.

Build the work-list for a full regenerate of the Clave PUBLIC documentation. The
information architecture must be PRESERVED exactly — same sections, same filenames,
same H1 titles. For each authored public page (do NOT include the generated
reference/ section, the internal docs/clave/ mirror, or docs/manifests/):

1. Glob ${PUBLIC_LAYER}.
2. For each page read its first '# H1' line — that text is its 'title' and must be
   preserved verbatim (the site nav is generated from the H1 by scripts/gen_ref_pages.py).
3. Map each page to the src/clave/ source it should be regenerated from ('sourceRefs'):
   - explanation/ pages -> the matching subsystem dir (events/, hooks/, plugin/, timing/,
     core/_capability_system.py, core/_state_manager.py; architecture-overview -> core/ + protocols/;
     framework-philosophy -> docs/manifests/FRAMEWORK_PHILOSOPHY.md + src/clave/__init__.py)
   - how-to/ pages -> the owning public-API module of the task the filename names
   - tutorials/getting-started -> end-to-end: Core (core/), Plugin (plugin/), event/hook decorators
   - */index.md (section-index) -> its sibling pages in that section
   - docs/index.md (root-index) -> README.md + src/clave/__init__.py (the 11-name __all__)

Return the structured work-list. Note any anomaly (page with no H1, orphan, etc.).`

const writeDirective = (p, fixHint) => `You are the technical-writer. FULL REGENERATE of exactly ONE public page. This is the PUBLIC layer.

Step 1 — load in full before writing: ${SKILLS}.
Apply the FULL voice — this is ${p.pageType} prose (NOT reference, NOT a docstring).

Step 2 — ground truth: read these source files before writing a word:
  ${(p.sourceRefs || []).join('\n  ')}
The public API surface is the 11 names in src/clave/__init__.py's __all__. Document only the public surface.

Step 3 — rewrite the WHOLE body of: ${p.filename}
  - OVERWRITE the entire file. This is a from-scratch regeneration, NOT a patch.
  - Keep EXACTLY ONE '# H1', and keep its text identical to: "${p.title}"
    (the site nav is generated from the H1 by scripts/gen_ref_pages.py — changing it reorders/renames nav).
  - Obey the blueprint's density + section rules for a ${p.pageType} page; markdown links only, NEVER wikilinks;
    every code block gets a language id; sentence-case headings; present tense.
  - how-to: numbered steps, one action each, no theory. tutorial: a concrete runnable result.
    explanation: system-level "why", no steps/no API detail. *-index: orientation paragraph + one line per sibling page only.
  - You MAY edit Google-style docstrings on the public surface you touch (docstrings ONLY, never logic/signatures).

Step 4 — append one line per file changed to docs/DOCS-CHANGELOG.md (create if absent), per the blueprint format.
${fixHint || ''}
Do exactly this one page. Re-read what you wrote to confirm it landed. Then return the structured result.`

const verifyDirective = (p) => `REVIEW ONLY. WRITE NOTHING, create/modify no files.

Judge whether ${p.filename} is publishable as-is against ${SKILLS} and the source
(${(p.sourceRefs || []).join(', ')}). Read the page and the sources.

Mark a 'blocker' for any of: the '# H1' differs from "${p.title}"; any wikilink '[[...]]';
a code block missing a language id; content at the wrong altitude or in the wrong Diataxis
section; factual drift from the source; or a voice violation on this prose surface
(throat-clearing opener, a defined concept with no second pass, an analogy stated but never
followed through). 'minor' = a nit that does not block publication.

Set conforms=true ONLY if there are zero blockers. For each issue give a concrete 'fix'.`

const auditDirective = () => `You are the documentation-auditor. Run a BROAD audit of the PUBLIC layer only
(${PUBLIC_LAYER}) against ${SKILLS}, plus code-truth in src/clave/. Write your handoff file under
docs/agent_data/doc-auditor/handoffs/ exactly per your contract. Return the absolute handoff path.`

// ---------------------------------------------------------------- per-page loop

const MAX_RETRIES = 2

async function regeneratePage(p) {
  let lastVerify = null
  let writeResult = null
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const fixHint =
      lastVerify && lastVerify.issues
        ? '\nThis is a RETRY. The verifier flagged blockers — fix exactly these and rewrite the page:\n' +
          lastVerify.issues
            .filter((i) => i.severity === 'blocker')
            .map((i) => `- ${i.what}${i.fix ? ' -> ' + i.fix : ''}`)
            .join('\n')
        : ''

    writeResult = await agent(writeDirective(p, fixHint), {
      agentType: 'technical-writer',
      schema: WRITE_SCHEMA,
      phase: 'Regenerate',
      label: `write:${p.filename}#${attempt}`,
    })

    if (!writeResult) return { page: p, status: 'write-failed', detail: 'writer returned null', attempts: attempt + 1 }
    if (writeResult.ok === false) return { page: p, status: 'write-failed', detail: writeResult.assumptions || 'source unclear', writeResult, attempts: attempt + 1 }

    lastVerify = await agent(verifyDirective(p), {
      schema: VERIFY_SCHEMA,
      phase: 'Regenerate',
      label: `verify:${p.filename}#${attempt}`,
    })

    const blockers = lastVerify && lastVerify.issues ? lastVerify.issues.filter((i) => i.severity === 'blocker') : [{ what: 'verify returned null' }]
    if (lastVerify && lastVerify.conforms && blockers.length === 0) {
      return { page: p, status: 'conforming', writeResult, attempts: attempt + 1 }
    }
  }
  return { page: p, status: 'nonconforming', writeResult, blockers: lastVerify ? lastVerify.issues : [], attempts: MAX_RETRIES + 1 }
}

// ---------------------------------------------------------------- run

// Phase: Scout (or trust the CLI-seeded list)
let worklist
if (seeded) {
  worklist = seeded.filter((p) => p && safePath(p.filename))
  log(`Using ${worklist.length} page(s) seeded from args (Scout skipped)`)
} else {
  const scouted = await agent(scoutDirective(), { schema: WORKLIST_SCHEMA, phase: 'Scout', label: 'scout-worklist' })
  if (!scouted || !Array.isArray(scouted.pages)) throw new Error('Scout returned no work-list — cannot regenerate')
  worklist = scouted.pages.filter((p) => safePath(p.filename))
  if (scouted.notes) log(`Scout notes: ${scouted.notes}`)
}
if (sections) worklist = worklist.filter((p) => sections.includes(p.section))
if (worklist.length === 0) throw new Error('Work-list is empty after filtering — nothing to regenerate')
log(`Work-list: ${worklist.length} page(s) — ${worklist.map((p) => p.filename).join(', ')}`)

if (dryRun) {
  return { mode: 'dry-run', sectionsScoped: sections || 'all', planned: worklist.length, pages: worklist }
}

// Phase: Regenerate (bounded concurrency via pipeline; sequential write->verify per page)
const results = await pipeline(worklist, (p) => regeneratePage(p))

// Phase: Audit (one broad pass; wrap the auditor's one-line path in a schema field)
const audit = await agent(auditDirective(), { agentType: 'documentation-auditor', schema: AUDIT_SCHEMA, phase: 'Audit', label: 'final-audit' })

// Synthesize
const ok = results.filter(Boolean)
return {
  mode: 'regenerate',
  sectionsScoped: sections || 'all',
  planned: worklist.length,
  regenerated: ok.filter((r) => r.status === 'conforming').map((r) => r.page.filename),
  stillNonconforming: ok
    .filter((r) => r.status === 'nonconforming')
    .map((r) => ({ page: r.page.filename, attempts: r.attempts, blockers: r.blockers })),
  writeFailed: ok.filter((r) => r.status === 'write-failed').map((r) => ({ page: r.page.filename, detail: r.detail })),
  changelogSummary: ok.flatMap((r) => (r.writeResult && r.writeResult.changelogLines) || []),
  docstringsEdited: [...new Set(ok.flatMap((r) => (r.writeResult && r.writeResult.docstringsEdited) || []))],
  candidateWork: [...new Set(ok.flatMap((r) => (r.writeResult && r.writeResult.candidateWork) || []))],
  auditHandoffPath: audit && audit.handoffPath ? audit.handoffPath : '(auditor returned no path — check newest file under docs/agent_data/doc-auditor/handoffs/)',
  nextSteps: 'Run `mkdocs build --strict`, then `git diff docs/ src/clave/` to review before committing.',
}
