from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn
import yaml
from a13n_harness import RunBindings
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
from a13n_harness_ui.composition import AgentCompositionResolver, AgentReconstructor
from a13n_harness_ui.composition.reconstruction import _ToolAllowlistCapability
from a13n_harness_ui.configuration import (
    ResourceMutationRequest,
    load_harness_ui_configuration,
    mutate_configuration_source,
)
from a13n_harness_ui.configuration.views import agent_tool_search_view
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.mcp_adapters import HarnessUiMCP
from fastmcp import FastMCP
from pydantic_ai.capabilities import Capability, ToolSearch
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from .test_composition import _MemoryFactory, _MemoryPlugin, _selection, _write_source

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def _http_mcp(server: FastMCP):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        http = uvicorn.Server(
            uvicorn.Config(
                server.http_app(path="/mcp", stateless_http=True, json_response=True),
                log_level="error",
                access_log=False,
            )
        )
        task = asyncio.create_task(http.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not http.started:
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
        finally:
            http.should_exit = True
            async with asyncio.timeout(10):
                await task


async def test_yaml_to_run_mixed_proxy_uses_real_mcp_and_native_plugin_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FastMCP("ui-proxy")
    calls: list[str] = []
    binds: list[str] = []
    plugin_events: list[str] = []

    @server.tool()
    def lookup() -> str:
        calls.append("mcp")
        return "document"

    class Plugin(_MemoryPlugin):
        def for_agent(self):
            plugin_events.append("agent")
            return self

        def get_capabilities(self):
            plugin_events.append("collect")

            def remember() -> str:
                calls.append("plugin")
                return "memory"

            return (Capability(id="memory-tools", toolsets=[FunctionToolset([remember])]),)

        async def for_run(self, ctx):
            plugin_events.append("run")
            return self

    class Factory(_MemoryFactory):
        def create_plugin(self, context):
            return Plugin(context.plugin_id)

    original = HarnessUiMCP.for_run

    async def bind(source, ctx):
        binds.append(ctx.deps.run_id)
        return await original(source, ctx)

    monkeypatch.setattr(HarnessUiMCP, "for_run", bind)
    catalog = HarnessUiExtensionCatalog(host_plugin_factories=(Factory(),))
    path = _write_source(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent.update(subagents=[], capabilities=[], tools=["knowledge__lookup", "knowledge__remember", "status"])
    agent["tool_search"] = {
        "config": {"mode": "proxy"},
        "groups": {
            "knowledge": {
                "description": "Documents and memory",
                "mcp_servers": ["mcp-docs"],
                "harness_plugins": ["plugin-memory"],
            }
        },
    }
    agent_path.write_text(yaml.safe_dump(agent))
    # This reconstruction test supplies RunBindings directly, without an App-owned runtime.
    path.write_text(
        path.read_text()
        + "tools: {enable_codeact: false, enable_ask_user_question: false, execution: {mode: inline}}\n"
    )

    def status() -> str:
        return "ready"

    async with _http_mcp(server) as url:
        mcp_path = tmp_path / "mcp/docs.yaml"
        mcp = yaml.safe_load(mcp_path.read_text())
        mcp["transport"] = {"url": url}
        mcp_path.write_text(yaml.safe_dump(mcp))
        source = await load_harness_ui_configuration(path)
        composition = AgentCompositionResolver(catalog).resolve_run(source, _selection())
        reconstructed = AgentReconstructor(catalog, configuration_root=tmp_path).reconstruct(
            composition,
            subagent_operator=None,
            root_capabilities=(Capability(toolsets=[FunctionToolset([status])]), ToolSearch(strategy="keywords")),
        )
        assert binds == []
        assert plugin_events == ["agent", "collect"]

        async def run_one():
            step = 0

            async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
                nonlocal step
                assert {tool.name for tool in info.function_tools} == {
                    "status",
                    "discover_tools",
                    "describe_tool",
                    "call_tool",
                    "list_tools",
                    "list_tool_groups",
                }
                commands = [
                    ("discover_tools", {"query": "", "group": "knowledge"}),
                    ("call_tool", {"group": "knowledge", "tool": "lookup", "arguments": {}}),
                    ("call_tool", {"group": "knowledge", "tool": "remember", "arguments": {}}),
                    ("status", {}),
                ]
                current = step
                step += 1
                if current < len(commands):
                    name, args = commands[current]
                    yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{current}")}
                else:
                    yield "done"

            async def resolve(ctx, model_id):
                from pydantic_ai.profiles.anthropic import anthropic_model_profile

                return FunctionModel(stream_function=model, profile=anthropic_model_profile("claude-sonnet-4-6"))

            return await reconstructed.executable.run("test", bindings=RunBindings.embedded(model_resolver=resolve))

        results = await asyncio.gather(run_one(), run_one())
    assert len(binds) == len(set(binds)) == 2
    assert plugin_events == ["agent", "collect", "run", "run"]
    assert sorted(calls) == ["mcp", "mcp", "plugin", "plugin"]
    for result in results:
        assert result.output_or_raise() == "done"
        returns = [
            part.content
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert returns[1:] == ["document", "memory", "ready"]
        assert {item["tool"] for item in returns[0]["tools"]} == {"lookup", "remember"}


@pytest.mark.parametrize(
    "names,visible", [(frozenset({"call_tool"}), False), (frozenset({"group__allowed", "call_tool"}), True)]
)
async def test_exact_target_allowlist_never_grants_all_group_members(names: frozenset[str], visible: bool) -> None:
    from a13n_harness import AgentSpec, HarnessBuilder

    def allowed() -> str:
        return "allowed"

    def denied() -> str:
        raise AssertionError("denied member must not execute")

    source = Capability(toolsets=[FunctionToolset([allowed, denied])])
    allowlist = _ToolAllowlistCapability(names, optional_controls=frozenset({"call_tool", "discover_tools"}))

    async def model(messages, info):
        assert {tool.name for tool in info.function_tools} == (
            {"discover_tools", "describe_tool", "call_tool", "list_tools", "list_tool_groups"} if visible else set()
        )
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(source, allowlist),
        tool_search=ToolSearchPlan(groups={"group": ToolSearchSelection("Selected tools", capabilities=(source,))}),
    )
    assert (await executable.run("test", bindings=RunBindings.embedded())).output_or_raise() == "done"


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("selected", [False, True])
async def test_allowlist_filters_bound_execution_controls_without_resurrecting_unbound_tools(bound, selected):
    from a13n_harness import AgentSpec, HarnessBuilder, ToolExecutionRuntime
    from a13n_harness.capabilities import ToolExecutionCapability

    controls = {f"tool_execution_{action}" for action in ("info", "result", "wait", "cancel")}
    allowlist = _ToolAllowlistCapability(frozenset({"tool_execution_info"} if selected else set()))
    runtime = ToolExecutionRuntime()
    requests = 0

    async def model(messages, info):
        nonlocal requests
        requests += 1
        assert controls.intersection(tool.name for tool in info.function_tools) == (
            {"tool_execution_info"} if bound and selected else set()
        )
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(ToolExecutionCapability(), allowlist),
    )
    try:
        result = await executable.run(
            "inspect", bindings=RunBindings.embedded(tool_execution=runtime if bound else None)
        )
        if selected and not bound:
            # Explicit selection is strict: missing controls fail preparation,
            # rather than becoming callable through an allowlist entry.
            assert result.status == "failed" and requests == 0
            assert result.failure.details["exception_type"] == "CompositionError"
        else:
            assert result.output_or_raise() == "done" and requests == 1
    finally:
        await runtime.close()


async def test_configuration_mutation_reads_back_static_active_dormant_direct_sources(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["mcp_servers"] = []
    agent["tool_search"] = {"groups": {"knowledge": {"description": "Documents", "mcp_servers": ["mcp-docs"]}}}
    result = await mutate_configuration_source(
        path, "agents/assistant.yaml", ResourceMutationRequest(content=yaml.safe_dump(agent))
    )
    saved_agent = result.configuration.agents["agent-assistant"]
    preview = agent_tool_search_view(result.configuration, saved_agent)
    states = {item.resource_id: item.presentation for item in preview.sources}
    assert states == {"mcp-docs": "dormant", "plugin-memory": "direct", "dynamic_environment": "direct"}
    assert saved_agent.tool_search is not None
    assert saved_agent.instructions == "Root authored instructions."


@pytest.mark.parametrize("dormant", [False, True])
async def test_inactive_plan_preserves_direct_tool_matching_control_name(tmp_path: Path, dormant: bool) -> None:
    path = _write_source(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent.update(subagents=[], capabilities=[], mcp_servers=[], harness_plugins=[], tools=["search"])
    agent["tool_search"] = {
        "groups": {"docs": {"description": "Documents", "mcp_servers": ["mcp-docs"]}} if dormant else {},
        "config": {"mode": "proxy", "search_name": "search"},
    }
    agent_path.write_text(yaml.safe_dump(agent))
    # This reconstruction test supplies RunBindings directly, without an App-owned runtime.
    path.write_text(
        path.read_text()
        + "tools: {enable_codeact: false, enable_ask_user_question: false, execution: {mode: inline}}\n"
    )
    configuration = await load_harness_ui_configuration(path)
    selection = replace(_selection(), mcp_server_ids=(), harness_plugin_ids=())
    catalog = HarnessUiExtensionCatalog(host_plugin_factories=(_MemoryFactory(),))
    composition = AgentCompositionResolver(catalog).resolve_run(configuration, selection)

    def search() -> str:
        return "direct result"

    reconstructed = AgentReconstructor(catalog, configuration_root=tmp_path).reconstruct(
        composition,
        subagent_operator=None,
        root_capabilities=(Capability(toolsets=[FunctionToolset([search])]),),
    )

    async def model(messages, info):
        assert {tool.name for tool in info.function_tools} == {"search"}
        yield "done"

    async def resolve(ctx, model_id):
        return FunctionModel(stream_function=model)

    result = await reconstructed.executable.run("test", bindings=RunBindings.embedded(model_resolver=resolve))
    assert result.output_or_raise() == "done"


@pytest.mark.parametrize("enabled", [False, True])
async def test_local_capability_group_uses_selected_source_without_activation(tmp_path: Path, enabled: bool) -> None:
    path = _write_source(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent.update(
        subagents=[],
        mcp_servers=[],
        harness_plugins=[],
        tools=None,
        capabilities=[{"capability": "working_state"}] if enabled else [],
    )
    agent["tool_search"] = {
        "groups": {"state": {"description": "Notes and tasks", "capabilities": ["working_state" if enabled else "web"]}}
    }
    agent_path.write_text(yaml.safe_dump(agent))
    path.write_text(
        path.read_text()
        + "tools: {enable_codeact: false, enable_ask_user_question: false, execution: {mode: inline}}\n"
    )
    configuration = await load_harness_ui_configuration(path)
    preview = agent_tool_search_view(configuration, configuration.agents["agent-assistant"])
    [source] = [item for item in preview.sources if item.resource_id == ("working_state" if enabled else "web")]
    assert source.kind == "capability"
    assert source.presentation == ("active" if enabled else "dormant")
    catalog = HarnessUiExtensionCatalog(host_plugin_factories=(_MemoryFactory(),))
    composition = AgentCompositionResolver(catalog).resolve_run(
        configuration, replace(_selection(), mcp_server_ids=(), harness_plugin_ids=())
    )
    reconstructed = AgentReconstructor(catalog, configuration_root=tmp_path).reconstruct(
        composition, subagent_operator=None
    )
    step = 0

    async def model(messages, info):
        nonlocal step
        names = {tool.name for tool in info.function_tools}
        assert ("call_tool" in names) is enabled
        assert ("note_write" not in names) is enabled
        if enabled and step == 0:
            step += 1
            yield {
                0: DeltaToolCall(
                    name="call_tool",
                    json_args=json.dumps(
                        {"group": "state", "tool": "note_write", "arguments": {"key": "topic", "value": "saved"}}
                    ),
                    tool_call_id="write",
                )
            }
        else:
            yield "done"

    async def resolve(ctx, model_id):
        return FunctionModel(stream_function=model)

    result = await reconstructed.executable.run("test", bindings=RunBindings.embedded(model_resolver=resolve))
    assert result.output_or_raise() == "done"
    if enabled:
        returns = [
            part
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert len(returns) == 1
        assert "error" not in str(returns[0].content).lower()
