"""The hook system: priority-ordered extension points.

Hooks are uxok's extension mechanism. Where events are fire-and-forget
notifications, a hook is a named point that runs every registered handler in
priority order (highest first), passing each the same arguments and collecting
their results into a list — so plugins can contribute behaviour at a defined
seam rather than just react after the fact. A ``firstresult`` mode instead
short-circuits to the first non-``None`` result.

The implementation lives in this package's private ``_system.py`` (with a
``_cache.py`` for resolved-handler lookup) and is not part of the public API.
What *is* public is spread across two other places by design, so the parts you
reach for as a plugin author stay where they are most ergonomic. Here is the
map:

- **Register a handler** by decorating a plugin method with
  [`@hook`][uxok.plugin.hook] (priority is a decorator argument), or at runtime
  with [`Plugin.register_hook`][uxok.plugin.Plugin.register_hook].
- **A registered handler** is modelled by [`Hook`][uxok.protocols.hooks.Hook].
- **The system contract** is the [`HookSystem`][uxok.protocols.hooks.HookSystem]
  protocol — depend on it, never on the implementation.

This split mirrors the kernel's curated-flat API: implementations stay private,
the contracts live under [`uxok.protocols`][uxok.protocols], and the day-to-day
ergonomics surface as methods and decorators on [`Plugin`][uxok.plugin.Plugin].
"""

# Internal implementation — not part of the public API. The public hook surface
# is the @hook decorator and Plugin.register_hook (uxok.plugin) plus the Hook
# and HookSystem contracts (uxok.protocols.hooks).
