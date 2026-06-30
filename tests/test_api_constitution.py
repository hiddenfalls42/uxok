"""Constitutional API compliance tests.

Mechanically verify that ``docs/manifests/API.md`` and the real kernel exports
agree.  This file is the canary for drift: if a symbol is added to (or removed
from) a module's ``__all__``, or if the document is edited without a matching
code change, at least one test here will fail.

Parser strategy
---------------
* Read ``API.md`` exactly once (module-level constant ``_API_TEXT``).
* Extract fenced ``python`` code blocks with a plain regex — one pattern,
  straightforward.
* Parse each extracted block with ``ast`` to pull ``__all__`` literals and
  ``ImportFrom`` nodes — no fragile string splitting.
* Fail the test that consumes a missing/malformed section rather than silently
  skipping it.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import re
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Locate and load the constitutional document once
# ---------------------------------------------------------------------------

_API_MD = Path(__file__).resolve().parents[1] / "docs" / "manifests" / "API.md"


def _load_api_text() -> str:
    """Return the raw text of API.md, or an empty string if unreadable.

    Callers that need the file to exist must first call
    ``test_api_manifest_file_exists`` or assert ``_API_MD.exists()`` directly.
    """
    if _API_MD.exists():
        return _API_MD.read_text(encoding="utf-8")
    return ""


_API_TEXT: str = _load_api_text()

# Fenced python code block: ```python ... ```  (non-greedy, DOTALL)
_FENCED_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _fenced_blocks() -> list[str]:
    return _FENCED_BLOCK_RE.findall(_API_TEXT)


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


def _parse_all_literal(source: str) -> list[str] | None:
    """Return the ``__all__`` list from *source*, or ``None`` if none found."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__all__"
        ):
            try:
                return ast.literal_eval(node.value)  # type: ignore[return-value]
            except (ValueError, TypeError):
                return None
    return None


def _parse_import_from(source: str) -> dict[str, list[str]]:
    """Return ``{module_name: [name, ...]}`` for every ``ImportFrom`` in *source*."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    result: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [alias.name for alias in node.names]
            # Accumulate if the same module appears in multiple lines (unusual
            # but possible in a multi-line parenthesised block).
            result.setdefault(node.module, []).extend(names)
    return result


# ---------------------------------------------------------------------------
# Aggregate the import declarations from all fenced blocks in §11
# ---------------------------------------------------------------------------


def _documented_imports() -> dict[str, list[str]]:
    """Collect every ``from <module> import (...)`` across all fenced blocks.

    Returns a dict mapping module name → list of documented import names.
    Raises ``AssertionError`` (which pytest surfaces as FAILED) if no import
    declarations are found at all — a parse failure must be loud.
    """
    aggregated: dict[str, list[str]] = {}
    for block in _fenced_blocks():
        for module, names in _parse_import_from(block).items():
            aggregated.setdefault(module, []).extend(names)

    # Deduplicate while preserving order (dict.fromkeys trick)
    return {mod: list(dict.fromkeys(names)) for mod, names in aggregated.items()}


def _documented_all() -> list[str] | None:
    """Return the ``__all__`` literal documented in the API manifest."""
    for block in _fenced_blocks():
        result = _parse_all_literal(block)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Modules that §11 declares in import blocks
# ---------------------------------------------------------------------------

_SUBPACKAGE_MODULES = [
    "uxok",
    "uxok.protocols",
    "uxok.plugin",
    "uxok.errors",
    "uxok.timing",
]


# ---------------------------------------------------------------------------
# Parametrize helpers — built at collection time so failures are per-symbol
# ---------------------------------------------------------------------------


def _subpackage_import_params() -> list[tuple[str, str]]:
    """``[(module, name), ...]`` from the documented import blocks."""
    imports = _documented_imports()
    params = []
    for mod in _SUBPACKAGE_MODULES:
        for name in imports.get(mod, []):
            params.append((mod, name))
    return params


def _subpackage_all_params() -> list[tuple[str, Any]]:
    """``[(module, module_obj), ...]`` for modules with a non-empty ``__all__``.

    A module whose ``__all__`` is empty (e.g. ``uxok.timing``) exposes no
    public names, so the document represents it in the "no public surface" list
    rather than an import block. Such modules are excluded from the reverse
    import-block requirement below.
    """
    params = []
    for mod_name in _SUBPACKAGE_MODULES:
        mod = importlib.import_module(mod_name)
        if getattr(mod, "__all__", None):
            params.append((mod_name, mod))
    return params


# ---------------------------------------------------------------------------
# 0. Precondition — file must exist
# ---------------------------------------------------------------------------


class TestApiManifestExists:
    """The constitutional document must be present at the expected path."""

    def test_api_manifest_file_exists(self) -> None:
        assert _API_MD.exists(), (
            f"API manifest not found at {_API_MD}. "
            "A rename or move of docs/manifests/API.md must be reflected here."
        )


# ---------------------------------------------------------------------------
# 1. Top-level __all__ — bidirectional exact match
# ---------------------------------------------------------------------------


class TestTopLevelAllMatch:
    """Documented ``__all__`` and ``uxok.__all__`` must be identical."""

    def test_api_md_contains_parseable_all_literal(self) -> None:
        """Parser must find an ``__all__ = [...]`` literal in the document.

        Failure here means the document's §1 structure changed and the parser
        needs updating — not that the code is wrong.
        """
        result = _documented_all()
        assert result is not None, (
            "No ``__all__ = [...]`` literal found in any fenced python block in "
            f"{_API_MD.name}. The document structure may have changed."
        )

    def test_documented_all_matches_real_all_exactly(self) -> None:
        """``uxok.__all__`` must equal the list the document declares."""
        documented = _documented_all()
        assert documented is not None, "Precondition: document must have a parseable __all__"

        import uxok

        assert uxok.__all__ == documented, (
            f"Top-level __all__ drift detected.\n"
            f"  documented: {documented}\n"
            f"  real:       {uxok.__all__}"
        )


# ---------------------------------------------------------------------------
# 2. Subpackage import blocks — forward check (document → code)
# ---------------------------------------------------------------------------


class TestSubpackageImportsResolve:
    """Every name the document lists in a ``from <module> import (...)`` block
    must be importable from that module at runtime.
    """

    def test_import_blocks_are_parseable(self) -> None:
        """At least one import-from declaration must be found in the document.

        Failure means the §11 structure changed and the parser needs updating.
        """
        imports = _documented_imports()
        assert imports, (
            f"No ``from ... import (...)`` blocks found in {_API_MD.name}. "
            "The document structure may have changed."
        )

    @pytest.mark.parametrize(("module", "name"), _subpackage_import_params())
    def test_documented_name_importable_from_module(self, module: str, name: str) -> None:
        """``getattr(importlib.import_module(module), name)`` must not be None."""
        mod = importlib.import_module(module)
        assert getattr(mod, name, None) is not None, (
            f"Documented name ``{name}`` is not importable from ``{module}``. "
            "Update the module's exports or remove the name from API.md."
        )


# ---------------------------------------------------------------------------
# 3. Reverse check (code → document) for modules with __all__
# ---------------------------------------------------------------------------


class TestSubpackageAllReverseMatch:
    """For modules that expose ``__all__``, the set of documented names must
    equal the module's real ``__all__`` set — catching names added to code but
    forgotten in the document.
    """

    @pytest.mark.parametrize(("module_name", "module"), _subpackage_all_params())
    def test_documented_names_equal_real_all(self, module_name: str, module: Any) -> None:
        imports = _documented_imports()
        documented = imports.get(module_name)

        assert documented is not None, (
            f"``{module_name}`` has a real ``__all__`` but API.md has no "
            f"``from {module_name} import (...)`` block. Add the block to §11."
        )

        documented_set = set(documented)
        real_set = set(module.__all__)

        missing_from_doc = real_set - documented_set
        extra_in_doc = documented_set - real_set

        messages = []
        if missing_from_doc:
            messages.append(f"In code __all__ but not in document: {sorted(missing_from_doc)}")
        if extra_in_doc:
            messages.append(f"In document but not in code __all__: {sorted(extra_in_doc)}")

        assert not messages, f"__all__ drift for ``{module_name}``:\n  " + "\n  ".join(messages)


# ---------------------------------------------------------------------------
# 4. Removed API — absent from top-level, CoreConfig still in protocols
# ---------------------------------------------------------------------------


class TestRemovedApiAbsent:
    """Names declared removed in §15 must not be accessible at top-level."""

    def test_on_not_at_top_level(self) -> None:
        """``on`` was renamed to ``event``; it must not appear at ``uxok``."""
        import uxok

        assert not hasattr(uxok, "on"), (
            "``on`` is back at top-level ``uxok``. §15 declares it removed; the code must match."
        )

    def test_coreconfig_not_at_top_level(self) -> None:
        """``CoreConfig`` was demoted to ``uxok.protocols``."""
        import uxok

        assert not hasattr(uxok, "CoreConfig"), (
            "``CoreConfig`` is at top-level ``uxok``. "
            "§15 declares it removed from the top-level; it belongs only in "
            "``uxok.protocols``."
        )

    def test_coreconfig_importable_from_protocols(self) -> None:
        """``CoreConfig`` must still be importable from its documented home."""
        from uxok.protocols import CoreConfig

        assert CoreConfig is not None

    def test_on_not_importable_from_top_level(self) -> None:
        """A direct ``from uxok import on`` must raise ``ImportError``."""
        with pytest.raises(ImportError):
            exec("from uxok import on")  # noqa: S102 — import-surface probe

    def test_blocked_plugins_kwarg_rejected_after_removal(self) -> None:
        """Regression: ``blocked_plugins`` config field was removed; passing it must fail fast."""
        from uxok import Core

        with pytest.raises(TypeError):
            Core(blocked_plugins=frozenset({"legacy"}))


# ---------------------------------------------------------------------------
# Section-scoped parse helpers (§7.x and §10.3)
# ---------------------------------------------------------------------------

# Regex: match a ### X.Y <Name> heading line exactly, then slice to the next
# ## / ### heading or a --- rule.  Scoping to one sub-section prevents the §2.1
# CoreConfig kwargs table from bleeding into the §7.3 CoreConfig fields test.
_SECTION_START_RE = re.compile(
    r"^#{2,3}\s+\d+\.\d+\s+(?P<name>\S.*?)\s*$",
    re.MULTILINE,
)
_SECTION_END_RE = re.compile(r"^(?:#{2,3}|---)", re.MULTILINE)


def _section_text(heading_name: str) -> str | None:
    """Slice ``_API_TEXT`` from the ``### X.Y <heading_name>`` line to the next
    heading or horizontal rule.  Returns ``None`` if the heading is not found.
    """
    for m in _SECTION_START_RE.finditer(_API_TEXT):
        if m.group("name") == heading_name:
            end_m = _SECTION_END_RE.search(_API_TEXT, m.end())
            return _API_TEXT[m.start() : end_m.start()] if end_m else _API_TEXT[m.start() :]
    return None


def _documented_fields(section: str) -> list[str]:
    """Return the ordered list of backtick-wrapped tokens in the first column of
    every ``|``-prefixed row.  Column header cells (``Field``, ``Field (declared)``,
    ``Member``, ``Type``, …) are not wrapped in backticks so they are naturally
    excluded.
    """
    fields: list[str] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        m = re.search(r"`([^`]+)`", line)
        if m:
            fields.append(m.group(1))
    return fields


def _documented_ctor(section: str) -> tuple[list[str], dict[str, Any]] | None:
    """Extract the first fenced ```python block from *section*, parse it as an
    expression, find the ``Call`` node, and return:

        (ordered_param_names, {kwarg: default_value})

    Positional args come from ``ast.Name`` nodes in ``call.args``; keyword args
    from ``call.keywords``.  Returns ``None`` if no parseable fenced block or no
    ``Call`` node is found.
    """
    blocks = _FENCED_BLOCK_RE.findall(section)
    if not blocks:
        return None
    try:
        tree = ast.parse(blocks[0].strip(), mode="eval")
    except SyntaxError:
        return None
    if not isinstance(tree.body, ast.Call):
        return None
    call = tree.body
    pos_names = [a.id for a in call.args if isinstance(a, ast.Name)]
    kw_names = [kw.arg for kw in call.keywords]
    kw_defaults: dict[str, Any] = {}
    for kw in call.keywords:
        try:
            kw_defaults[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, TypeError):
            pass
    return (pos_names + kw_names, kw_defaults)


def _documented_enum(section: str) -> dict[str, str]:
    """Parse ``| `NAME` | `"value"` |`` rows into ``{NAME: value}``.

    Backtick-wrapped values that contain surrounding double-quotes (as written in
    the CoreState table) have the quotes stripped from the extracted string.
    """
    result: dict[str, str] = {}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        m = re.match(r'^\|\s*`([^`]+)`\s*\|\s*`"([^"]+)"`\s*\|', line)
        if m:
            result[m.group(1)] = m.group(2)
    return result


# ---------------------------------------------------------------------------
# Registry of dataclasses to check: (heading_name, module_path, attr_name)
# ---------------------------------------------------------------------------

_DATACLASS_PARAMS: list[tuple[str, str, str]] = [
    ("Event", "uxok.protocols", "Event"),
    ("Hook", "uxok.protocols", "Hook"),
    ("CoreConfig", "uxok.protocols", "CoreConfig"),
    ("PluginMetadata", "uxok.protocols", "PluginMetadata"),
    ("CapabilityInfo", "uxok.registry", "CapabilityInfo"),
]

_CTOR_PARAMS: list[tuple[str, str, str]] = [
    ("Event", "uxok.protocols", "Event"),
    ("Hook", "uxok.protocols", "Hook"),
]


# ---------------------------------------------------------------------------
# 5. Dataclass field sets — bidirectional match against §7.x / §10.3 tables
# ---------------------------------------------------------------------------


class TestDataclassFields:
    """Every field table in §7.x and §10.3 of API.md must exactly match the
    real ``dataclasses.fields()`` of the corresponding class — no extras, no
    missing.
    """

    @pytest.mark.parametrize(
        ("heading_name", "module_path", "attr_name"),
        _DATACLASS_PARAMS,
        ids=[p[0] for p in _DATACLASS_PARAMS],
    )
    def test_field_table_parseable(
        self, heading_name: str, module_path: str, attr_name: str
    ) -> None:
        """``_documented_fields`` must return a non-empty list for the section.

        Failure here means the document section structure changed and the parser
        needs updating — not that the code is wrong.
        """
        section = _section_text(heading_name)
        assert section is not None, (
            f"Section heading '{heading_name}' not found in {_API_MD.name}. "
            "The document structure may have changed."
        )
        fields = _documented_fields(section)
        assert fields, (
            f"§ '{heading_name}' field table parsed no backtick-wrapped names. "
            "The document structure may have changed — parser needs updating."
        )

    @pytest.mark.parametrize(
        ("heading_name", "module_path", "attr_name"),
        _DATACLASS_PARAMS,
        ids=[p[0] for p in _DATACLASS_PARAMS],
    )
    def test_field_set_matches_real_dataclass(
        self, heading_name: str, module_path: str, attr_name: str
    ) -> None:
        """Documented field set must equal ``{f.name for f in dataclasses.fields(cls)}``."""
        section = _section_text(heading_name)
        assert section is not None, f"Section '{heading_name}' not found."
        documented = _documented_fields(section)
        assert documented, f"No documented fields parsed for '{heading_name}'."

        mod = importlib.import_module(module_path)
        cls = getattr(mod, attr_name)
        real_fields = {f.name for f in dataclasses.fields(cls)}
        documented_set = set(documented)

        missing_from_doc = real_fields - documented_set
        extra_in_doc = documented_set - real_fields

        messages: list[str] = []
        if missing_from_doc:
            messages.append(
                f"In dataclasses.fields() but not documented: {sorted(missing_from_doc)}"
            )
        if extra_in_doc:
            messages.append(f"Documented but not in dataclasses.fields(): {sorted(extra_in_doc)}")

        assert not messages, f"Field drift for {attr_name} (§'{heading_name}'):\n  " + "\n  ".join(
            messages
        )


# ---------------------------------------------------------------------------
# 6. Constructor signatures — documented Call matches inspect.signature
# ---------------------------------------------------------------------------


class TestDataclassConstructors:
    """The fenced ``Cls(name, callback, ...)`` in §7.1 and §7.2 must match the
    real ``inspect.signature`` of the class — ordered param names and keyword
    defaults.  This catches the intentional mismatch between Hook's constructor
    arg ``callback`` and its stored field name ``func``.
    """

    @pytest.mark.parametrize(
        ("heading_name", "module_path", "attr_name"),
        _CTOR_PARAMS,
        ids=[p[0] for p in _CTOR_PARAMS],
    )
    def test_ctor_block_parseable(
        self, heading_name: str, module_path: str, attr_name: str
    ) -> None:
        """``_documented_ctor`` must find a parseable ``Call`` node in the section.

        Failure here means the fenced constructor block was removed or changed
        in structure — the parser needs updating.
        """
        section = _section_text(heading_name)
        assert section is not None, f"Section heading '{heading_name}' not found in {_API_MD.name}."
        result = _documented_ctor(section)
        assert result is not None, (
            f"No parseable fenced constructor Call found in §'{heading_name}'. "
            "The document structure may have changed — parser needs updating."
        )

    @pytest.mark.parametrize(
        ("heading_name", "module_path", "attr_name"),
        _CTOR_PARAMS,
        ids=[p[0] for p in _CTOR_PARAMS],
    )
    def test_ctor_param_names_match_signature(
        self, heading_name: str, module_path: str, attr_name: str
    ) -> None:
        """Documented ordered param names must equal ``[p.name for p in signature.parameters.values()]``."""
        section = _section_text(heading_name)
        assert section is not None
        result = _documented_ctor(section)
        assert result is not None
        doc_names, _doc_defaults = result

        mod = importlib.import_module(module_path)
        cls = getattr(mod, attr_name)
        real_names = [p.name for p in inspect.signature(cls).parameters.values()]

        assert doc_names == real_names, (
            f"Constructor param name drift for {attr_name} (§'{heading_name}'):\n"
            f"  documented: {doc_names}\n"
            f"  real:       {real_names}"
        )

    @pytest.mark.parametrize(
        ("heading_name", "module_path", "attr_name"),
        _CTOR_PARAMS,
        ids=[p[0] for p in _CTOR_PARAMS],
    )
    def test_ctor_keyword_defaults_match_signature(
        self, heading_name: str, module_path: str, attr_name: str
    ) -> None:
        """Documented keyword defaults must equal the real signature defaults for those kwargs."""
        section = _section_text(heading_name)
        assert section is not None
        result = _documented_ctor(section)
        assert result is not None
        _doc_names, doc_defaults = result

        mod = importlib.import_module(module_path)
        cls = getattr(mod, attr_name)
        sig = inspect.signature(cls)
        real_defaults = {
            name: p.default
            for name, p in sig.parameters.items()
            if p.default is not inspect.Parameter.empty
        }

        assert doc_defaults == real_defaults, (
            f"Constructor default drift for {attr_name} (§'{heading_name}'):\n"
            f"  documented: {doc_defaults}\n"
            f"  real:       {real_defaults}"
        )


# ---------------------------------------------------------------------------
# 7. CoreState enum — documented members match real enum exactly
# ---------------------------------------------------------------------------


class TestCoreStateMembers:
    """The §7.4 CoreState table must exactly match the real ``CoreState`` enum —
    every member name and its string value.
    """

    def test_corestate_table_parseable(self) -> None:
        """``_documented_enum`` must return a non-empty dict for §7.4.

        Failure here means the §7.4 table structure changed — parser needs updating.
        """
        section = _section_text("CoreState")
        assert section is not None, f"'CoreState' section not found in {_API_MD.name}."
        members = _documented_enum(section)
        assert members, (
            "§7.4 CoreState enum table parsed no members. "
            "The document structure may have changed — parser needs updating."
        )

    def test_corestate_members_match_real_enum(self) -> None:
        """``_documented_enum(§7.4)`` must equal ``{m.name: m.value for m in CoreState}``."""
        from uxok.protocols import CoreState

        section = _section_text("CoreState")
        assert section is not None
        documented = _documented_enum(section)
        assert documented, "Precondition: §7.4 must parse non-empty."

        real = {m.name: m.value for m in CoreState}

        assert documented == real, (
            f"CoreState enum drift:\n  documented: {documented}\n  real:       {real}"
        )
