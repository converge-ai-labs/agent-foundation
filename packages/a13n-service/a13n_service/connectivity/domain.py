"""Shared Connectivity value types with no feature-layer dependencies."""

from typing import Annotated

from pydantic import JsonValue, StringConstraints

BoundedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
AdapterKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ConfigVersion = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
JsonObject = dict[str, JsonValue]

__all__ = ["AdapterKey", "BoundedName", "ConfigVersion", "JsonObject"]
