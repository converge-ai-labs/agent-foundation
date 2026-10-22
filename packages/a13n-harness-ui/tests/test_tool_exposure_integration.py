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
from a13n_harness.tools import ToolExposure, ToolsetExposure
from a13n_harness.toolsets.progressive import CONTROL_NAMES
from a13n_harness_ui.composition import AgentCompositionResolver, AgentReconstructor
from a13n_harness_ui.composition.reconstruction import _ToolAllowlistCapability
from a13n_harness_ui.configuration import (
    ResourceMutationRequest,
    load_harness_ui_configuration,
    mutate_configuration_source,
)
from a13n_harness_ui.configuration.views import agent_tool_exposure_view
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.mcp_adapters import HarnessUiMCP
from fastmcp import FastMCP
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    NativeToolSearchCallPart,
    NativeToolSearchReturnPart,
    ToolReturnPart,
)
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


@pytest.mark.parametrize("native_search", [False, True])
async def test_yaml_to_run_mixed_exposure_uses_real_mcp_and_native_plugin_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_search: bool
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

            return (Capability(id="memory-tools", toolsets=[FunctionToolset([remember], id="memory-tools")]),)

        async def for_run(self, ctx):
            plugin_events.append("run")
            return self

    class Factory(_MemoryFactory):
        def describe_toolsets(self, context):
            return {"memory-tools": "Remember useful information"}

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
    agent.update(subagents=[], capabilities=[], tools=["lookup", "remember", "status"])
    agent["progressive_search"] = "native" if native_search else "harness"
    plugin_path = tmp_path / "extensions/memory.yaml"
    plugin = yaml.safe_load(plugin_path.read_text())
    plugin["exposure"] = "progressive"
    plugin_path.write_text(yaml.safe_dump(plugin))
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
        mcp.update(transport={"url": url}, exposure="progressive", description="Search documents")
        mcp_path.write_text(yaml.safe_dump(mcp))
        source = await load_harness_ui_configuration(path)
        composition = AgentCompositionResolver(catalog).resolve_run(source, _selection())
        reconstructed = AgentReconstructor(catalog, configuration_root=tmp_path).reconstruct(
            composition,
            subagent_operator=None,
            root_capabilities=(Capability(toolsets=[FunctionToolset([status])]),),
        )
        assert binds == []
        assert plugin_events == ["agent", "collect"]

        async def run_one():
            step = 0

            async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
                nonlocal step
                assert {tool.name for tool in info.function_tools} == (
                    {"status", "lookup", "remember"} if native_search else {"status", *CONTROL_NAMES}
                )
                commands = [
                    ("search_tools", {"query": "lookup remember"}),
                    ("execute_tool", {"name": "lookup", "arguments": {}}),
                    ("execute_tool", {"name": "remember", "arguments": {}}),
                    ("status", {}),
                ]
                if native_search:
                    commands = [
                        ("lookup", {}),
                        ("remember", {}),
                        ("status", {}),
                    ]
                current = step
                step += 1
                if current < len(commands):
                    name, args = commands[current]
                    if native_search and current == 0:
                        yield {
                            0: NativeToolSearchCallPart(args={"queries": ["lookup remember"]}, tool_call_id="search")
                        }
                        yield {
                            1: NativeToolSearchReturnPart(
                                content={"discovered_tools": [{"name": "lookup"}, {"name": "remember"}]},
                                tool_call_id="search",
                            )
                        }
                    yield {2: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{current}")}
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
        assert returns[-3:] == ["document", "memory", "ready"]
        if not native_search:
            assert {item["name"] for item in returns[0]["tools"]} == {"lookup", "remember"}


@pytest.mark.parametrize(
    "names,visible", [(frozenset({"execute_tool"}), False), (frozenset({"allowed", "execute_tool"}), True)]
)
async def test_exact_target_allowlist_never_grants_all_toolset_members(names: frozenset[str], visible: bool) -> None:
    from a13n_harness import AgentSpec, HarnessBuilder

    def allowed() -> str:
        return "allowed"

    def denied() -> str:
        raise AssertionError("denied member must not execute")

    source = Capability(toolsets=[FunctionToolset([allowed, denied], id="selected")])
    allowlist = _ToolAllowlistCapability(names, optional_controls=frozenset(CONTROL_NAMES))

    async def model(messages, info):
        assert {tool.name for tool in info.function_tools} == (set(CONTROL_NAMES) if visible else set())
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(source, allowlist),
        tool_exposure=ToolExposure(
            toolsets={"selected": ToolsetExposure(exposure="progressive", description="Selected tools")}
        ),
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


async def test_configuration_mutation_reads_back_selected_source_exposure(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["mcp_servers"] = []
    agent["progressive_search"] = "native"
    result = await mutate_configuration_source(
        path, "agents/assistant.yaml", ResourceMutationRequest(content=yaml.safe_dump(agent))
    )
    saved_agent = result.configuration.agents["agent-assistant"]
    preview = agent_tool_exposure_view(result.configuration, saved_agent)
    states = {item.resource_id: item.enabled for item in preview.sources}
    assert states == {"dynamic_environment": True, "mcp-docs": False, "plugin-memory": True}
    assert all(item.exposure == "direct" for item in preview.sources)
    assert saved_agent.progressive_search == "native"
    assert saved_agent.instructions == "Root authored instructions."


async def test_direct_source_preserves_original_tool_name(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent.update(subagents=[], capabilities=[], mcp_servers=[], harness_plugins=[], tools=["search"])
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
