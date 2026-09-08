"""Shared Connectivity value types with no feature-layer dependencies."""

from typing import Annotated

from pydantic import JsonValue, StringConstraints

from a13n_service.names import DisplayName

AdapterKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ConfigVersion = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
JsonObject = dict[str, JsonValue]

__all__ = ["AdapterKey", "ConfigVersion", "DisplayName", "JsonObject"]
