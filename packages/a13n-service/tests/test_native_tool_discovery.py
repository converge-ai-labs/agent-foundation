"""Service selects deferred tools; upstream discovery retains direct dispatch and permissions."""

import pytest
from a13n_harness import DeferredToolResume, RunBindings
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents import toolsets
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride, NativeToolSearch, apply_override
from a13n_service.runs.agent import build
from pydantic import ValidationError
from pydantic_ai import Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import NativeToolSearchCallPart, NativeToolSearchReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.toolsets import FunctionToolset

from .test_agents import PLUGINS, config, resolved

pytestmark = pytest.mark.anyio
CONNECTION = new_object_id("con")


def test_native_configuration_round_trip_and_override() -> None:
    selected = config(tool_search={"mode": "native"})
    assert isinstance(selected.tool_search, NativeToolSearch)
    assert selected.tool_search.sources == toolsets.AutomaticToolSearch()
    assert AgentConfig.model_validate_json(selected.model_dump_json()) == selected
    proxy = config(tool_search={"automatic": {}})
    assert apply_override(proxy, AgentOverride(tool_search={"mode": "native"})).tool_search == selected.tool_search
    assert apply_override(selected, AgentOverride(tool_search=None)).tool_search == selected.tool_search
    assert apply_override(selected, AgentOverride(tool_search={"groups": {}})).tool_search != selected.tool_search
    for extra in ({"groups": {}}, {"automatic": {}}, {"config": {"mode": "proxy"}}, {"plugins": ["audit"]}):
        with pytest.raises(ValidationError):
            config(tool_search={"mode": "native", **extra})


@pytest.mark.parametrize("permission", ["allow", "ask", "deny"])
@pytest.mark.parametrize("provider_search", [False, True])
async def test_native_search_direct_call_resume_and_permissions(permission: str, provider_search: bool) -> None:
    effects = []

    def lookup(value: int) -> int:
        """Lookup a customer value."""
        effects.append(value)
        return value * 2

    source = Capability(toolsets=[FunctionToolset([lookup], id=CONNECTION)])
    selected = config(
        connection_tools=[{"connection_id": CONNECTION, "permission": permission}],
        tool_search={"mode": "native"},
    )
    root = resolved(
        new_object_id("ap"),
        new_object_id("apr"),
        selected,
        connections={CONNECTION: toolsets.ConnectionInfo(name="CRM", type="connector")},
    )
    phase, calls = "search", 0

    async def stream(messages, info):
        nonlocal calls
        names = {tool.name for tool in info.function_tools}
        assert not {"discover_tools", "call_tool", "list_tool_groups", "load_capability"} & names
        if phase == "search" and calls == 0:
            if provider_search:
                params = info.model_request_parameters
                assert any(tool.kind == "tool_search" for tool in params.native_tools)
                target = next(tool for tool in params.function_tools if tool.name == "lookup")
                assert target.defer_loading and target.with_native == "tool_search"
                yield {
                    0: NativeToolSearchCallPart(
                        args={"queries": ["lookup"]}, tool_call_id="search", provider_name="openai"
                    )
                }
                yield {
                    1: NativeToolSearchReturnPart(
                        content={"discovered_tools": [{"name": "lookup"}]},
                        tool_call_id="search",
                        provider_name="openai",
                    )
                }
                yield "done"
                return
            assert "search_tools" in names and "lookup" not in names
            calls += 1
            yield {0: DeltaToolCall(name="search_tools", json_args='{"queries":["lookup"]}', tool_call_id="search")}
        elif phase == "call" and calls == 0:
            assert "lookup" in names
            calls += 1
            yield {0: DeltaToolCall(name="lookup", json_args='{"value":4}', tool_call_id="invoke")}
        else:
            assert "lookup" in names
            yield "done"

    async def model_resolver(context, model_id):
        # Switching to a non-native provider on continuation also verifies portable discovery history.
        profile = None if provider_search and phase == "search" else ModelProfile(supported_native_tools=frozenset())
        return FunctionModel(stream_function=stream, profile=profile)

    def executable():
        return build(root, capabilities=lambda _: [source], plugins=PLUGINS, instrumentation=None)

    bindings = RunBindings.embedded(model_resolver=model_resolver)
    first = await executable().run("find", bindings=bindings)
    assert first.output_or_raise() == "done" and effects == []
    state = type(first.state).model_validate_json(first.state.model_dump_json())
    phase, calls = "call", 0
    result = await executable().run("use it", previous_state=state, bindings=bindings)
    if permission == "ask":
        assert result.status == "suspended" and effects == []
        pending = result.deferred
        assert pending is not None
        [request] = pending.approvals
        assert request.model_call.tool_name == request.target_call.tool_name == "lookup"
        state = type(result.state).model_validate_json(result.state.model_dump_json())
        result = await executable().run(
            previous_state=state,
            bindings=bindings,
            deferred_resume=DeferredToolResume(
                pending, pending.build_results(approvals={request.target_call.tool_call_id: True})
            ),
        )
    assert result.output_or_raise() == "done"
    assert effects == ([] if permission == "deny" else [4])


@pytest.mark.parametrize("empty", [False, True])
async def test_native_source_selection_preserves_direct_tools_and_capability_instructions(empty: bool) -> None:
    excluded = new_object_id("con")
    catalogue = toolsets.catalog(frozenset())
    computer_id = next(group.tools[0].execution_id for group in catalogue.items if group.key == "computer")
    file_id = next(group.tools[0].execution_id for group in catalogue.items if group.key == "files")
    platform_id = next(group.tools[0].execution_id for group in catalogue.items if group.platform)

    def computer() -> str:
        return "computer"

    def files() -> str:
        return "files"

    def platform() -> str:
        return "platform"

    def direct_connection() -> str:
        return "excluded"

    def plugin_tool() -> str:
        return "plugin"

    source = Capability(
        instructions="Keep source instructions active before discovery.",
        toolsets=[
            FunctionToolset(
                [
                    Tool(computer, metadata={TOOL_IDENTITY_KEY: ToolIdentity(computer_id)}),
                    Tool(files, metadata={TOOL_IDENTITY_KEY: ToolIdentity(file_id)}),
                    Tool(platform, metadata={TOOL_IDENTITY_KEY: ToolIdentity(platform_id)}),
                ],
                id="service",
            )
        ],
    )
    selected = config(
        connection_tools=[{"connection_id": excluded}],
        tool_search={
            "mode": "native",
            "sources": {"capabilities": [], "connections": False, "exclude_platform": True}
            if empty
            else {"exclude_connections": [excluded]},
        },
        user_questions=True,
    )
    root = resolved(
        new_object_id("ap"),
        new_object_id("apr"),
        selected,
        connections={excluded: toolsets.ConnectionInfo(name="Direct", type="connector")},
    )

    async def stream(messages, info):
        names = {tool.name for tool in info.function_tools}
        assert {"files", "direct_connection", "plugin_tool", "ask_user_question"} <= names
        assert "call_tool" not in names
        if empty:
            assert {"computer", "platform"} <= names
            assert "search_tools" not in names
        else:
            assert "search_tools" in names
            assert not {"computer", "platform"} & names
        assert "Keep source instructions active" in info.instructions
        yield "done"

    async def model_resolver(context, model_id):
        return FunctionModel(stream_function=stream, profile=ModelProfile(supported_native_tools=frozenset()))

    executable = build(
        root,
        capabilities=lambda _: [
            source,
            Capability(toolsets=[FunctionToolset([direct_connection], id=excluded)]),
            Capability(tools=[plugin_tool]),
        ],
        plugins=PLUGINS,
        instrumentation=None,
    )
    assert executable.definition.tool_search is None
    result = await executable.run("go", bindings=RunBindings.embedded(model_resolver=model_resolver))
    assert result.output_or_raise() == "done"
