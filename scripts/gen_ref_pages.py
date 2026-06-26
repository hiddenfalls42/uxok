"""Generate the code reference pages and the full site navigation.

Two things are produced at build time and tracked nowhere — they exist only for
the duration of the build:

1. One reference page per kernel module under ``reference/`` (each a single
   mkdocstrings ``:::`` directive), plus ``reference/SUMMARY.md`` for the API nav.
2. The whole-site ``SUMMARY.md`` that ``literate-nav`` consumes for the entire
   navigation. The authored sections (tutorials, how-to, explanation) are
   discovered from the filesystem and titled from each page's ``# H1`` — so adding
   a Markdown file is enough to place it in the nav, with no ``mkdocs.yml`` edit.

Because the nav is generated, ``mkdocs.yml`` carries no ``nav:`` block. This file
is the single source of truth for navigation structure.
"""

from pathlib import Path

import mkdocs_gen_files

root = Path(__file__).parent.parent
src = root / "src"
docs = root / "docs"


# --- Reference pages: one mkdocstrings directive per module -----------------

# Curated public modules that get a reference page. Everything under
# uxok.protocols is included automatically (it is the contract layer); these
# are the remaining top-level public modules.
#
# The reference documents the curated public API surface, not the module tree.
# `events` and `hooks` are kept even though their implementations are private
# (all real code lives in their _bus.py/_system.py): their package docstrings
# are written as maps that point at where each public part actually lives — the
# @event/@hook decorators on Plugin, and the Event/EventBus, Hook/HookSystem
# contracts under uxok.protocols. The genuinely internal packages (core impl,
# timing, utils) and the impl/duplicate submodules (registry.impl,
# plugin.config_field) are absent: their contracts live under uxok.protocols
# and their public symbols are re-exported from the top-level `uxok` package,
# so nothing is lost. New public modules must be added here deliberately — the
# same discipline as uxok.__init__'s __all__.
_PUBLIC_MODULES = {
    "uxok",
    "uxok.errors",
    "uxok.events",
    "uxok.hooks",
    "uxok.plugin",
    "uxok.registry",
}


def _is_public(parts: tuple[str, ...]) -> bool:
    """Whether the module at ``parts`` gets its own reference page."""
    if any(part.startswith("_") for part in parts):
        return False
    dotted = ".".join(parts)
    return (
        dotted in _PUBLIC_MODULES
        or dotted == "uxok.protocols"
        or dotted.startswith("uxok.protocols.")
    )


def _hidden_children(parts: tuple[str, ...]) -> list[str]:
    """Non-private direct submodules of ``parts`` that get no page of their own.

    mkdocstrings auto-emits a "Modules" summary listing every submodule of a
    package. Without this, internal submodules that survive the leading-
    underscore convention (utils, timing, the core/registry impls, the
    config_field duplicate) would appear there as dangling, unlinked mentions.
    We feed these back as per-page ``filters`` so the summary lists only the
    submodules that actually have pages.
    """
    pkg_dir = src.joinpath(*parts)
    if not pkg_dir.is_dir():
        return []
    hidden = []
    for child in sorted(pkg_dir.iterdir()):
        if child.name.startswith((".", "_")):
            continue
        if child.suffix == ".py":
            name = child.stem
        elif child.is_dir() and (child / "__init__.py").exists():
            name = child.name
        else:
            continue
        if not _is_public((*parts, name)):
            hidden.append(name)
    return hidden


def _directive(parts: tuple[str, ...]) -> str:
    """The mkdocstrings directive for a page, hiding any dangling submodules."""
    dotted = ".".join(parts)
    hidden = _hidden_children(parts)
    if not hidden:
        return f"::: {dotted}"
    # Per-directive filters replace (not extend) the global config, so the
    # leading-underscore rule must be repeated here.
    lines = [f"::: {dotted}", "    options:", "      filters:", '        - "!^_"']
    lines += [f'        - "!^{name}$"' for name in hidden]
    return "\n".join(lines) + "\n"


def generate_reference() -> None:
    """Emit a reference page per module and the API section's nav file."""
    nav = mkdocs_gen_files.Nav()

    for path in sorted(src.rglob("*.py")):
        module_path = path.relative_to(src).with_suffix("")
        doc_path = path.relative_to(src).with_suffix(".md")
        full_doc_path = Path("reference", doc_path)

        parts = tuple(module_path.parts)

        if parts[-1] == "__init__":
            # Bind __init__ docs to the section itself (section-index behaviour).
            parts = parts[:-1]
            doc_path = doc_path.with_name("index.md")
            full_doc_path = full_doc_path.with_name("index.md")
        elif parts[-1] == "__main__":
            continue

        if not _is_public(parts):
            continue

        nav[parts] = doc_path.as_posix()

        with mkdocs_gen_files.open(full_doc_path, "w") as fd:
            fd.write(_directive(parts))

        mkdocs_gen_files.set_edit_path(full_doc_path, Path("../") / path)

    with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as nav_file:
        nav_file.writelines(nav.build_literate_nav())


# --- Authored-section nav: discovered from disk, titled from each page's H1 ---

# (section directory, stems pinned to the front in this order). Anything not
# listed is appended in title order. This keeps getting-started first without
# renaming files or hand-editing nav.
AUTHORED_SECTIONS: list[tuple[str, list[str]]] = [
    ("tutorials", ["getting-started"]),
    ("how-to", []),
    ("explanation", []),
]


def read_h1(md_path: Path, fallback: str) -> str:
    """Return the first ``# H1`` of a Markdown file, or ``fallback`` if absent."""
    try:
        for line in md_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
    except OSError:
        pass
    return fallback


def section_lines(section: str, pinned: list[str]) -> list[str]:
    """Build the literate-nav lines for one authored section, or ``[]`` if empty."""
    section_dir = docs / section
    index = section_dir / "index.md"
    if not index.exists():
        return []

    label = read_h1(index, section.replace("-", " ").capitalize())
    lines = [f"* [{label}]({section}/index.md)\n"]

    pages = [p for p in section_dir.glob("*.md") if p.name != "index.md"]
    pin_order = {stem: i for i, stem in enumerate(pinned)}
    # Pinned stems first (declared order), then everything else by title.
    pages.sort(key=lambda p: (pin_order.get(p.stem, len(pin_order)), read_h1(p, p.stem)))

    for page in pages:
        title = read_h1(page, page.stem.replace("-", " ").capitalize())
        lines.append(f"    * [{title}]({section}/{page.name})\n")
    return lines


def generate_site_nav() -> None:
    """Emit the root ``SUMMARY.md`` that drives the whole-site nav."""
    lines = ["* [Home](index.md)\n"]
    for section, pinned in AUTHORED_SECTIONS:
        lines += section_lines(section, pinned)
    # Trailing slash defers to reference/SUMMARY.md (nested literate-nav).
    lines.append("* [API Reference](reference/)\n")

    with mkdocs_gen_files.open("SUMMARY.md", "w") as nav_file:
        nav_file.writelines(lines)


generate_reference()
generate_site_nav()
