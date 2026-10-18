from __future__ import annotations

import ast
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from a13n_harness.toolsets.codeact import (
    CodeActPolicyToolset,
    CodeActToolPolicy,
    _build_catalog,
    _render_tool_declaration,
    render_codeact_runner_description,
)
from pydantic_ai import FunctionToolset, RunContext, Tool, ToolDefinition
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage


def test_declaration_preserves_keyword_requiredness_defaults_and_return_types() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "count": {"type": "integer", "minimum": 1, "default": 3},
            "mode": {"type": "string", "enum": ["short", "full"], "default": "short"},
        },
        "required": ["count"],
    }
    original = deepcopy(schema)
    declaration = _render_tool_declaration(
        ToolDefinition(name="inspect", parameters_json_schema=schema, return_schema={"type": "boolean"})
    )
    function = ast.parse(declaration).body[-1]
    assert isinstance(function, ast.AsyncFunctionDef)
    assert not function.args.args
    assert [arg.arg for arg in function.args.kwonlyargs] == ["count", "mode"]
    assert function.args.kw_defaults[0] is None  # A schema default does not make a required key optional.
    assert ast.literal_eval(function.args.kw_defaults[1]) == "short"
    assert ast.unparse(function.returns) == "bool"
    assert schema == original


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "additionalProperties": {"type": "integer"}},
        {"type": "object", "properties": {"optional": {"type": "string"}}, "additionalProperties": False},
        {
            "type": "object",
            "properties": {"some-key": {"type": "string"}},
            "required": ["some-key"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"class": {"type": "string"}},
            "required": ["class"],
            "additionalProperties": False,
        },
    ],
)
def test_nonrepresentable_argument_shapes_keep_callable_mapping_form(schema: dict) -> None:
    declaration = _render_tool_declaration(ToolDefinition(name="custom", parameters_json_schema=schema))
    function = ast.parse(declaration).body[-1]
    assert isinstance(function, ast.AsyncFunctionDef)
    assert function.args.kwarg is not None and function.args.kwarg.arg == "kwargs"
    assert not function.args.kwonlyargs


def test_referenced_shapes_are_self_contained_and_qualified_by_tool() -> None:
    def definition(name: str, field: str) -> ToolDefinition:
        return ToolDefinition(
            name=name,
            parameters_json_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"value": {"$ref": "#/$defs/Item"}},
                "required": ["value"],
                "$defs": {
                    "Item": {
                        "type": "object",
                        "properties": {field: {"type": "string"}},
                        "required": [field],
                    }
                },
            },
        )

    for name, field in [("first", "one"), ("second", "two")]:
        rendered = _render_tool_declaration(definition(name, field))
        module = ast.parse(rendered)
        shape, function = module.body
        assert isinstance(shape, ast.ClassDef) and shape.name == f"{name}_Item"
        assert isinstance(function, ast.AsyncFunctionDef)
        assert ast.unparse(function.args.kwonlyargs[0].annotation) == shape.name
        assert isinstance(shape.body[0], ast.AnnAssign)
        assert ast.unparse(shape.body[0].target) == field


def test_conflicting_input_and_output_shape_names_fall_back_to_exact_schemas() -> None:
    definition = ToolDefinition(
        name="transform",
        parameters_json_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"item": {"$ref": "#/$defs/Item"}},
            "required": ["item"],
            "$defs": {"Item": {"type": "object", "properties": {"input": {"type": "integer"}}}},
        },
        return_schema={
            "$ref": "#/$defs/Item",
            "$defs": {"Item": {"type": "object", "properties": {"output": {"type": "string"}}}},
        },
    )
    function = ast.parse(_render_tool_declaration(definition)).body[-1]
    assert isinstance(function, ast.AsyncFunctionDef)
    assert function.args.kwarg is not None


@pytest.mark.anyio
async def test_runner_directory_uses_current_prepared_schemas_and_eligibility() -> None:
    def lookup(count: int) -> int:
        return count

    def denied() -> None:
        raise AssertionError("not callable")

    toolset = CodeActPolicyToolset(
        wrapped=FunctionToolset([Tool(lookup, name="lookup-items"), denied]),
        policy=CodeActToolPolicy(tools={"lookup-items": True}),
    )
    ctx = RunContext(deps=None, model=FunctionModel(lambda messages, info: "unused"), usage=RunUsage())
    tools = await toolset.get_tools(ctx)
    original = tools["lookup-items"]
    updated_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"query": {"type": "string", "minLength": 3}},
        "required": ["query"],
    }
    updated = replace(original, tool_def=replace(original.tool_def, parameters_json_schema=updated_schema))
    for program in (False, True):
        before = render_codeact_runner_description(tools, ctx=ctx, program=program)
        after = render_codeact_runner_description({**tools, "lookup-items": updated}, ctx=ctx, program=program)
        assert "async def lookup_items(*, count: int)" in before
        assert "async def lookup_items(*, query: str)" in after
        assert "count: int" not in after
        assert "denied" not in after
        assert "(tool `lookup-items`)" in after
        encoded_schema = after.split("Arguments JSON Schema: ", 1)[1].splitlines()[0]
        assert json.loads(encoded_schema) == updated_schema
    assert original.tool_def.parameters_json_schema != updated_schema


@pytest.mark.anyio
async def test_deferred_catalog_tracks_current_discovery_and_prepared_membership() -> None:
    def lookup() -> int:
        return 42

    tools = await CodeActPolicyToolset(
        FunctionToolset([Tool(lookup, defer_loading=True)]), CodeActToolPolicy(default=True)
    ).get_tools(ctx := RunContext(deps=None, model=FunctionModel(lambda messages, info: "unused"), usage=RunUsage()))
    assert set(_build_catalog(tools).definitions) == {"lookup"}
    for discovered in (False, True, False):
        ctx.discovered_tool_names = {"lookup"} if discovered else set()
        assert bool(_build_catalog(tools, ctx=ctx).definitions) is discovered
        for program in (False, True):
            description = render_codeact_runner_description(tools, ctx=ctx, program=program)
            assert ("async def lookup" in description) is discovered
        assert tools["lookup"].tool_def.defer_loading
    ctx.discovered_tool_names = {"lookup"}
    assert not _build_catalog({}, ctx=ctx).definitions  # Stale discovery cannot restore a removed tool.


@pytest.mark.anyio
async def test_deferred_tools_participate_in_preparation_name_collision_checks() -> None:
    def lookup() -> int:
        return 42

    ctx = RunContext(deps=None, model=FunctionModel(lambda messages, info: "unused"), usage=RunUsage())
    tools = await CodeActPolicyToolset(
        FunctionToolset([Tool(lookup, name="look-up", defer_loading=True), Tool(lookup, name="look_up")]),
        CodeActToolPolicy(default=True),
    ).get_tools(ctx)
    with pytest.raises(UserError, match="CodeAct tool-name collision"):
        _build_catalog(tools)
