"""Resolve declared public exports without eagerly importing sibling features."""

from importlib import import_module
from typing import Any


def load_export(
    module_name: str,
    namespace: dict[str, Any],
    exports: dict[str, tuple[str, str]],
    name: str,
) -> Any:
    if name not in exports:
        raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
    module, attribute = exports[name]
    value = getattr(import_module(module), attribute)
    namespace[name] = value
    return value
