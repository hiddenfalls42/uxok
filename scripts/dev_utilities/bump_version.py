#!/usr/bin/env python3
"""Tag-based version bump for uxok (run by hand at release time).

Performs the mechanical parts of a release in one atomic call:
  1. Bumps ``version`` in ``pyproject.toml`` (semver major/minor/patch or --set).
  2. Rolls the ``## [Unreleased]`` heading in ``CHANGELOG.md`` to
     ``## [X.Y.Z] — YYYY-MM-DD`` (Keep-a-Changelog bracket form, em dash) and
     opens a fresh empty ``## [Unreleased]`` section above it.
  3. Creates an annotated git tag ``vX.Y.Z``.

Atomicity: every file edit is computed *in full* before anything is written. If
any step cannot be planned (missing version line, missing ``## [Unreleased]``
heading, no-op bump), the script exits having touched nothing — it never leaves a
half-bumped tree. The git tag is created last, after both files are on disk.

Usage:
    bump_version.py {major|minor|patch}     [--dry-run] [--no-tag]
    bump_version.py --set X.Y.Z             [--dry-run] [--no-tag]

``--dry-run`` prints the planned diffs and the tag command and changes nothing.
Stdlib only (``tomllib`` reads; writes are targeted regex splices so comments and
formatting survive). Shells out to ``git`` for the tag.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
import tomllib
from pathlib import Path

# This file lives at <repo>/scripts/dev_utilities/bump_version.py, so the repo
# root is three parents up. (parents[2] == <repo>.)
REPO = Path(__file__).resolve().parents[2]
PYPROJECT = REPO / "pyproject.toml"
CHANGELOG = REPO / "CHANGELOG.md"

# Only ever match the version inside [project], i.e. the first top-level
# `version = "..."` line. setuptools/build versions live under [build-system]
# but never use the bare `version =` key, so a first-match anchor is safe.
_VERSION_RE = re.compile(r'^version\s*=\s*"(?P<ver>\d+\.\d+\.\d+)"\s*$', re.MULTILINE)
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
# Keep-a-Changelog bracket form: `## [Unreleased]`. Match only trailing
# spaces/tabs on the heading line — NOT `\s*`, which would gobble the blank line
# after the heading and collapse it into the rolled entry.
_UNRELEASED_RE = re.compile(r"^## \[Unreleased\][ \t]*$", re.MULTILINE)


def read_current_version() -> str:
    data = tomllib.loads(PYPROJECT.read_text())
    return data["project"]["version"]


def compute_version(current: str, *, level: str | None, explicit: str | None) -> str:
    if explicit is not None:
        if not _SEMVER_RE.match(explicit):
            sys.exit(f"--set value must be X.Y.Z, got {explicit!r}")
        return explicit
    major, minor, patch = (int(p) for p in current.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    sys.exit(f"unknown level {level!r}")


def plan_pyproject(new: str) -> tuple[str, list[str]]:
    """Return (new_full_text, preview_lines) for pyproject.toml. Pure; no writes."""
    text = PYPROJECT.read_text()
    match = _VERSION_RE.search(text)
    if not match:
        sys.exit('could not find a `version = "X.Y.Z"` line in pyproject.toml')
    updated = text[: match.start("ver")] + new + text[match.end("ver") :]
    old_line = match.group(0)
    new_line = old_line.replace(match.group("ver"), new)
    preview = ["pyproject.toml:", f"  - {old_line}", f"  + {new_line}"]
    return updated, preview


def plan_changelog(new: str, today: str) -> tuple[str, list[str]]:
    """Return (new_full_text, preview_lines) for CHANGELOG.md. Pure; no writes."""
    text = CHANGELOG.read_text()
    match = _UNRELEASED_RE.search(text)
    if not match:
        sys.exit("could not find a `## [Unreleased]` heading in CHANGELOG.md")
    new_heading = f"## [{new}] — {today}"
    replacement = f"## [Unreleased]\n\n{new_heading}"
    updated = text[: match.start()] + replacement + text[match.end() :]
    preview = [
        "CHANGELOG.md:",
        "  - ## [Unreleased]",
        "  + ## [Unreleased]",
        "  +",
        f"  + {new_heading}",
    ]
    return updated, preview


def create_tag(new: str, *, dry_run: bool) -> None:
    tag = f"v{new}"
    cmd = ["git", "tag", "-a", tag, "-m", f"Release {tag}"]
    if dry_run:
        print(f"git tag: {' '.join(cmd)}")
        return
    subprocess.run(cmd, cwd=REPO, check=True)
    print(f"created annotated tag {tag}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("level", nargs="?", choices=["major", "minor", "patch"])
    group.add_argument("--set", dest="explicit", help="set an explicit X.Y.Z version")
    parser.add_argument("--dry-run", action="store_true", help="print changes, write nothing")
    parser.add_argument("--no-tag", action="store_true", help="skip creating the git tag")
    args = parser.parse_args(argv)

    current = read_current_version()
    new = compute_version(current, level=args.level, explicit=args.explicit)
    if new == current:
        sys.exit(f"new version equals current ({current}); nothing to do")

    today = _dt.date.today().isoformat()

    # Plan EVERY file edit before writing anything. Any failure here exits
    # before a single byte is written, so the tree is never half-bumped.
    pyproject_text, pyproject_preview = plan_pyproject(new)
    changelog_text, changelog_preview = plan_changelog(new, today)

    print(f"{'[dry-run] ' if args.dry_run else ''}{current} -> {new}")
    for line in (*pyproject_preview, *changelog_preview):
        print(line)

    if not args.dry_run:
        # Both plans succeeded; commit them to disk together.
        PYPROJECT.write_text(pyproject_text)
        CHANGELOG.write_text(changelog_text)

    if not args.no_tag:
        create_tag(new, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
