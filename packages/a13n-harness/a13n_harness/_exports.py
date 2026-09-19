"""Resolve declared public exports without eagerly importing sibling features."""

from collections.abc import Mapping
from importlib import import_module
from typing import Any


def exported_names(exports: Mapping[str, tuple[str, ...]]) -> list[str]:
    return sorted(name for names in exports.values() for name in names)


def load_export(
    module_name: str,
    namespace: dict[str, Any],
    exports: Mapping[str, tuple[str, ...]],
    name: str,
) -> Any:
    for module, names in exports.items():
        if name in names:
            value = getattr(import_module(module), name)
            namespace[name] = value
            return value
    raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
