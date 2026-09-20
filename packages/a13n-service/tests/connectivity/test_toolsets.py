import json

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_service.connectivity.naming import portable_tool_name
from a13n_service.connectivity.toolsets import local_capability, selected_tools
from mcp.types import Tool
from pydantic_ai import Agent
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("defer_loading", [False, True])
@pytest.mark.parametrize("native_name", ["notion-list-private-pages", "read.documents", "r" * 128])
async def test_local_capabilities_isolate_equal_tool_names_and_enforce_scope(defer_loading, native_name):
    calls = []
    tools = [
        Tool(name=native_name, inputSchema={"type": "object", "properties": {}, "additionalProperties": False}),
        Tool(name="write", inputSchema={"type": "object"}),
    ]

    def handler(account):
        async def call(name, arguments):
            calls.append((account, name, arguments))
            return {"account": account}

        return call

    first = await local_capability(
        key="conn_one",
        model_alias="first",
        tools=tools,
        allowed=(native_name,),
        handler=handler("one"),
        defer_loading=defer_loading,
    )
    second = await local_capability(
        key="conn_two",
        model_alias="second",
        tools=tools,
        allowed=(native_name,),
        handler=handler("two"),
        defer_loading=defer_loading,
    )
    assert first is not None and second is not None
    step = 0

    async def model(messages, info):
        nonlocal step
        visible = {tool.name for tool in info.function_tools}
        if defer_loading and step == 0:
            assert visible == {"load_capability"}
            calls = [("load_capability", {"id": cap.id}) for cap in (first, second)]
        elif step == int(defer_loading):
            names = [f"{cap.id}_{portable_tool_name(native_name)}" for cap in (first, second)]
            assert set(names) <= visible
            assert all(len(name) <= 64 and all(char.isalnum() or char in "_-" for char in name) for name in names)
            calls = [(name, {}) for name in names]
        else:
            yield "done"
            return
        step += 1
        yield {
            index: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=f"call-{step}-{index}")
            for index, (name, arguments) in enumerate(calls)
        }

    agent = HarnessBuilder().build(AgentSpec(), model=FunctionModel(stream_function=model), output_type=str)
    result = await agent.run("read both", bindings=RunBindings.embedded(capabilities=(first, second)))
    result.output_or_raise()
    assert sorted(calls) == [("one", native_name, {}), ("two", native_name, {})]


def test_short_native_tool_name_needs_no_hash():
    assert portable_tool_name("notion-list-private-pages") == "notion-list-private-pages"


async def test_missing_explicit_tool_fails_preparation_and_empty_scope_has_no_capability():
    async def call(name, arguments):
        raise AssertionError("empty selections never execute")

    tools = [Tool(name="read", inputSchema={"type": "object"})]
    with pytest.raises(ValueError, match="selected_tool_unavailable"):
        selected_tools(tools, ("missing",))
    assert await local_capability(key="empty", model_alias="empty", tools=tools, allowed=(), handler=call) is None


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "invalid"},
        {"type": "object", "$ref": "https://external.example/schema"},
        {"type": "object", "$dynamicRef": "https://external.example/schema"},
        {"type": "object", "$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"},
    ],
)
def test_discovery_rejects_invalid_or_unbounded_schema_references(schema):
    from jsonschema.exceptions import SchemaError

    with pytest.raises((ValueError, SchemaError)):
        selected_tools([Tool(name="unsafe", inputSchema=schema)], None)


async def test_oversized_result_fails_without_repeating_effect():
    calls = []

    async def call(name, arguments):
        calls.append(name)
        return {"large": "x" * (1024 * 1024)}

    capability = await local_capability(
        key="source",
        model_alias="source",
        tools=[Tool(name="write", inputSchema={"type": "object"})],
        allowed=None,
        handler=call,
    )
    with pytest.raises(Exception, match="tool_result_too_large"):
        await Agent(TestModel(), capabilities=[capability]).run("write")
    assert calls == ["write"]


async def test_invalid_arguments_retry_without_dispatching_invalid_effect():
    calls = []
    step = 0

    async def call(name, arguments):
        calls.append(arguments)
        return {"kind": "succeeded"}

    capability = await local_capability(
        key="source",
        model_alias="source",
        allowed=None,
        handler=call,
        tools=[
            Tool(
                name="reply",
                inputSchema={
                    "type": "object",
                    "properties": {"placement": {"enum": ["thread", "main"]}},
                    "additionalProperties": False,
                },
            )
        ],
    )

    async def model(messages, info):
        nonlocal step
        step += 1
        if step == 1:
            yield {0: DeltaToolCall(name="source_reply", json_args='{"placement":"dialog"}', tool_call_id="bad")}
        elif step == 2:
            assert "nothing was dispatched" in repr(messages)
            yield {0: DeltaToolCall(name="source_reply", json_args='{"placement":"thread"}', tool_call_id="good")}
        else:
            yield "done"

    agent = HarnessBuilder().build(AgentSpec(), model=FunctionModel(stream_function=model), output_type=str)
    result = await agent.run("reply", bindings=RunBindings.embedded(capabilities=(capability,)))
    assert result.output_or_raise() == "done"
    assert calls == [{"placement": "thread"}]
