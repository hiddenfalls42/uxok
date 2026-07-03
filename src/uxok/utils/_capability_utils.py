"""Utilities for typed capability protocol support."""

from __future__ import annotations

import inspect
from typing import Any

from uxok.utils._helpers import camel_to_snake

# Suffixes to strip from Protocol class names when deriving capability names
_CAPABILITY_SUFFIXES = ("Capability", "Cap")


def derive_capability_name(cls_or_str: type | str) -> str:
    """Derive a capability string name from a type or pass through a string.

    Resolution order:
    1. If already a string, return as-is.
    2. If the type has a ``__capability_name__`` class attribute, use that.
    3. Strip known suffixes (``Capability``, ``Cap``), then CamelCase → snake_case.

    Examples:
        >>> derive_capability_name("greeting")
        'greeting'
        >>> derive_capability_name(Greeting)          # class Greeting(Protocol)
        'greeting'
        >>> derive_capability_name(GreetingCapability) # strips suffix
        'greeting'
        >>> derive_capability_name(FileStorage)
        'file_storage'
    """
    if isinstance(cls_or_str, str):
        return cls_or_str

    # Explicit override via class attribute
    explicit = getattr(cls_or_str, "__capability_name__", None)
    if explicit is not None:
        return explicit

    name = cls_or_str.__name__
    for suffix in _CAPABILITY_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break

    return camel_to_snake(name)


def normalize_capability_set(
    raw: set[Any] | frozenset[Any] | None,
) -> tuple[frozenset[str], dict[str, type]]:
    """Split a mixed set of strings and Protocol types into string names and a protocol map.

    Args:
        raw: Set that may contain strings, Protocol types, or a mix of both.
             ``None`` is treated as empty.

    Returns:
        Tuple of (string names frozenset, {name: protocol_type} mapping).
        The mapping only contains entries for items that were types.

    Example:
        >>> normalize_capability_set({Greeting, "math"})
        (frozenset({'greeting', 'math'}), {'greeting': Greeting})
    """
    if not raw:
        return frozenset(), {}

    names: set[str] = set()
    protocols: dict[str, type] = {}

    for item in raw:
        if isinstance(item, type):
            name = derive_capability_name(item)
            names.add(name)
            protocols[name] = item
        else:
            names.add(str(item))

    return frozenset(names), protocols


def _method_dict(attr_name: str, attr: Any) -> dict[str, Any] | None:
    """Build the standard method-info dict for a single callable attribute.

    Returns ``None`` if the attribute is not introspectable (no signature).
    """
    try:
        sig = inspect.signature(attr)
    except (ValueError, TypeError):
        return None

    params = []
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        param_info: dict[str, Any] = {"name": param_name}
        if param.annotation is not inspect.Parameter.empty:
            param_info["annotation"] = _annotation_str(param.annotation)
        if param.default is not inspect.Parameter.empty:
            param_info["default"] = repr(param.default)
        params.append(param_info)

    return_annotation = ""
    if sig.return_annotation is not inspect.Signature.empty:
        return_annotation = _annotation_str(sig.return_annotation)

    return {
        "name": attr_name,
        "signature": str(sig),
        "doc": inspect.getdoc(attr) or "",
        "parameters": params,
        "return_annotation": return_annotation,
    }


def get_protocol_methods(protocol: type) -> list[dict[str, Any]]:
    """Introspect a Protocol class to extract its method signatures.

    Returns a list of dicts, each describing one method:
    - ``name``: method name
    - ``signature``: string representation of the full signature
    - ``doc``: docstring (or empty string)
    - ``parameters``: list of parameter dicts with name, annotation, default info
    - ``return_annotation``: string of return type annotation (or "")

    Only public methods (no ``_`` prefix) are included.
    """
    methods: list[dict[str, Any]] = []

    for attr_name in sorted(dir(protocol)):
        if attr_name.startswith("_"):
            continue

        attr = getattr(protocol, attr_name, None)
        if attr is None or not callable(attr):
            continue

        info = _method_dict(attr_name, attr)
        if info is not None:
            methods.append(info)

    return methods


def _annotation_str(annotation: Any) -> str:
    """Convert a type annotation to a readable string."""
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def signature_incompatibility(proto_method: Any, provider_method: Any) -> str | None:
    """Return a reason a provider method is signature-incompatible with a protocol method.

    Returns ``None`` when the two are compatible. Compatibility is structural
    (Liskov-style substitutability), not equality:

    * the provider must accept every parameter the protocol declares — by name,
      or absorbed by the provider's ``*args`` / ``**kwargs``;
    * the provider must not *require* a parameter the protocol never supplies
      (an optional provider param, or ``*args`` / ``**kwargs``, is fine);
    * when both sides annotate the return type, the annotations must match.

    A method whose signature cannot be introspected (e.g. a C builtin) falls
    back to presence-only — returns ``None`` rather than raising. ``self`` is
    ignored on both sides (the protocol method is unbound, the provider method
    is typically bound).
    """
    try:
        proto_sig = inspect.signature(proto_method)
        provider_sig = inspect.signature(provider_method)
    except (ValueError, TypeError):
        return None

    kinds = inspect.Parameter
    positional = (kinds.POSITIONAL_ONLY, kinds.POSITIONAL_OR_KEYWORD)

    proto_params = [p for name, p in proto_sig.parameters.items() if name != "self"]
    provider_params = {name: p for name, p in provider_sig.parameters.items() if name != "self"}
    provider_var_kw = any(p.kind == kinds.VAR_KEYWORD for p in provider_params.values())
    provider_var_pos = any(p.kind == kinds.VAR_POSITIONAL for p in provider_params.values())

    # Rule 1: provider must accept every concrete parameter the protocol declares.
    for pp in proto_params:
        if pp.kind in (kinds.VAR_POSITIONAL, kinds.VAR_KEYWORD):
            continue
        if pp.name in provider_params:
            continue
        if pp.kind == kinds.KEYWORD_ONLY and provider_var_kw:
            continue
        if pp.kind in positional and (provider_var_pos or provider_var_kw):
            continue
        return f"does not accept protocol parameter '{pp.name}'"

    # Rule 2: provider must not require a parameter the protocol never supplies.
    proto_names = {p.name for p in proto_params}
    proto_var_pos = any(p.kind == kinds.VAR_POSITIONAL for p in proto_params)
    proto_var_kw = any(p.kind == kinds.VAR_KEYWORD for p in proto_params)
    for name, qp in provider_params.items():
        if qp.kind in (kinds.VAR_POSITIONAL, kinds.VAR_KEYWORD):
            continue
        if qp.default is not kinds.empty:
            continue
        if name in proto_names:
            continue
        if qp.kind == kinds.KEYWORD_ONLY and proto_var_kw:
            continue
        if qp.kind in positional and (proto_var_pos or proto_var_kw):
            continue
        return f"requires parameter '{name}' not declared by the protocol"

    # Rule 3: return annotations, when both present, must match.
    empty = inspect.Signature.empty
    proto_ret = proto_sig.return_annotation
    provider_ret = provider_sig.return_annotation
    if (
        proto_ret is not empty
        and provider_ret is not empty
        and _annotation_str(proto_ret) != _annotation_str(provider_ret)
    ):
        return (
            f"return annotation '{_annotation_str(provider_ret)}' does not match "
            f"protocol's '{_annotation_str(proto_ret)}'"
        )

    return None
