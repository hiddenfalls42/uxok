#!/usr/bin/env python3
"""Launch the doc-update Workflow to reconcile public docs with changed source.

Thin launcher only — all orchestration lives in
``.claude/workflows/doc-update.js``. This computes the changed ``src/uxok``
files with git (the workflow has no filesystem access) and passes them so the
workflow maps each to the doc pages that document it and reconciles only those
pages with minimal edits.

Writing is OPT-IN. By default this is a dry-run (map + planned pages, no writes);
pass ``--apply`` to actually edit pages.

Usage:
    update_docs.py                       # DRY-RUN: working-tree src changes vs HEAD -> affected pages
    update_docs.py --apply               # reconcile the affected pages (edits them)
    update_docs.py --since origin/main   # change set = src changes since a ref
    update_docs.py --print-directive     # print the directive and exit (no claude call)

Stdlib only. Shells out to ``git`` (to find the change set) and ``claude``.
The headless ``claude -p`` -> Workflow-tool path is environment-dependent; see
the RISK note in scripts/regen_docs.py.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".claude" / "workflows" / "doc-update.js"


def changed_sources(since: str | None) -> list[str]:
    """Return changed src/uxok/*.py paths: vs ``since`` if given, else working tree + staged vs HEAD."""
    specs = [["diff", "--name-only", since] if since else ["diff", "--name-only"]]
    if not since:
        specs.append(["diff", "--name-only", "--cached"])
    found: set[str] = set()
    for spec in specs:
        out = subprocess.run(
            ["git", *spec, "--", "src/uxok"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout
        found.update(p for p in out.splitlines() if p.endswith(".py"))
    return sorted(found)


def build_directive(payload: dict[str, object]) -> str:
    rel = WORKFLOW.relative_to(REPO).as_posix()
    return (
        f"Run the Workflow tool with the workflow script at {rel} and pass it these "
        f"args (JSON): {json.dumps(payload)}. Do not do any documentation work "
        "yourself — the workflow dispatches the technical-writer and a verifier. "
        "When it returns, print its JSON return object verbatim."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--apply", action="store_true", help="edit pages (default: dry-run, write nothing)")
    ap.add_argument("--since", help="git ref to diff against (default: working tree + staged vs HEAD)")
    ap.add_argument("--print-directive", action="store_true", help="print the directive and exit (no claude call)")
    ap.add_argument("--claude-bin", default="claude", help="path to the claude CLI")
    args = ap.parse_args(argv)

    if not WORKFLOW.exists():
        print(f"workflow not found: {WORKFLOW}", file=sys.stderr)
        return 2

    sources = changed_sources(args.since)
    if not sources:
        print("No changed src/uxok/*.py files — nothing to reconcile.")
        return 0
    print(f"changed sources ({len(sources)}): {', '.join(sources)}", file=sys.stderr)

    payload: dict[str, object] = {"apply": bool(args.apply), "changedSources": sources}
    if args.since:
        payload["since"] = args.since
    directive = build_directive(payload)

    if args.print_directive:
        print(directive)
        return 0
    if shutil.which(args.claude_bin) is None:
        print(f"`{args.claude_bin}` not found on PATH", file=sys.stderr)
        return 2

    cmd = [args.claude_bin, "-p", directive]
    print(f"$ {args.claude_bin} -p <directive>")
    return subprocess.run(cmd, cwd=REPO).returncode


if __name__ == "__main__":
    raise SystemExit(main())
