"""GitHub's nullable result fields survive the pinned Composio compatibility projection."""

from copy import deepcopy

import pytest
from a13n_service.connectivity.connectors.providers.composio.output_schema import corrected_output_schema
from a13n_service.connectivity.connectors.providers.composio.runtime import _tool
from jsonschema import Draft202012Validator, ValidationError


def repository_schema():
    repository = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "full_name": {"type": "string"},
            "license": {"$ref": "#/$defs/License"},
            "language": {"type": "string"},
            "mirror_url": {"type": "string"},
            "description": {"type": "string"},
            "homepage": {"type": "string"},
            "temp_clone_token": {"type": "string"},
            "parent": {"$ref": "#/$defs/MinimalRepository"},
        },
        "required": ["id"],
    }
    minimal = deepcopy(repository)
    minimal["properties"].update({name: {"type": "string"} for name in ("created_at", "pushed_at", "updated_at")})
    return {
        "type": "object",
        "properties": {"data": {"$ref": "#/$defs/GetARepositoryResponse"}, "successful": {"type": "boolean"}},
        "required": ["data", "successful"],
        "$defs": {
            "GetARepositoryResponse": repository,
            "MinimalRepository": minimal,
            "License": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "url": {"type": "string"}, "spdx_id": {"type": "string"}},
            },
        },
    }


def test_pinned_composio_repository_schema_accepts_github_nulls_without_changing_values():
    schema = repository_schema()
    original = deepcopy(schema)
    tool = _tool(
        {
            "slug": "GITHUB_GET_A_REPOSITORY",
            "version": "20260902_00",
            "input_parameters": {"type": "object"},
            "output_parameters": schema,
        }
    )
    response = {
        "successful": True,
        "data": {
            "id": 1296269,
            "full_name": "octocat/Hello-World",
            "license": None,
            "language": None,
            "mirror_url": None,
            "description": None,
            "homepage": None,
            "temp_clone_token": None,
            "parent": {
                "id": 2,
                "created_at": None,
                "pushed_at": None,
                "updated_at": None,
                "license": {"key": "other", "url": None, "spdx_id": "NOASSERTION"},
            },
        },
    }
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(response)
    before = deepcopy(response)
    Draft202012Validator(tool.output_schema).validate(response)
    assert response == before
    assert schema == original


@pytest.mark.parametrize(
    "data", [{}, {"id": None}, {"id": 1, "full_name": None}, {"id": 1, "language": 7}, {"id": 1, "license": "MIT"}]
)
def test_composio_nullable_projection_retains_required_fields_and_non_null_constraints(data):
    schema = corrected_output_schema(repository_schema(), tool_key="GITHUB_GET_A_REPOSITORY", version="20260902_00")
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate({"successful": True, "data": data})


@pytest.mark.parametrize(
    ("tool_key", "version"),
    [("GITHUB_ANOTHER_TOOL", "20260902_00"), ("GITHUB_GET_A_REPOSITORY", "20260903_01")],
)
def test_composio_nullable_projection_does_not_change_other_tools_or_versions(tool_key, version):
    schema = repository_schema()
    assert corrected_output_schema(schema, tool_key=tool_key, version=version) is schema
