"""Preserve integer schema bounds across FastAPI's OpenAPI model serialization."""

import inspect
from typing import Any

from fastapi import FastAPI
from fastapi.openapi import utils as openapi_utils

_BOUNDS = frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"})


def _restore_integer_bounds(source: Any, target: Any) -> None:
    if isinstance(source, dict) and isinstance(target, dict):
        for key, value in source.items():
            if key in _BOUNDS and type(value) is int:
                actual = target.get(key)
                if type(actual) not in (int, float):
                    raise ValueError(f"OpenAPI lost numeric schema bound {key}")
                if actual != value:
                    target[key] = value
            elif key in target:
                _restore_integer_bounds(value, target[key])
    elif isinstance(source, list) and isinstance(target, list):
        if len(source) != len(target):
            raise ValueError("OpenAPI schema array changed during serialization")
        for source_item, target_item in zip(source, target, strict=True):
            _restore_integer_bounds(source_item, target_item)


def preserve_integer_schema_bounds(app: FastAPI, document: dict[str, Any]) -> None:
    """Use Pydantic's pre-OpenAPI definitions, not rounded floats, as numeric authority."""
    # FastAPI's internal schema helpers have different signatures across supported versions.
    # Treat the module dynamically here; the version-specific runtime path is checked below.
    compat: Any = openapi_utils
    fields = compat.get_fields_from_routes([*app.routes, *app.webhooks.routes])
    if "schema_generator" in inspect.signature(compat.get_definitions).parameters:
        # FastAPI 0.116 uses the older explicit schema-generator interface.
        names = compat.get_compat_model_name_map(fields)
        _, definitions = compat.get_definitions(
            fields=fields,
            model_name_map=names,
            schema_generator=compat.GenerateJsonSchema(ref_template=compat.REF_TEMPLATE),
            separate_input_output_schemas=app.separate_input_output_schemas,
        )
    else:
        models = compat.get_flat_models_from_fields(fields, known_models=set())
        _, definitions = compat.get_definitions(
            fields=fields,
            model_name_map=compat.get_model_name_map(models),
            separate_input_output_schemas=app.separate_input_output_schemas,
        )
    _restore_integer_bounds(definitions, document.get("components", {}).get("schemas", {}))
