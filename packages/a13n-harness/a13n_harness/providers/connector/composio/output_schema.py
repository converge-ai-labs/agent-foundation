"""Versioned corrections for documented upstream result-schema defects."""

from copy import deepcopy

from a13n_harness.providers.connector.contracts import JsonObject

_REPOSITORY_NULLABLE = ("description", "homepage", "language", "license", "mirror_url")
_NULLABLE_FIELDS = {
    "GetARepositoryResponse": (*_REPOSITORY_NULLABLE, "temp_clone_token"),
    "MinimalRepository": (*_REPOSITORY_NULLABLE, "created_at", "pushed_at", "updated_at"),
    "License": ("url",),
}


def corrected_output_schema(schema: JsonObject, *, tool_key: str, version: str) -> JsonObject:
    """Retain GitHub's nullable values omitted by this Composio schema version."""
    if tool_key != "GITHUB_GET_A_REPOSITORY" or version != "20260902_00":
        return schema
    result = deepcopy(schema)
    definitions = result.get("$defs")
    if not isinstance(definitions, dict):
        return result
    # GitHub's REST full/minimal repository and license schemas permit these nulls.
    # Do not infer nullability from optional fields or change other tool versions.
    for name, fields in _NULLABLE_FIELDS.items():
        definition = definitions.get(name)
        properties = definition.get("properties") if isinstance(definition, dict) else None
        if not isinstance(properties, dict):
            continue
        for field in fields:
            original = properties.get(field)
            if isinstance(original, dict):
                properties[field] = {"anyOf": [original, {"type": "null"}]}
    return result
