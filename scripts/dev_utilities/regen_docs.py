#!/usr/bin/env python3
"""Launch the doc-regenerate Workflow to fully regenerate uxok's public docs.

Thin launcher only — all orchestration lives in
``.claude/workflows/doc-regenerate.js``. This script shells out to ``claude -p``
with a directive that runs that workflow (the technical-writer, a verifier, and
the documentation-auditor do the work), optionally scoped to certain sections or
in dry-run.

Writing is OPT-IN. By default this is a dry-run (scout + planned work-list, no
writes); pass ``--apply`` to actually overwrite pages. This is deliberate: a
destructive full regenerate must never be the default-when-a-flag-is-missing.

Usage:
    regen_docs.py                              # DRY-RUN: scout + planned work-list, write nothing
    regen_docs.py --apply                      # regenerate every public page (overwrites)
    regen_docs.py --apply --sections how-to explanation
    regen_docs.py --print-directive            # print the directive and exit (no claude call, zero tokens)

Stdlib only. Shells out to ``claude``.

RISK: that ``claude -p`` reliably triggers the Workflow tool with a named workflow
file + an args object is environment-dependent and unproven in this repo. Validate
with ``--print-directive`` and ``--dry-run`` before any unscoped or CI use, and
confirm the exact headless flags / permission mode against your installed CLI.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".claude" / "workflows" / "doc-regenerate.js"


def build_directive(args: argparse.Namespace) -> str:
    """Build the natural-language directive that asks Claude to run the workflow."""
    payload: dict[str, object] = {"apply": bool(args.apply)}
    if args.sections:
        payload["sections"] = args.sections
    rel = WORKFLOW.relative_to(REPO).as_posix()
    return (
        f"Run the Workflow tool with the workflow script at {rel} and pass it these "
        f"args (JSON): {json.dumps(payload)}. Do not do any documentation work "
        "yourself — the workflow dispatches the technical-writer, a verifier, and the "
        "documentation-auditor. When it returns, print its JSON return object verbatim."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--sections",
        nargs="+",
        choices=["tutorials", "how-to", "explanation", "root"],
        help="limit regeneration to these sections (default: all)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually overwrite pages (default: dry-run, write nothing)",
    )
    ap.add_argument(
        "--print-directive",
        action="store_true",
        help="print the claude -p directive and exit (no claude call)",
    )
    ap.add_argument("--claude-bin", default="claude", help="path to the claude CLI")
    args = ap.parse_args(argv)

    if not WORKFLOW.exists():
        print(f"workflow not found: {WORKFLOW}", file=sys.stderr)
        return 2

    directive = build_directive(args)
    if args.print_directive:
        print(directive)
        return 0

    if shutil.which(args.claude_bin) is None:
        print(f"`{args.claude_bin}` not found on PATH", file=sys.stderr)
        return 2

    # `-p` runs a single prompt non-interactively. Exact flags (--output-format,
    # permission mode for sub-agents) are CLI-version specific — see the RISK note.
    cmd = [args.claude_bin, "-p", directive]
    print(f"$ {args.claude_bin} -p <directive>")
    return subprocess.run(cmd, cwd=REPO).returncode


if __name__ == "__main__":
    raise SystemExit(main())
