#!/usr/bin/env python3
"""Enforce the pre-1.0 API/CHANGELOG coupling policy.

uxok's constitution (CLAUDE.md, CHANGELOG.md header) requires that any breaking
change land together with its `CHANGELOG.md` and `docs/manifests/API.md` updates in
the same change set. This script detects a "breaking-shaped" change and asserts both
files are present in the diff.

A change is treated as breaking if ANY of:
  * a commit subject in the range carries a Conventional-Commits `!` (e.g. `feat!:`)
    or the word ``BREAKING`` (range mode only — staged mode has no commit message),
  * the diff touches a protocols module (``src/uxok/**/protocols*``),
  * the diff adds or removes an ``__all__`` member in any kernel module.

Usage:
    check_api_changelog_coupling.py --range origin/main...HEAD   # CI / PR
    check_api_changelog_coupling.py --staged                     # pre-commit

Exit codes: 0 = compliant (or not a breaking change), 1 = violation, 2 = usage error.
Stdlib only; shells out to ``git``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

CHANGELOG = "CHANGELOG.md"
API_DOC = "docs/manifests/API.md"

PROTOCOLS_MARKER = "protocols"
KERNEL_PREFIX = "src/uxok/"


def _git(*args: str) -> str:
    """Run a git command and return stdout, raising on failure."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def changed_files(*, range_spec: str | None, staged: bool) -> list[str]:
    """Return the list of files changed in the range or in the staged index."""
    if staged:
        out = _git("diff", "--cached", "--name-only")
    else:
        # `--name-only` over a range; `...` (merge-base) is the caller's choice.
        out = _git("diff", "--name-only", range_spec)  # type: ignore[arg-type]
    return [line for line in out.splitlines() if line.strip()]


def _diff_text(*, range_spec: str | None, staged: bool) -> str:
    if staged:
        return _git("diff", "--cached", "--unified=0")
    return _git("diff", "--unified=0", range_spec)  # type: ignore[arg-type]


def _commit_subjects(range_spec: str) -> list[str]:
    out = _git("log", "--format=%s%n%b", range_spec)
    return out.splitlines()


def touches_all_export(diff: str) -> bool:
    """True if the diff adds/removes a line mentioning ``__all__`` in the kernel."""
    current_file_is_kernel = False
    for line in diff.splitlines():
        if line.startswith(("+++ ", "--- ")):
            current_file_is_kernel = KERNEL_PREFIX in line
            continue
        if not current_file_is_kernel:
            continue
        is_content_change = line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        if is_content_change and "__all__" in line:
            return True
    return False


def is_breaking(
    *,
    files: list[str],
    diff: str,
    subjects: list[str],
) -> tuple[bool, str]:
    """Decide whether the change is breaking-shaped; return (verdict, reason)."""
    for f in files:
        if f.startswith(KERNEL_PREFIX) and PROTOCOLS_MARKER in f:
            return True, f"protocols module changed: {f}"

    if touches_all_export(diff):
        return True, "an __all__ export line changed in the kernel"

    for subject in subjects:
        stripped = subject.strip()
        if "BREAKING" in stripped:
            return True, f"commit message marked BREAKING: {stripped!r}"
        # Conventional Commits: `type(scope)!:` or `type!:`
        head = stripped.split(":", 1)[0]
        if head.endswith("!"):
            return True, f"commit subject carries '!': {stripped!r}"

    return False, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--range", dest="range_spec", help="git range, e.g. origin/main...HEAD")
    group.add_argument("--staged", action="store_true", help="check the staged index")
    args = parser.parse_args(argv)

    try:
        files = changed_files(range_spec=args.range_spec, staged=args.staged)
        diff = _diff_text(range_spec=args.range_spec, staged=args.staged)
        subjects = [] if args.staged else _commit_subjects(args.range_spec)
    except subprocess.CalledProcessError as exc:
        print(f"git error: {exc.stderr.strip()}", file=sys.stderr)
        return 2

    breaking, reason = is_breaking(files=files, diff=diff, subjects=subjects)
    if not breaking:
        print("No breaking-shaped change detected; coupling check not required.")
        return 0

    missing = [doc for doc in (CHANGELOG, API_DOC) if doc not in files]
    if missing:
        print(f"BREAKING change detected ({reason}).", file=sys.stderr)
        print(
            "Pre-1.0 policy requires these be updated in the same change set, "
            f"but they are missing: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    print(f"Breaking change ({reason}) — CHANGELOG.md and API.md both updated. OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
