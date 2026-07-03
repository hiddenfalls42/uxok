"""Error hierarchy for the core system."""

__all__ = [
    "BatchLoadError",
    "CapabilityAccessError",
    "CapabilityError",
    "CoreError",
    "MissingCapabilityError",
    "PluginError",
    "StalePluginError",
]


class CoreError(Exception):
    """Base error for all core operations."""


class PluginError(CoreError):
    """Plugin-related errors."""


class CapabilityError(CoreError):
    """Capability resolution errors with helpful messages.

    Raised when a requested capability is not available.
    """

    def __init__(
        self,
        capability: str | list[str] | None,
        available: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        """Initialize with capability name(s) and optional available capabilities.

        Args:
            capability: The requested capability name or names
            available: List of available capability names for suggestions
            message: Preformatted message (bypasses capability-based formatting)
        """
        if message is not None:
            super().__init__(message)
            return

        if capability is None:
            super().__init__("Capability error")
            return

        if isinstance(capability, list):
            missing_list = ", ".join(sorted(capability))
            if available:
                msg = (
                    f"Capabilities '{missing_list}' not available.\n"
                    f"Available capabilities: {', '.join(sorted(available))}"
                )
            else:
                msg = f"Capabilities '{missing_list}' not available."
            super().__init__(msg)
            return

        if available:
            msg = f"Capability '{capability}' not available.\n"
            msg += f"Available capabilities: {', '.join(sorted(available))}"
            msg += f"\nDid you forget to register a plugin that provides '{capability}'?"
        else:
            msg = f"Capability '{capability}' not available."
        super().__init__(msg)


class StalePluginError(PluginError):
    """Raised when a held PluginView's plugin is no longer resolvable.

    Occurs when the plugin has been unregistered or torn down between the
    time the PluginView was fetched and the time an action (call, uptime,
    methods) is attempted on it.  Callers using the EAFP pattern must catch
    this to handle the case where a plugin disappears during an await.
    """


class BatchLoadError(PluginError):
    """A load_plugins() batch failed.

    Carries how far the boot got (``installed``, in commit order) so the host
    can enforce its own rollback-or-keep policy instead of introspecting the
    registry. ``phase`` discriminates the two failure classes: ``"plan"`` is a
    pre-commit, graph-wide fault (cycle, missing capability, duplicate
    provider/name, materialize/compile failure) where ``installed`` is always
    empty; ``"commit"`` is a failure partway through installing the plan,
    where ``installed`` lists everything committed before the failing
    candidate. ``failed`` is the offending candidate's origin/name, or
    ``None`` for graph-wide faults such as a cycle.
    """

    def __init__(
        self,
        *,
        phase: str,
        cause: BaseException,
        installed: tuple[str, ...] = (),
        failed: str | None = None,
    ) -> None:
        self.phase = phase
        self.cause = cause
        self.installed = installed
        self.failed = failed
        loc = f" at '{failed}'" if failed else ""
        prefix = f" (installed so far: {', '.join(installed)})" if installed else ""
        super().__init__(f"Batch load failed during {phase}{loc}: {cause}{prefix}")


class CapabilityAccessError(CapabilityError):
    """Raised when a plugin resolves a capability outside its runtime grant.

    Complementary to :class:`MissingCapabilityError`: that one means a required
    capability is *absent* at registration; this one means the capability *exists*
    but the caller never granted itself access, so under ``capability_access="declared"`` /
    ``"sealed"`` it may not reach it (RFC 0001 §3.2, RFC 0002 §3.2). The runtime grant is
    the union ``requires | resolves`` (plus everything, if it holds ``kernel.dispatch``);
    the manifest is the only door.

    Also raised by the sealed return guard (RFC 0004 §4 / spec 0005 §C) when a
    sealed capability method hands back a live authority handle (a ``Plugin`` or a
    kernel handle) — a manifest-invisible second-hop leak. That path passes an
    explicit ``message``.
    """

    def __init__(
        self,
        capability: str,
        plugin_name: str,
        declared: list[str] | None = None,
        *,
        message: str | None = None,
    ) -> None:
        self.capability = capability
        self.plugin_name = plugin_name
        if message is None:
            declared_list = ", ".join(sorted(declared)) if declared else "(none)"
            message = (
                f"Plugin '{plugin_name}' resolved capability '{capability}' that is not in its "
                f"runtime grant (requires | resolves). Granted: {declared_list}. Add "
                f"'{capability}' to the plugin's `resolves` (runtime grant) — or `requires` if "
                "it is also a load-order dependency — to allow access (capability_access "
                "enforcement, RFC 0002)."
            )
        super().__init__(capability, message=message)


class MissingCapabilityError(CapabilityError):
    """Structured error for missing capabilities during lifecycle operations."""

    def __init__(
        self,
        missing: list[str],
        phase: str = "register",
        available: list[str] | None = None,
        requirer: str | None = None,
    ) -> None:
        self.missing = missing
        self.phase = phase
        self.requirer = requirer
        missing_list = ", ".join(sorted(missing))
        requirer_part = f" (required by plugin '{requirer}')" if requirer else ""
        msg = (
            f"No registered plugin provides required capability: {missing_list}{requirer_part}\n"
            "Register the providing plugin first (load order matters), or remove the "
            "capability from the plugin's `requires`."
        )
        if phase:
            msg = f"{msg} (phase={phase})"
        if available:
            msg = f"{msg}. Available capabilities: {', '.join(sorted(available))}"
        super().__init__(missing, available, message=msg)
