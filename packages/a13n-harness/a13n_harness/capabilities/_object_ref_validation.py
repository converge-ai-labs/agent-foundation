"""Pinned Pydantic compatibility adapter for reference-preserving validation.

Keep native defaults, coercion, nested types, and JSON/Python entry semantics.
Reference-valued strings and enclosing user validators defer payload checks;
native fields still validate, and the original validator remains authoritative
immediately before target dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from pydantic import BaseModel, JsonValue, TypeAdapter
from pydantic.plugin._schema_validator import PluggableSchemaValidator
from pydantic_ai._function_schema import _validate_single_arg
from pydantic_core import SchemaValidator, core_schema

from a13n_harness.errors import DefinitionError
from a13n_harness.objects import contains_object_ref

_JSON = TypeAdapter(dict[str, JsonValue])


def _string_schema(schema: dict[str, Any]) -> bool:
    kind = schema.get("type")
    if kind == "str" or (kind == "enum" and schema.get("sub_type") == "str"):
        return True
    if kind == "literal":
        return all(isinstance(value, str) for value in schema["expected"])
    if kind in {"function-before", "function-after", "function-wrap", "nullable"}:
        return _string_schema(schema["schema"])
    return False


def _preserve_ref(value: Any, handler: Any) -> Any:
    if isinstance(value, str) and contains_object_ref(value):
        return value
    return handler(value)


def _reference_callback(function: Any, *, wrapped: bool) -> Any:
    # This native callback builds the kwargs envelope; it is structural, not a
    # payload constraint. Preserve it while widening its inner model schema.
    if isinstance(function, partial) and function.func is _validate_single_arg:
        return function

    def validate(value: Any, *args: Any) -> Any:
        if contains_object_ref(value):
            # Container/model validators can depend on payload bytes. Bypass
            # only this callback, retaining native inner field validation.
            return args[0](value) if wrapped else value
        return function(value, *args)

    return validate


def _widen(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_widen(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    # Wrap the complete string subtree, including its field validators. Other
    # fields and containers continue through the native validation machinery.
    if _string_schema(schema):
        return core_schema.no_info_wrap_validator_function(_preserve_ref, cast(core_schema.CoreSchema, schema))
    result = {key: _widen(value) for key, value in schema.items()}
    kind = schema.get("type")
    if kind in {"function-before", "function-after", "function-wrap"} and isinstance(schema.get("function"), dict):
        result["function"]["function"] = _reference_callback(
            schema["function"]["function"], wrapped=kind == "function-wrap"
        )
    return result


@dataclass
class ReferenceValidator:
    original: Any
    references: SchemaValidator

    @classmethod
    def create(cls, original: Any) -> ReferenceValidator:
        validator = original
        # Pydantic's plugin wrapper has no public schema export. This one private
        # unwrap is isolated here and covered by compatibility tests against the
        # pinned dependency line. Never reconstruct types from JSON Schema.
        if isinstance(validator, PluggableSchemaValidator):
            validator = validator._schema_validator
        if not isinstance(validator, SchemaValidator):
            raise DefinitionError(
                "Object-reference opt-in requires a native Pydantic Core argument validator.",
                code="object_ref_validator_unsupported",
            )
        arguments = cast(tuple[Any, ...], validator.__reduce__()[1])
        return cls(original, SchemaValidator(_widen(arguments[0]), *arguments[1:]))

    def validate_python(self, value: Any, **kwargs: Any) -> Any:
        validator = self.references if contains_object_ref(value) else self.original
        return validator.validate_python(value, **kwargs)

    def validate_json(self, value: Any, **kwargs: Any) -> Any:
        # Parse only to select the transport path; delegate the actual validation
        # to the matching JSON-mode validator, preserving strict date/UUID rules.
        parsed = _JSON.validate_json(value)
        validator = self.references if contains_object_ref(parsed) else self.original
        return validator.validate_json(value, **kwargs)


def reference_values(value: Any) -> Any:
    """Expose typed model fields for recursive expansion without serialization."""
    return dict(value) if isinstance(value, BaseModel) else value
