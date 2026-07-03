"""Registry helper: resolve a plugin by ID or name."""

from __future__ import annotations

from typing import Any


async def resolve_plugin(plugin_id: Any, registry: Any) -> tuple[Any, Any]:
    """Resolve a plugin by ID (UUID) or name string.

    Args:
        plugin_id: Plugin UUID, UUID string, or name string
        registry: Registry with get() and all() methods

    Returns:
        Tuple of (resolved_plugin_or_None, resolved_plugin_id)
    """
    if isinstance(plugin_id, str):
        try:
            import uuid

            parsed = uuid.UUID(plugin_id)
            return await registry.get(parsed), parsed
        except ValueError:
            # Not a UUID — treat as plugin name
            for p in (await registry.all()).values():
                if p.metadata.name == plugin_id:
                    return p, p.metadata.id
            return None, plugin_id
    return await registry.get(plugin_id), plugin_id
