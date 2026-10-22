"""Progressive discovery contracts on the real Harness/ToolManager path."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from a13n_harness.tools import ToolExposure, ToolsetExposure
from pydantic_ai import Tool
from pydantic_ai.toolsets import FunctionToolset

from .test_tool_exposure import run_calls

pytestmark = pytest.mark.anyio


def _source(*tools, group="crm"):
    return FunctionToolset(list(tools), id=group)


async def _run(sources, calls):
    return await run_calls(
        list(sources),
        calls,
        policy=ToolExposure(
            toolsets={
                source.id: ToolsetExposure(exposure="progressive", description="Operations") for source in sources
            }
        ),
    )


def _operation(value: int = 1) -> int:
    return value


@pytest.mark.parametrize("query", ["github create issue", "CREATE create ISSUE", "create-issue", "createIssue"])
async def test_keywords_recall_any_word_and_rank_coverage_first(query):
    _, results = await _run(
        (
            _source(
                Tool(_operation, name="create_issue", description="Open a ticket"),
                Tool(_operation, name="delete_issue", description="Remove a ticket"),
                Tool(_operation, name="create_repository", description="Create a repository"),
                group="github",
            ),
        ),
        [("search_tools", {"query": query})],
    )
    page = results[0].content
    assert [entry["name"] for entry in page["tools"]] == ["create_issue", "create_repository", "delete_issue"]
    assert "parameters_json_schema" not in page["tools"][0]


async def test_ranking_prefers_name_words_then_whole_words_with_stable_ties():
    _, results = await _run(
        (
            _source(
                Tool(_operation, name="z_issue_create", description="A ticket"),
                Tool(_operation, name="b_create", description="An issue"),
                Tool(_operation, name="a_create", description="An issue"),
                Tool(_operation, name="c_action", description="Create an issue"),
                Tool(_operation, name="a_substring", description="Recreate issues"),
                Tool(_operation, name="create_only", description="Partial match"),
            ),
        ),
        [("search_tools", {"query": "create issue"}), ("search_tools", {"query": "absentkeyword"})],
    )
    assert [x["name"] for x in results[0].content["tools"]] == [
        "z_issue_create",
        "a_create",
        "b_create",
        "c_action",
        "a_substring",
        "create_only",
    ]
    assert results[1].content == {"tools": [], "total": 0, "partial": False}


@pytest.mark.parametrize("query", ["getHTTP", "GETHTTP", "gethttp"])
async def test_case_insensitive_exact_and_raw_substrings_survive_camel_boundaries(query):
    _, results = await _run(
        (_source(Tool(_operation, name="getHTTP"), Tool(_operation, name="getHTTPStatus")),),
        [("search_tools", {"query": query})],
    )
    assert [x["name"] for x in results[0].content["tools"]] == ["getHTTP", "getHTTPStatus"]


async def test_punctuation_only_exact_name_is_valid():
    _, results = await _run(
        (_source(Tool(_operation, name="__"), Tool(_operation, name="other")),),
        [("search_tools", {"query": " __ "})],
    )
    assert [x["name"] for x in results[0].content["tools"]] == ["__"]


async def test_parameter_search_traverses_current_input_schema_only():
    schema = {
        "type": "object",
        "properties": {
            "payload": {"anyOf": [{"$ref": "#/$defs/Node"}, {"type": "null"}]},
            "mapping": {
                "type": "object",
                "additionalProperties": {"type": "string", "description": "warehouse locator"},
            },
            "pair": {"type": "array", "prefixItems": [{"description": "fingerprint"}, {"type": "integer"}]},
        },
        "$defs": {
            "Node": {
                "properties": {
                    "customerId": {"type": "string", "description": "Purchaser reference"},
                    "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                },
                "allOf": [{"description": "nested guidance"}],
            }
        },
        "description": "Root guidance",
        "default": {"description": "defaultsecret"},
        "examples": [{"properties": {"examplesecret": {"description": "examplesecret"}}}],
        "title": "titlesecret",
        "$ref": "https://invalid.example/remotesecret",
    }
    original = json.dumps(schema, sort_keys=True)
    reads = []

    async def prepare(ctx, definition):
        return replace(definition, parameters_json_schema=schema, return_schema={"description": "returnsecret"})

    class Source(FunctionToolset):
        async def get_instructions(self, ctx):
            reads.append(True)
            return "instructionsecret"

    source = Source([Tool(_operation, name="action", description="Execute", prepare=prepare)], id="ops")
    hits = ["CUSTOMERID", "purchas", "warehouse", "fingerprint", "nested", "root", "payload"]
    misses = [
        "defaultsecret",
        "examplesecret",
        "returnsecret",
        "instructionsecret",
        "titlesecret",
        "remotesecret",
        "groupsecret",
        "Node",
    ]
    _, results = await _run((source,), [("search_tools", {"query": q}) for q in [*hits, *misses]])
    assert [r.content["total"] for r in results] == [1] * len(hits) + [0] * len(misses)
    assert json.dumps(schema, sort_keys=True) == original


async def test_parameter_search_uses_each_steps_prepared_definition():
    steps = []

    async def prepare(ctx, definition):
        steps.append(ctx.run_step)
        return replace(
            definition,
            parameters_json_schema={"type": "object", "properties": {}, "description": f"step{ctx.run_step}"},
        )

    _, results = await _run(
        (_source(Tool(_operation, prepare=prepare)),),
        [("search_tools", {"query": "step1"}), ("search_tools", {"query": "step1"})],
    )
    assert steps[:2] == [1, 2]
    assert [r.content["total"] for r in results] == [1, 0]


async def test_field_priority_counts_each_keyword_once_and_keeps_stable_ties():
    async def parameter_name(ctx, definition):
        return replace(definition, parameters_json_schema={"properties": {"needle": {"type": "string"}}})

    async def parameter_description(ctx, definition):
        return replace(definition, parameters_json_schema={"properties": {"value": {"description": "needle " * 30}}})

    sources = (
        FunctionToolset(
            [
                Tool(_operation, name="z_needle"),
                Tool(_operation, name="b_description", description="needle " * 30, prepare=parameter_name),
                Tool(_operation, name="a_description", description="needle"),
                Tool(_operation, name="c_parameter", prepare=parameter_name),
                Tool(_operation, name="d_parameter", prepare=parameter_description),
                Tool(_operation, name="a_needles"),
            ],
            id="ops",
        ),
        FunctionToolset([Tool(_operation, name="action")], id="needle"),
    )
    _, results = await _run(sources, [("search_tools", {"query": q}) for q in ["needle", "needle NEEDLE needle"]])
    expected = ["z_needle", "a_description", "b_description", "action", "c_parameter", "d_parameter", "a_needles"]
    assert all([x["name"] for x in r.content["tools"]] == expected for r in results)
