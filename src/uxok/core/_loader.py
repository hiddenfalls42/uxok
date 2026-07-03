"""Plugin loader: isolated-module execution and Plugin subclass discovery."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from uxok.errors import PluginError

if TYPE_CHECKING:
    from uxok.plugin import Plugin


def materialize_plugin(code: str, origin: str | None = None) -> type[Plugin]:
    """Execute plugin source in an isolated module and return the Plugin subclass.

    Execute in an isolated module.  Default (no origin): a bare module that
    never touches sys.modules.  With an ``origin`` file path: the module is
    made a PACKAGE rooted at the file's folder, so the plugin can import
    sibling helper modules relatively (``from . import _helper``).  The
    synthetic package is registered in sys.modules only for the duration of
    exec (so the import machinery can resolve siblings) and removed in the
    finally — top-level imports are already bound into the module namespace,
    so the loaded plugin keeps working and sys.modules stays clean.

    Args:
        code: Python source code containing exactly one Plugin subclass.
        origin: Optional source file path. When given, the code is executed as
            a package rooted at the file's folder, so the plugin may import
            sibling helper modules relatively. When omitted, behaviour is
            unchanged (a bare isolated module).

    Returns:
        The single Plugin subclass discovered in the code.

    Raises:
        PluginError: If the code fails to compile, or if zero or multiple
            Plugin subclasses are found.
    """
    # Import Plugin class so it's available in the isolated module namespace.
    from uxok.plugin import Plugin

    # Execute in an isolated module. Default (no origin): a bare module that
    # never touches sys.modules. With an ``origin`` file path: the module is
    # made a PACKAGE rooted at the file's folder, so the plugin can import
    # sibling helper modules relatively (``from . import _helper``). The
    # synthetic package is registered in sys.modules only for the duration of
    # exec (so the import machinery can resolve siblings) and removed in the
    # finally — top-level imports are already bound into the module namespace,
    # so the loaded plugin keeps working and sys.modules stays clean.
    pkg_name = f"_uxok_plugin_{uuid4().hex}"
    module = types.ModuleType(pkg_name)
    # Inject Plugin into the module namespace
    module.__dict__["Plugin"] = Plugin

    pkg_registered = False
    if origin is not None:
        origin_path = Path(origin)
        module.__file__ = str(origin_path)
        module.__path__ = [str(origin_path.parent)]  # makes the module a package
        module.__package__ = pkg_name  # relative imports resolve here
        sys.modules[pkg_name] = module
        pkg_registered = True

    try:
        exec(compile(code, origin or "<uxok_plugin>", "exec"), module.__dict__)  # noqa: S102
    except Exception as e:
        raise PluginError(f"Failed to compile plugin code: {e}") from e
    finally:
        # Drop the synthetic package and any siblings it imported, keeping the
        # no-permanent-sys.modules-pollution invariant.
        if pkg_registered:
            for key in [k for k in sys.modules if k == pkg_name or k.startswith(pkg_name + ".")]:
                sys.modules.pop(key, None)

    # Discover Plugin subclass
    plugin_classes = [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, Plugin) and obj is not Plugin
    ]

    if not plugin_classes:
        raise PluginError("No Plugin subclass found in provided code")

    if len(plugin_classes) > 1:
        names = [cls.__name__ for cls in plugin_classes]
        raise PluginError(
            f"Multiple Plugin subclasses found: {names}. "
            "Each code string must contain exactly one Plugin subclass."
        )

    return plugin_classes[0]
