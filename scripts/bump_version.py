#!/usr/bin/env python3
"""Tag-based version bump for uxok (built, but not wired into CI).

Performs the mechanical parts of a release in one call:
  1. Bumps ``version`` in ``pyproject.toml`` (semver major/minor/patch or --set).
  2. Rolls the ``## Unreleased`` heading in ``CHANGELOG.md`` to ``## X.Y.Z (DATE)``
     and opens a fresh empty ``## Unreleased`` section above it.
  3. Creates an annotated git tag ``vX.Y.Z``.

This script is intentionally NOT invoked by CI. Versioning waits on the docs/API
work owned elsewhere; run this by hand at release time.

Usage:
    bump_version.py {major|minor|patch}     [--dry-run] [--no-tag]
    bump_version.py --set X.Y.Z             [--dry-run] [--no-tag]

``--dry-run`` prints the diffs and the tag command and changes nothing.
Stdlib only (``tomllib`` reads; writes are done by targeted regex so comments and
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

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"
CHANGELOG = REPO / "CHANGELOG.md"

# Only ever match the version inside [project], i.e. the first top-level
# `version = "..."` line. setuptools/build versions live under [build-system]
# but never use the bare `version =` key, so a first-match anchor is safe.
_VERSION_RE = re.compile(r'^version\s*=\s*"(?P<ver>\d+\.\d+\.\d+)"\s*$', re.MULTILINE)
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_UNRELEASED_RE = re.compile(r"^## Unreleased\s*$", re.MULTILINE)


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


def bump_pyproject(new: str, *, dry_run: bool) -> str:
    text = PYPROJECT.read_text()
    match = _VERSION_RE.search(text)
    if not match:
        sys.exit('could not find a `version = "X.Y.Z"` line in pyproject.toml')
    updated = text[: match.start("ver")] + new + text[match.end("ver") :]
    line = match.group(0)
    new_line = line.replace(match.group("ver"), new)
    if dry_run:
        print("pyproject.toml:")
        print(f"  - {line}")
        print(f"  + {new_line}")
    else:
        PYPROJECT.write_text(updated)
    return new_line


def roll_changelog(new: str, *, dry_run: bool) -> None:
    text = CHANGELOG.read_text()
    match = _UNRELEASED_RE.search(text)
    if not match:
        sys.exit("could not find a `## Unreleased` heading in CHANGELOG.md")
    today = _dt.date.today().isoformat()
    replacement = f"## Unreleased\n\n## {new} ({today})"
    updated = text[: match.start()] + replacement + text[match.end() :]
    if dry_run:
        print("CHANGELOG.md:")
        print("  - ## Unreleased")
        print("  + ## Unreleased")
        print("  +")
        print(f"  + ## {new} ({today})")
    else:
        CHANGELOG.write_text(updated)


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

    print(f"{'[dry-run] ' if args.dry_run else ''}{current} -> {new}")
    bump_pyproject(new, dry_run=args.dry_run)
    roll_changelog(new, dry_run=args.dry_run)
    if not args.no_tag:
        create_tag(new, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
