"""Run-local accessibility references and typed tool-schema projection."""

from __future__ import annotations

import secrets
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema
from pydantic_core import SchemaValidator, core_schema

from a13n_harness.environment.accessibility import (
    AccessibilityActionReference,
    AccessibilityCursor,
    AccessibilityNodeReference,
    AccessibilityRangeReference,
)
from a13n_harness.environment.models import EnvironmentError

_REFERENCE_KINDS = {
    AccessibilityNodeReference: ("node", 8192),
    AccessibilityRangeReference: ("range", 1024),
    AccessibilityActionReference: ("action", 1024),
    AccessibilityCursor: ("cursor", 128),
}
type Reference = (
    AccessibilityNodeReference | AccessibilityRangeReference | AccessibilityActionReference | AccessibilityCursor
)


class AccessibilityReferences:
    """References are observations in this Run, never continuation state or authority."""

    def __init__(self) -> None:
        self._values: dict[type, OrderedDict[str, Reference]] = {kind: OrderedDict() for kind in _REFERENCE_KINDS}
        self._reverse: dict[type, dict[str, str]] = {kind: {} for kind in _REFERENCE_KINDS}

    def remember(self, value: Reference) -> str:
        kind = type(value)
        values, reverse = self._values[kind], self._reverse[kind]
        identity = value.model_dump_json()
        existing = reverse.get(identity)
        if existing is not None:
            values.move_to_end(existing)
            return existing
        prefix, limit = _REFERENCE_KINDS[kind]
        reference = f"{prefix}-{secrets.token_hex(6)}"
        values[reference] = value
        reverse[identity] = reference
        while len(values) > limit:
            _, evicted = values.popitem(last=False)
            reverse.pop(evicted.model_dump_json())
        return reference

    def resolve(self, reference: Any, kind: type) -> Reference:
        if not isinstance(reference, str):
            raise EnvironmentError("Supply a returned accessibility reference.", code="environment_request_invalid")
        value = self._values[kind].get(reference)
        if value is None:
            raise EnvironmentError(
                "Accessibility reference is unavailable; acquire the intended node or range again.",
                code="environment_not_found",
                details={"reason": "accessibility_reference_unavailable", "dispatch_stage": "pre_dispatch"},
            )
        self._values[kind].move_to_end(reference)
        return value

    def encode(self, value: Any) -> Any:
        if isinstance(
            value,
            AccessibilityNodeReference
            | AccessibilityRangeReference
            | AccessibilityActionReference
            | AccessibilityCursor,
        ):
            return self.remember(value)
        if isinstance(value, BaseModel):
            return {name: self.encode(getattr(value, name)) for name in type(value).model_fields}
        if isinstance(value, Mapping):
            return {key: self.encode(item) for key, item in value.items()}
        if isinstance(value, tuple | list):
            return [self.encode(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def arguments(
        self, model: type[BaseModel], *, allowed_ops: frozenset[str] | None = None
    ) -> tuple[dict[str, Any], Callable[[Any], BaseModel]]:
        """Reuse Pydantic's validators, replacing only native references and denied op tags.

        Core schemas retain the owning models' field and cross-field validators.
        The model sees short strings instead of execution identities/native handles.
        """

        def project(value: Any) -> Any:
            if isinstance(value, list):
                return [project(item) for item in value]
            if not isinstance(value, dict):
                return value
            kind = value.get("cls") if value.get("type") == "model" else None
            if isinstance(kind, type) and kind in _REFERENCE_KINDS:
                prefix, _ = _REFERENCE_KINDS[kind]
                replacement = core_schema.no_info_plain_validator_function(
                    lambda reference, kind=kind: self.resolve(reference, kind),
                    json_schema_input_schema=core_schema.str_schema(pattern=f"^{prefix}-[0-9a-f]+$", max_length=128),
                )
                if "ref" in value:
                    replacement["ref"] = value["ref"]
                return replacement
            result = {key: project(item) for key, item in value.items()}
            if result.get("type") == "model-fields" and "op" in result["fields"] and allowed_ops is not None:
                op_schema = result["fields"]["op"]["schema"]
                if op_schema.get("type") == "default":
                    op_schema = op_schema["schema"]
                if op_schema.get("type") == "literal" and set(op_schema["expected"]) & allowed_ops:
                    op_schema["expected"] = [op for op in op_schema["expected"] if op in allowed_ops]
            if result.get("type") == "tagged-union" and result.get("discriminator") == "op" and allowed_ops is not None:
                result["choices"] = {key: item for key, item in result["choices"].items() if key in allowed_ops}
            return result

        schema = project(TypeAdapter(model).core_schema)
        validator = SchemaValidator(schema)
        return GenerateJsonSchema().generate(schema), validator.validate_python
