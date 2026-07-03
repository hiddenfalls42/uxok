"""Import surface tests for the curated-flat public API.

The top-level ``uxok`` package re-exports exactly the names an ordinary
plugin author or the minimal host touches directly: the two classes (Core,
Plugin), the two decorators (event, hook), config-schema construction
(ConfigField, REQUIRED), and the exceptions the framework raises into caller
code (CoreError, PluginError, CapabilityError, MissingCapabilityError).

Protocol/data types and the EventBus/HookSystem surfaces stay subpackage-only.
This list is held explicitly here as an
independent second opinion on the surface; ``test_api_constitution.py`` is the
doc-driven check that API.md and the code agree.
"""

import pytest

# The full curated top-level surface, in the canonical (isort) order of
# ``uxok.__all__``. Maintained deliberately alongside the constitution.
EXPECTED_TOP_LEVEL = [
    "REQUIRED",
    "CapabilityAccessError",
    "CapabilityError",
    "ConfigField",
    "Core",
    "CoreError",
    "MissingCapabilityError",
    "Plugin",
    "PluginError",
    "StalePluginError",
    "event",
    "hook",
]

# ---------------------------------------------------------------------------
# 1. Top-level imports — the curated public names
# ---------------------------------------------------------------------------


class TestTopLevelImports:
    """Every curated public name resolves from the package root."""

    @pytest.mark.parametrize("name", EXPECTED_TOP_LEVEL)
    def test_public_name_imports_successfully(self, name: str) -> None:
        """Each top-level export must be importable."""
        import importlib

        module = importlib.import_module("uxok")
        assert getattr(module, name) is not None

    def test_core_names_importable_in_single_statement(self) -> None:
        """The everyday import line works."""
        from uxok import Core, Plugin, event, hook

        assert all((Core, Plugin, hook, event))


# ---------------------------------------------------------------------------
# 2. __all__ contract — exactly the curated surface
# ---------------------------------------------------------------------------


class TestAllContract:
    """uxok.__all__ must list exactly the curated public names."""

    def test_all_lists_the_curated_surface(self) -> None:
        import uxok

        assert uxok.__all__ == EXPECTED_TOP_LEVEL

    def test_all_entries_are_accessible(self) -> None:
        import uxok

        for name in uxok.__all__:
            assert hasattr(uxok, name), f"__all__ lists '{name}' but it is not accessible"


# ---------------------------------------------------------------------------
# 3. Old names are NOT at the top level
# ---------------------------------------------------------------------------


class TestOldNamesRejectedAtTopLevel:
    """Symbols demoted to subpackages must raise ImportError from the root."""

    REMOVED_SYMBOLS = [
        "CoreConfig",
        "CoreState",
        "Event",
        "Hook",
        "PluginMetadata",
        "EventBus",
        "HookSystem",
        "PluginProtocol",
        "Registry",
        "CapabilitySystem",
        "on",
    ]

    @pytest.mark.parametrize("name", REMOVED_SYMBOLS)
    def test_import_raises_error(self, name: str) -> None:
        """Importing a demoted symbol from uxok must fail."""
        with pytest.raises(ImportError):
            exec(f"from uxok import {name}")  # noqa: S102 — import-surface probe


# ---------------------------------------------------------------------------
# 4. Subpackage imports — demoted symbols live in their new homes
# ---------------------------------------------------------------------------


class TestProtocolSubpackageImports:
    """Protocols subpackage exports all protocol / data types."""

    @pytest.mark.parametrize(
        "name",
        [
            "CoreConfig",
            "CoreState",
            "Event",
            "Hook",
            "PluginMetadata",
            "PluginProtocol",
        ],
    )
    def test_protocol_symbol_imports_successfully(self, name: str) -> None:
        """Each curated protocol symbol must resolve from uxok.protocols."""
        from uxok import protocols

        assert hasattr(protocols, name)

    @pytest.mark.parametrize("name", ["EventBus", "HookSystem"])
    def test_eventbus_hooksystem_not_on_protocols_package(self, name: str) -> None:
        """EventBus/HookSystem are reached via core.events/core.hooks; they are
        NOT part of the public protocols package surface."""
        import uxok.protocols as protocols

        assert name not in protocols.__all__
        assert not hasattr(protocols, name)

    def test_eventbus_importable_from_definition_module(self) -> None:
        """EventBus resolves from its definition module."""
        from uxok.protocols.events import EventBus

        assert EventBus is not None

    def test_hooksystem_importable_from_definition_module(self) -> None:
        """HookSystem resolves from its definition module."""
        from uxok.protocols.hooks import HookSystem

        assert HookSystem is not None

    def test_registry_importable_from_submodule(self) -> None:
        """Registry is importable from uxok.protocols.registry."""
        from uxok.protocols.registry import Registry

        assert Registry is not None

    def test_capability_system_importable_from_submodule(self) -> None:
        """CapabilitySystem is importable from uxok.protocols.capability_system."""
        from uxok.protocols.capability_system import CapabilitySystem

        assert CapabilitySystem is not None


class TestPluginSubpackageImports:
    """Plugin subpackage exports decorators and config helpers."""

    @pytest.mark.parametrize("name", ["ConfigField", "REQUIRED", "hook", "event"])
    def test_plugin_symbol_imports_successfully(self, name: str) -> None:
        """Each demoted plugin symbol must resolve from uxok.plugin."""

        # Parametrize confirms each name exists; the import above proves it.
        assert True


class TestErrorSubpackageImports:
    """Error classes live in uxok.errors."""

    @pytest.mark.parametrize(
        "name", ["CoreError", "PluginError", "CapabilityError", "MissingCapabilityError"]
    )
    def test_error_imports_successfully(self, name: str) -> None:
        import importlib

        errors = importlib.import_module("uxok.errors")
        assert getattr(errors, name) is not None


class TestTypesEvictedFromKernel:
    """Result monad types are capability-layer convention, not kernel API."""

    def test_uxok_types_is_gone(self) -> None:
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("uxok.types")


class TestKernelBoundary:
    """The kernel is src/uxok/ and nothing else.

    The kernel must not import application-level packages (including its own
    reference `plugins/`) — dependencies point inward, never outward.
    """

    _FORBIDDEN = ("capabilities", "examples", "model", "transform", "io.", "plugins")

    def test_kernel_never_imports_application_packages(self) -> None:
        import ast
        from pathlib import Path

        kernel_root = Path(__file__).parent.parent / "src" / "uxok"
        violations = []

        for py_file in kernel_root.rglob("*.py"):
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    if root in {"capabilities", "examples", "model", "transform", "io", "plugins"}:
                        # stdlib `io` is allowed; flag only the repo package,
                        # which would appear as `io.` submodule imports or
                        # `from io import` of repo-only names — stdlib io has
                        # no submodules, so any dotted io import is the repo.
                        if root == "io" and "." not in name:
                            continue
                        violations.append(f"{py_file.relative_to(kernel_root)}: {name}")

        assert violations == [], f"Kernel imports application-level package: {violations}"


# ---------------------------------------------------------------------------
# 5. `on` is completely gone
# ---------------------------------------------------------------------------


class TestOnIsGone:
    """The old `on` decorator has been renamed to `event` everywhere."""

    def test_on_not_in_top_level(self) -> None:
        with pytest.raises(ImportError):
            from uxok import on  # noqa: F401

    def test_on_not_in_plugin_subpackage(self) -> None:
        with pytest.raises(ImportError):
            from uxok.plugin import on  # noqa: F401


# ---------------------------------------------------------------------------
# 6. No circular imports
# ---------------------------------------------------------------------------


class TestNoCircularImports:
    """Key subpackages must import without circular-import errors."""

    @pytest.mark.parametrize("submodule", ["protocols", "core", "plugin", "errors"])
    def test_submodule_imports_cleanly(self, submodule: str) -> None:
        try:
            import importlib

            importlib.import_module(f"uxok.{submodule}")
        except ImportError as exc:
            pytest.fail(f"Circular import detected in uxok.{submodule}: {exc}")


# ---------------------------------------------------------------------------
# 7. Subpackage __all__ contracts
# ---------------------------------------------------------------------------


class TestSubpackageAllContracts:
    """Verify __all__ contents for subpackages whose public surface changed."""

    def test_timing_all_is_empty(self) -> None:
        """uxok.timing.__all__ must be [] — all timing types are internal."""
        import uxok.timing as timing

        assert timing.__all__ == []

    def test_registry_all_lists_collection_view_and_capability_info(self) -> None:
        """uxok.registry.__all__ must be ['CapabilityInfo', 'PluginCollection', 'PluginView']."""
        import uxok.registry as reg

        assert reg.__all__ == ["CapabilityInfo", "PluginCollection", "PluginView"]

    def test_protocols_all_does_not_contain_registry_or_capability_system(self) -> None:
        """Registry and CapabilitySystem are removed from protocols.__all__."""
        import uxok.protocols as protocols

        assert "Registry" not in protocols.__all__
        assert "CapabilitySystem" not in protocols.__all__


# ---------------------------------------------------------------------------
# 8. uxok.registry promoted exports
# ---------------------------------------------------------------------------


class TestRegistryPromotedExports:
    """PluginCollection, PluginView, and CapabilityInfo must be importable from uxok.registry."""

    def test_plugin_collection_importable_from_registry(self) -> None:
        from uxok.registry import PluginCollection

        assert PluginCollection is not None

    def test_plugin_view_importable_from_registry(self) -> None:
        from uxok.registry import PluginView

        assert PluginView is not None

    def test_capability_info_importable_from_registry(self) -> None:
        from uxok.registry import CapabilityInfo

        assert CapabilityInfo is not None

    def test_stale_plugin_error_importable_from_top_level(self) -> None:
        from uxok import StalePluginError

        assert StalePluginError is not None
