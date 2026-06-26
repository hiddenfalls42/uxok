#!/usr/bin/env python3
"""Single-call, holistic project rename (dry-run by default).

Renames the project from ``uxok`` to a new name — content, the package
directory, and the virtualenv — in one invocation. Two modes:

  In-place (default): rewrite the tree, ``git mv src/uxok src/<new>``, move
  the repo-root folder, and rebuild the venv at the new location. ``--no-move``
  stops after the git mv and leaves the folder/venv to you.

  Copy (``--copy``): leave THIS repo entirely untouched and build a renamed
  COPY at ``<parent>/<new>`` instead — copy the working tree (excluding
  ``.git``, ``.venv``, caches and ``*.egg-info``), rewrite it, rename the
  package dir, and build a fresh venv in the copy. Because ``.git`` is not
  copied, the one task left afterward is to set up git in the copy
  (``git init && git add -A && git commit`` ...).

Token replacement is letter-boundary aware: ``uxok``/``uxok`` become the
new lowercase name, while the unrelated architecture word ``exokernel`` is left
intact (see TOKEN_RE). The script never creates a GitHub repo or publishes.

Usage:
    rename_project.py --to <new-name> [--copy] [--dry-run] [--no-venv] [--no-move]
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

OLD_PKG = "uxok"

# The project token "uxok" is a prefix of the unrelated architecture term
# "exokernel"/"Exokernel", which must survive the rename. A plain \b boundary is
# wrong twice over: it would still let "exokernel" through (no — \b stops it) but
# it also SKIPS legitimate snake_case compounds like "uxok_host" and
# "uxok_types" (the trailing "_" is a word char, so \b fails there). The real
# rule is: replace "uxok" unless it is immediately followed or preceded by a
# LETTER — a letter is what fuses it into a longer word ("exokernel"). Digits,
# underscores, dots, and slashes are all crossable token separators. re.IGNORECASE
# folds the brand ("uxok") and the package ("uxok") onto one lowercase
# replacement — the new brand is always lowercase.
TOKEN_RE = re.compile(rf"(?<![A-Za-z]){re.escape(OLD_PKG)}(?![A-Za-z])", re.IGNORECASE)

# Extensions worth rewriting; everything else (images, binaries) is skipped.
TEXT_SUFFIXES = {".py", ".toml", ".md", ".yml", ".yaml", ".cfg", ".ini", ".txt", ".rst"}

# Directories never to descend into.
SKIP_DIRS = {
    ".git", ".venv", "venv", "build", "dist", "site", "htmlcov", ".hypothesis",
    "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "docs.old",  # untracked stale backup — keep it reflecting the old name
}


def iter_text_files(root: Path = REPO) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*"):
        if path.is_dir() or path.resolve() == SELF:
            continue  # never rewrite the *running* script's own docstring mid-run
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix in TEXT_SUFFIXES:
            out.append(path)
    return out


def rewrite_files(new: str, *, root: Path = REPO, dry_run: bool) -> int:
    total = 0
    for path in iter_text_files(root):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        hits = len(TOKEN_RE.findall(text))
        if not hits:
            continue
        total += hits
        if dry_run:
            print(f"  {path.relative_to(root)}: {hits} substitution(s)")
            continue
        path.write_text(TOKEN_RE.sub(new, text), encoding="utf-8", errors="surrogateescape")
    return total


def git_mv_package(new_pkg: str, *, dry_run: bool) -> None:
    src = REPO / "src" / OLD_PKG
    dst = REPO / "src" / new_pkg
    cmd = ["git", "mv", f"src/{OLD_PKG}", f"src/{new_pkg}"]
    if dry_run:
        print(f"  would: {' '.join(cmd)}")
        return
    subprocess.run(cmd, cwd=REPO, check=True)
    print(f"  {src.relative_to(REPO)} -> {dst.relative_to(REPO)}")


def move_root(new_pkg: str, *, dry_run: bool) -> Path:
    new_root = REPO.parent / new_pkg
    if dry_run:
        print(f"  would: mv {REPO} -> {new_root}")
        return new_root
    REPO.rename(new_root)  # plain dir rename; .git and all content move with it
    print(f"  {REPO} -> {new_root}")
    return new_root


def rebuild_venv(new_root: Path, *, dry_run: bool) -> None:
    venv = new_root / ".venv"
    # The base interpreter behind any active venv — it lives outside the repo,
    # so it survives both deleting .venv and moving the root. sys.executable
    # would be the venv python we are about to delete.
    base_python = getattr(sys, "_base_executable", None) or shutil.which("python3")
    if dry_run:
        print(f"  would: rm -rf {venv}")
        print(f"  would: {base_python or 'python3'} -m venv .venv  (at {new_root})")
        print("  would: .venv/bin/pip install -e .[dev]")
        return
    if base_python is None:
        print("  ! no base python found; skipping venv rebuild — run it manually", file=sys.stderr)
        return
    shutil.rmtree(venv, ignore_errors=True)
    subprocess.run([base_python, "-m", "venv", ".venv"], cwd=new_root, check=True)
    # Non-fatal: a network failure here must not obscure that the rename itself
    # succeeded. Report and let the operator finish the install by hand.
    proc = subprocess.run(
        [str(venv / "bin" / "pip"), "install", "-e", ".[dev]"], cwd=new_root, check=False
    )
    if proc.returncode != 0:
        print("  ! pip install failed (network?); rerun `.venv/bin/pip install -e .[dev]`",
              file=sys.stderr)


def smoke_test(new_root: Path, new_pkg: str) -> None:
    py = new_root / ".venv" / "bin" / "python"
    if not py.exists():
        print(f"  ! {py} missing; skipping import smoke test", file=sys.stderr)
        return
    proc = subprocess.run([str(py), "-c", f"import {new_pkg}"], cwd=new_root, check=False)
    print(f"  import {new_pkg}: {'ok' if proc.returncode == 0 else 'FAILED'}")


def _copy_ignore(src: str, names: list[str]) -> set[str]:
    """copytree filter: drop skipped dirs (incl. .git/.venv) and egg-info cruft."""
    return {n for n in names if n in SKIP_DIRS or n.endswith(".egg-info")}


def copy_tree(new_root: Path, *, dry_run: bool) -> None:
    """Copy the working tree to new_root, leaving the original untouched.

    .git is intentionally excluded (it lives in SKIP_DIRS), so the copy is a
    clean, history-free working tree — git is set up fresh afterward.
    """
    if dry_run:
        print(f"  would: copy {REPO} -> {new_root}")
        print(f"         (excluding {', '.join(sorted(SKIP_DIRS))}, and *.egg-info)")
        return
    shutil.copytree(REPO, new_root, ignore=_copy_ignore, symlinks=True)
    print(f"  copied {REPO} -> {new_root}")


def rename_pkg_dir(root: Path, new_pkg: str, *, dry_run: bool) -> None:
    """Plain directory rename of the package inside the copy (no git involved)."""
    src = root / "src" / OLD_PKG
    dst = root / "src" / new_pkg
    if dry_run:
        print(f"  would: mv src/{OLD_PKG} -> src/{new_pkg}")
        return
    src.rename(dst)
    print(f"  src/{OLD_PKG} -> src/{new_pkg}")


def run_copy(new: str, *, dry_run: bool, no_venv: bool) -> int:
    new_root = REPO.parent / new
    tag = "[dry-run] " if dry_run else ""
    print(f"{tag}copy-rename {OLD_PKG} -> {new} into {new_root} (original left untouched):")
    print(f"  {OLD_PKG} -> {new}  |  {OLD_PKG.capitalize()} -> {new} (lowercase)  |  '{OLD_PKG}el' preserved")
    print("1. copy repo (excluding .git, .venv, caches, *.egg-info):")
    copy_tree(new_root, dry_run=dry_run)
    print("2. text substitutions in the copy:")
    # In dry-run the copy does not exist yet; preview against the live source,
    # whose content is identical to what the copy would contain.
    total = rewrite_files(new, root=REPO if dry_run else new_root, dry_run=dry_run)
    print(f"   {'would change' if dry_run else 'changed'} {total} occurrence(s)")
    print("3. package directory in the copy:")
    rename_pkg_dir(new_root, new, dry_run=dry_run)
    if no_venv:
        print("4. venv: skipped (--no-venv)")
    else:
        print("4. build fresh venv in the copy:")
        rebuild_venv(new_root, dry_run=dry_run)
        if not dry_run:
            print("5. smoke test:")
            smoke_test(new_root, new)

    if dry_run:
        print("\n[dry-run] nothing was changed; the original and the parent dir are untouched.")
        return 0
    print(f"\nDone. Original repo untouched at {REPO}")
    print(f"      renamed copy ready at {new_root}")
    print("The only thing left is git — in the copy:")
    print(f"  cd {new_root}")
    print(f"  git init && git add -A && git commit -m 'Initial commit: {new} (renamed from {OLD_PKG})'")
    print("  # create the GitHub repo to match the rewritten URLs, then:")
    print("  git remote add origin <new-repo-url> && git push -u origin main")
    print("Verify first: pytest && ruff check src tests plugins && mkdocs build")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", dest="new", required=True, help="new project name (lowercase identifier)")
    parser.add_argument("--dry-run", action="store_true", help="print every step, change nothing")
    parser.add_argument("--copy", action="store_true",
                        help="leave this repo untouched; build a renamed copy at <parent>/<new> "
                             "(excludes .git — set up git fresh in the copy)")
    parser.add_argument("--no-move", action="store_true",
                        help="in-place only: rename content + git mv, leave the repo-root move (and venv) to you")
    parser.add_argument("--no-venv", action="store_true", help="skip the venv rebuild + smoke test")
    args = parser.parse_args(argv)

    new = args.new
    if not (new.isidentifier() and new.islower()):
        sys.exit(f"invalid name {new!r} (must be a lowercase Python identifier)")
    if new == OLD_PKG:
        sys.exit("new name equals current; nothing to do")

    # Preflight: fail before any destructive step if the targets are occupied.
    if not (REPO / "src" / OLD_PKG).is_dir():
        sys.exit(f"package dir not found: {REPO / 'src' / OLD_PKG}")
    if (REPO / "src" / new).exists():
        sys.exit(f"destination package already exists: {REPO / 'src' / new}")
    if (REPO.parent / new).exists():
        sys.exit(f"destination repo root already exists: {REPO.parent / new}")

    if args.copy:
        return run_copy(new, dry_run=args.dry_run, no_venv=args.no_venv)

    tag = "[dry-run] " if args.dry_run else ""

    print(f"{tag}rename {OLD_PKG} -> {new} (brand always lowercase; word-boundary safe):")
    print(f"  {OLD_PKG} -> {new}")
    print(f"  {OLD_PKG.capitalize()} -> {new}   (lowercase, not {new.capitalize()})")
    print(f"  word '{OLD_PKG}el'/'{OLD_PKG.capitalize()}el' preserved")
    print("1. text substitutions:")
    total = rewrite_files(new, dry_run=args.dry_run)
    print(f"   {'would change' if args.dry_run else 'changed'} {total} occurrence(s)")
    print("2. package directory:")
    git_mv_package(new, dry_run=args.dry_run)

    if args.no_move:
        print("3. repo root: skipped (--no-move)")
        if args.dry_run:
            print("\n[dry-run] nothing was changed.")
        else:
            old = REPO
            print(f"\nContent renamed and package git-mv'd; repo root still '{old.name}'.")
            print("To finish the move yourself (rebuilds the venv too):")
            print(f"  cd {old.parent} && mv {old.name} {new} && cd {new} \\")
            print("    && rm -rf .venv && python3 -m venv .venv && .venv/bin/pip install -e .[dev]")
            print("Then create the GitHub org/repo/Pages site to match the rewritten URLs.")
        return 0

    print("3. repo root:")
    new_root = move_root(new, dry_run=args.dry_run)
    if args.no_venv:
        print("4. venv: skipped (--no-venv)")
    else:
        print("4. rebuild venv:")
        rebuild_venv(new_root, dry_run=args.dry_run)
        if not args.dry_run:
            print("5. smoke test:")
            smoke_test(new_root, new)

    if args.dry_run:
        print("\n[dry-run] nothing was changed.")
    else:
        print(f"\nDone. Your shell is now in a stale directory — run:\n  cd {new_root}")
        print("Then verify: pytest && ruff check src tests plugins && mkdocs build")
        print("And create the GitHub org/repo/Pages site to match the rewritten URLs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
