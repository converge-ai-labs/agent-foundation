from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import replace

import pytest
from a13n_harness import HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities.codeact import CodeActCapability
from a13n_harness.codeact.config import CodeActConfig
from a13n_harness.toolsets.codeact import (
    CodeActPolicyToolset,
    CodeActToolPolicy,
    _build_catalog,
    render_codeact_catalog,
)
from pydantic_ai import FunctionToolset, RunContext, Tool, ToolDefinition
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import (
    InstructionDeltaPart,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import RunUsage


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_catalog_argument_schemas_requires_a_strict_bool(value) -> None:
    with pytest.raises(TypeError, match="catalog_argument_schemas"):
        CodeActConfig(catalog_argument_schemas=value)


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
    before = render_codeact_catalog(tools, ctx=ctx, catalog_argument_schemas=True)
    after = render_codeact_catalog({**tools, "lookup-items": updated}, ctx=ctx, catalog_argument_schemas=True)
    assert '"count"' in before
    assert '"query"' in after
    assert '"count"' not in after
    default = render_codeact_catalog(tools, ctx=ctx, catalog_argument_schemas=False)
    assert default == (
        "CodeAct callable host tools. Call them with the same keyword arguments as the matching tool definitions.\n"
        "`lookup_items` (tool `lookup-items`)"
    )
    assert "async def" not in after and "Return JSON Schema" not in after
    assert "lookup(count" not in after
    assert "denied" not in after
    assert "(tool `lookup-items`)" in after
    encoded_schema = after.split("Arguments JSON Schema: ", 1)[1].splitlines()[0]
    assert json.loads(encoded_schema) == updated_schema
    assert encoded_schema == json.dumps(updated_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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
        description = render_codeact_catalog(tools, ctx=ctx, catalog_argument_schemas=False)
        assert ("`lookup`" in description) is discovered
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


@pytest.mark.anyio
@pytest.mark.parametrize("argument_schemas", [False, True])
async def test_catalog_changes_append_once_without_rewriting_runner_definitions_or_prefix(argument_schemas) -> None:
    requests = 0
    prefixes: list[str | None] = []
    runners: list[list[ToolDefinition]] = []

    def lookup(value: int) -> int:
        return value

    async def prepare(ctx, definition):
        if requests >= 4:
            return None
        if requests >= 2:
            return replace(
                definition,
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            )
        return definition

    async def model(messages, info):
        nonlocal requests
        prefixes.append(info.instructions)
        runners.append([tool for tool in info.function_tools if tool.name in {"run_code", "run_program"}])
        requests += 1
        if requests < 6:
            yield {0: DeltaToolCall(name="run_code", json_args='{"code":"1"}', tool_call_id=f"step-{requests}")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Capability(
                toolsets=[
                    CodeActPolicyToolset(
                        FunctionToolset([Tool(lookup, prepare=prepare)]), CodeActToolPolicy(default=True)
                    )
                ]
            ),
            CodeActCapability(CodeActConfig(catalog_argument_schemas=argument_schemas)),
        ),
    )
    result = await executable.run("run", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert requests == 6
    assert all(definitions == runners[0] for definitions in runners)
    assert all(prefix == prefixes[0] for prefix in prefixes)
    assert prefixes[0] is not None and prefixes[0].count("`lookup`") == 1
    assert ('"type":"integer"' in prefixes[0]) is argument_schemas
    changes = [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, InstructionDeltaPart) and str(part.id) == "capability:a13n.codeact:catalog"
    ]
    assert len(changes) == (2 if argument_schemas else 1)
    if argument_schemas:
        assert changes[0].content is not None and '"type":"string"' in changes[0].content
    assert changes[-1].content is not None and "`lookup`" not in changes[-1].content
    assert "`store`" in changes[-1].content

    # A new native run resumes the same instruction window without restoring the
    # removed tool or appending the unchanged stored-values-only catalog again.
    assert result.state is not None
    resumed = await executable.run("continue", bindings=RunBindings.embedded(), previous_state=result.state)
    assert resumed.output_or_raise() == "done"
    assert prefixes[-1] == prefixes[0]
    resumed_changes = [
        part
        for message in resumed.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, InstructionDeltaPart) and str(part.id) == "capability:a13n.codeact:catalog"
    ]
    assert resumed_changes == changes

    # A suspended provider response continues the exact prior request: its
    # original catalog baseline plus the already-recorded changes.
    history = deepcopy(list(result.state.message_history))
    assert isinstance(history[-1], ModelResponse)
    history[-1].state = "suspended"
    suspended = result.state.model_copy(
        update={"message_history_json": ModelMessagesTypeAdapter.dump_json(history).decode()}
    )
    suspended = HarnessState.model_validate_json(suspended.model_dump_json())
    continued = await executable.run(None, bindings=RunBindings.embedded(), previous_state=suspended)
    assert continued.output_or_raise()
    assert prefixes[-1] == prefixes[0]
    assert runners[-1] == runners[0]


@pytest.mark.anyio
async def test_catalog_names_have_native_definitions_in_each_request_after_discovery() -> None:
    from pydantic_ai.capabilities import ToolSearch

    requests = 0
    catalogs = []

    def lookup() -> int:
        return 42

    async def model(messages, info):
        nonlocal requests
        # FunctionModel receives native instruction deltas projected as user parts.
        visible = (
            (info.instructions or "")
            + "\n"
            + "\n".join(
                part.content
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart) and isinstance(part.content, str)
            )
        )
        catalog = re.findall(
            r'<pyai:context id="capability:a13n.codeact:catalog">\n(.*?)\n</pyai:context>', visible, re.S
        )[-1]
        entries = [line for line in catalog.splitlines() if line.startswith("`")]
        native_names = {tool.name for tool in info.function_tools}
        for entry in entries:
            name = entry.split("`")[1]
            canonical = entry.split("(tool `")[1].split("`")[0] if "(tool `" in entry else name
            assert canonical in native_names
        catalogs.append(entries)
        requests += 1
        if requests == 1:
            yield {0: DeltaToolCall(name="search_tools", json_args='{"queries":["deferred"]}', tool_call_id="search-1")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Capability(
                toolsets=[
                    CodeActPolicyToolset(
                        FunctionToolset(
                            [Tool(lookup, name="crm.lookup"), Tool(lookup, name="deferred", defer_loading=True)]
                        ),
                        CodeActToolPolicy(default=True),
                    )
                ]
            ),
            ToolSearch(strategy="keywords"),
            CodeActCapability(),
        ),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert requests == 2
    assert "`crm_lookup` (tool `crm.lookup`)" in catalogs[0]
    assert "`deferred`" not in catalogs[0]
    assert "`deferred`" in catalogs[1]
