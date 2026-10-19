"""Service selects presentation; Harness owns discovery and local invocation."""

from typing import Any

import pytest
from a13n_harness import RunBindings
from a13n_harness.errors import DefinitionError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents import definition
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride, apply_override
from a13n_service.runs.agent import build
from pydantic import ValidationError
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import NativeToolSearchCallPart, NativeToolSearchReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.profiles.anthropic import anthropic_model_profile
from pydantic_ai.toolsets import FunctionToolset

from .test_agents import HELPER, HELPER_REVISION, PLUGINS, config, resolved

pytestmark = pytest.mark.anyio
CONNECTION = new_object_id("con")


def selection(**changes: Any) -> AgentConfig:
    return config(
        connection_tools=[{"connection_id": CONNECTION, "defer_loading": True}],
        tool_search={
            "config": {"mode": "native"},
            "groups": {"knowledge": {"description": "Knowledge lookup", "connections": [CONNECTION]}},
        },
        **changes,
    )


def test_configuration_preserves_defaults_and_validates_membership() -> None:
    assert "tool_search" not in config().model_dump(mode="json")
    selected = selection()
    assert AgentConfig.model_validate_json(selected.model_dump_json()) == selected
    assert selected.connection_tools[0].defer_loading
    assert selected.tool_search is not None
    assert selected.tool_search.connection_ids == {CONNECTION}
    for groups, message in (
        ({"bad__name": {"description": "Tools"}}, "ToolSearch group"),
        ({"tools": {"description": " "}}, "non-blank"),
        ({"tools": {"description": "Tools", "connections": [new_object_id("con")]}}, "unselected connection"),
        ({"tools": {"description": "Tools", "plugins": ["missing"]}}, "unselected plugin"),
        ({"tools": {"description": "Tools", "platform": True}}, "enabled platform tool"),
        (
            {name: {"description": "Tools", "connections": [CONNECTION]} for name in ("one", "two")},
            "multiple groups",
        ),
    ):
        with pytest.raises(ValidationError, match=message):
            AgentConfig.model_validate({**selected.model_dump(), "tool_search": {"groups": groups}})


def test_override_replaces_the_plan_and_validates_the_result() -> None:
    selected = selection()
    for override in ({}, {"tool_search": None}):
        assert apply_override(selected, AgentOverride.model_validate(override)).tool_search == selected.tool_search
    disabled = apply_override(selected, AgentOverride.model_validate({"tool_search": {"groups": {}}}))
    assert disabled.tool_search is not None and not disabled.tool_search.groups
    with pytest.raises(ValidationError, match="unselected connection"):
        apply_override(selected, AgentOverride(connection_tools=()))
    cleared = apply_override(selected, AgentOverride.model_validate({"connection_tools": [], "tool_search": {}}))
    assert not cleared.connection_tools


def test_plan_uses_selected_instances_and_child_has_its_own_configuration() -> None:
    from a13n_service.runs.agent import ResolvedSubagent

    connection = Capability(tools=[lambda: "connection"])
    platform = Capability(tools=[lambda: "platform"])
    selected = config(
        connection_tools=[{"connection_id": CONNECTION}],
        plugins=[{"instance_name": "audit", "plugin_key": "test.audit"}],
        toolsets={"agents": {"enabled": True}},
        tool_search={
            "config": {"mode": "native"},
            "groups": {
                "tools": {
                    "description": "Selected tools",
                    "connections": [CONNECTION],
                    "plugins": ["audit"],
                    "platform": True,
                }
            },
        },
        subagents={"helper": {"agent_id": HELPER, "revision_id": HELPER_REVISION}},
    )
    sources = {CONNECTION: connection, "platform": platform}
    plan = definition.tool_search(selected, sources)
    assert plan is not None and plan.config.mode == "native"
    assert plan.groups["tools"].capabilities == (connection, platform)
    assert plan.groups["tools"].plugins == ("audit",)
    child = resolved(HELPER, HELPER_REVISION, config())
    root = resolved(
        new_object_id("ap"),
        new_object_id("apr"),
        selected,
        connection_types={CONNECTION: "mcp"},
        subagents={"helper": ResolvedSubagent(selected.subagents["helper"], HELPER_REVISION, "Help", child)},
    )
    executable = build(
        root,
        capabilities=lambda node: list(sources.values()) if node is root else [],
        tool_sources=lambda node: sources if node is root else {},
        plugins=PLUGINS,
        instrumentation=None,
    )
    assert executable.definition.tool_search == plan
    assert executable.definition.subagents[0].agent.tool_search is None


@pytest.mark.parametrize("permission", ["allow", "ask", "deny"])
async def test_native_discovery_restores_history_and_keeps_connection_permissions(permission: str) -> None:
    effects = []

    def lookup(value: int) -> int:
        effects.append(value)
        return value * 2

    source = Capability(toolsets=[FunctionToolset([lookup], id=CONNECTION)])
    selected = config(
        toolsets={},
        connection_tools=[{"connection_id": CONNECTION, "permission": permission}],
        tool_search=selection().tool_search,
    )
    root = resolved(new_object_id("ap"), new_object_id("apr"), selected, connection_types={CONNECTION: "connector"})
    phase = "discover"
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        tools = {tool.name: tool for tool in info.function_tools}
        assert "load_capability" not in tools
        assert "call_proxy_tool" not in tools and "search_tools" not in tools
        assert tools["knowledge__lookup"].defer_loading
        assert any(
            tool.kind == "tool_search" and not tool.optional for tool in info.model_request_parameters.native_tools
        )
        if phase == "discover":
            yield {0: NativeToolSearchCallPart(args={"queries": ["lookup"]}, tool_call_id="search")}
            yield {
                1: NativeToolSearchReturnPart(
                    content={"discovered_tools": [{"name": "knowledge__lookup"}]}, tool_call_id="search"
                )
            }
            yield "found"
        elif calls == 0:
            calls += 1
            yield {0: DeltaToolCall(name="knowledge__lookup", json_args='{"value":4}', tool_call_id="invoke")}
        else:
            yield "done"

    async def model_resolver(context, model_id):
        return FunctionModel(stream_function=stream, profile=anthropic_model_profile("claude-sonnet-4-6"))

    executable = build(
        root,
        capabilities=lambda _: [source],
        tool_sources=lambda _: {CONNECTION: source},
        plugins=PLUGINS,
        instrumentation=None,
    )
    bindings = RunBindings.embedded(model_resolver=model_resolver)
    first = await executable.run("find", bindings=bindings)
    assert first.output_or_raise() == "found"
    state = type(first.state).model_validate_json(first.state.model_dump_json())
    phase = "invoke"
    result = await executable.run("call", previous_state=state, bindings=bindings)
    assert result.status == ("suspended" if permission == "ask" else "completed")
    assert effects == ([4] if permission == "allow" else [])


async def test_unsupported_model_fails_before_a_request() -> None:
    source = Capability(tools=[lambda: "hidden"])
    root = resolved(new_object_id("ap"), new_object_id("apr"), selection(), connection_types={CONNECTION: "mcp"})

    async def stream(messages, info):
        raise AssertionError("unsupported model must not receive a request")
        yield "unreachable"

    async def model_resolver(context, model_id):
        return FunctionModel(stream_function=stream, profile=ModelProfile(supported_native_tools=frozenset()))

    executable = build(
        root,
        capabilities=lambda _: [source],
        tool_sources=lambda _: {CONNECTION: source},
        plugins=PLUGINS,
        instrumentation=None,
    )
    with pytest.raises(DefinitionError, match="native tool search"):
        await executable.run("go", bindings=RunBindings.embedded(model_resolver=model_resolver))


async def test_revision_and_host_native_mcp_keep_dispatch_and_live_connection_checks(service) -> None:
    from a13n_service.infra.db import short_session
    from a13n_service.providers.tools import ToolDispatch
    from a13n_service.resources.connections.runtime import open_connections
    from a13n_service.runs.host import resolve_host
    from a13n_service.runs.tables import RunRow
    from a13n_service.tenancy.authorize import execution_authority

    from .test_agents import create_agent, create_model, revision_config
    from .test_connections import SECRET, admin, create, patch, remote, scope

    async with remote() as server:
        connection = await create(
            service,
            {
                "config": {"url": f"{server.url}/mcp", "headers": ["x-api-key"], "tools": ["echo"]},
                "auth": "headers",
                "credential": {"headers": {"x-api-key": SECRET}},
            },
        )
        connection_id = connection["id"]
        model_key = await create_model(service)
        agent = await create_agent(
            service,
            "Native search",
            model_key,
            toolsets={},
            connection_tools=[{"connection_id": connection_id, "defer_loading": True, "permission": "allow"}],
            tool_search={
                "config": {"mode": "native"},
                "groups": {
                    "knowledge": {
                        "description": "Knowledge tools",
                        "connections": [connection_id],
                    }
                },
            },
        )
        selected = AgentConfig.model_validate(await revision_config(service, agent))
        assert selected.connection_tools[0].defer_loading
        root = resolved(agent["id"], agent["default_revision_id"], selected, connection_types={connection_id: "mcp"})
        actor, workspace = admin(service), scope(service)
        run = RunRow(memory_mounts=[], mcp_headers={connection_id: {"x-trace": "native-search"}})
        async with short_session(service.runtime.storage) as session:
            plan = await resolve_host(
                session, run, actor, workspace, root, authority=execution_authority(actor, workspace)
            )
        [planned] = plan.parts[root.revision_id].connections
        assert not planned.defer_loading
        # The stored configuration still controls capability loading outside these groups.
        assert selected.connection_tools[0].defer_loading
        dispatches = []

        async def check(dispatch: ToolDispatch) -> None:
            dispatches.append(dispatch)

        runtime = service.runtime
        async with open_connections(
            (planned,),
            check,
            storage=runtime.storage,
            redis=runtime.redis,
            keys=runtime.keys,
            registry=runtime.registry,
            policy=runtime.endpoint_policy,
            settings=runtime.settings.providers,
        ) as capabilities:
            phase = "discover"
            called = False

            async def stream(messages, info):
                nonlocal called
                tools = {tool.name: tool for tool in info.function_tools}
                assert set(tools) == {"knowledge__echo"}
                assert tools["knowledge__echo"].defer_loading
                if phase == "discover":
                    yield {0: NativeToolSearchCallPart(args={"queries": ["echo"]}, tool_call_id="search")}
                    yield {
                        1: NativeToolSearchReturnPart(
                            content={"discovered_tools": [{"name": "knowledge__echo"}]}, tool_call_id="search"
                        )
                    }
                    yield "found"
                elif not called:
                    called = True
                    yield {
                        0: DeltaToolCall(name="knowledge__echo", json_args='{"text":"hello"}', tool_call_id="invoke")
                    }
                else:
                    yield "done"

            async def model_resolver(context, model_id):
                return FunctionModel(stream_function=stream, profile=anthropic_model_profile("claude-sonnet-4-6"))

            executable = build(
                root,
                capabilities=lambda _: capabilities,
                tool_sources=lambda _: {connection_id: capabilities[0]},
                plugins=PLUGINS,
                instrumentation=None,
            )
            bindings = RunBindings.embedded(model_resolver=model_resolver)
            found = await executable.run("find", bindings=bindings)
            assert found.output_or_raise() == "found"
            phase = "invoke"
            invoked = await executable.run("call", previous_state=found.state, bindings=bindings)
            assert invoked.output_or_raise() == "done"
            assert server.echoed == ["hello"]
            assert dispatches == [ToolDispatch(connection_id, None, "echo", "invoke")]
            assert server.requests[-1]["x-trace"] == "native-search"
            assert server.requests[-1]["x-api-key"] == SECRET
            await patch(service, connection, {"enabled": False})
            called = False
            refused = await executable.run("call again", previous_state=invoked.state, bindings=bindings)
            assert refused.status == "completed"
            assert server.echoed == ["hello"] and len(dispatches) == 1


@pytest.mark.parametrize("native", [False, True])
async def test_plugin_tools_compose_once_for_each_discovery_mode(native, monkeypatch) -> None:
    from .test_agents import _Plugin

    contributions = []
    effects = []

    def lookup() -> str:
        effects.append("called")
        return "found"

    def get_capabilities(self):
        contributions.append(self.plugin_id)
        return [Capability(tools=[lookup])]

    monkeypatch.setattr(_Plugin, "get_capabilities", get_capabilities)
    selected = config(
        toolsets={},
        plugins=[{"instance_name": "audit", "plugin_key": "test.audit"}],
        tool_search={
            "config": {"mode": "native" if native else "proxy"},
            "groups": {
                "knowledge": {
                    "description": "Knowledge",
                    "plugins": ["audit"],
                }
            },
        },
    )
    root = resolved(new_object_id("ap"), new_object_id("apr"), selected)
    step = 0

    async def stream(messages, info):
        nonlocal step
        step += 1
        if step == 1:
            if native:
                yield {0: NativeToolSearchCallPart(args={"queries": ["lookup"]}, tool_call_id="search")}
                yield {
                    1: NativeToolSearchReturnPart(
                        content={"discovered_tools": [{"name": "knowledge__lookup"}]}, tool_call_id="search"
                    )
                }
                yield {2: DeltaToolCall(name="knowledge__lookup", json_args="{}", tool_call_id="call")}
            else:
                assert {tool.name for tool in info.function_tools} == {"search_proxy_tools", "call_proxy_tool"}
                yield {
                    0: DeltaToolCall(
                        name="call_proxy_tool",
                        json_args='{"group":"knowledge","tool":"lookup","arguments":{}}',
                        tool_call_id="call",
                    )
                }
        else:
            yield "done"

    async def model_resolver(context, model_id):
        return FunctionModel(stream_function=stream, profile=anthropic_model_profile("claude-sonnet-4-6"))

    executable = build(root, capabilities=lambda _: [], plugins=PLUGINS, instrumentation=None)
    result = await executable.run("go", bindings=RunBindings.embedded(model_resolver=model_resolver))
    assert result.output_or_raise() == "done"
    assert contributions == ["audit"] and effects == ["called"]


@pytest.mark.parametrize("strategy", [None, "bm25", "regex"])
def test_native_config_roundtrip_and_plan_strategy(strategy) -> None:
    selected = config(tool_search={"config": {"mode": "native", "strategy": strategy}, "groups": {}})
    restored = AgentConfig.model_validate_json(selected.model_dump_json())
    assert restored.tool_search.config.strategy == strategy
    plan = definition.tool_search(restored, {})
    assert plan is not None and plan.config.strategy == strategy


@pytest.mark.parametrize(
    "value",
    [
        {"native_search": False, "config": {"mode": "native"}},
        {"config": {"mode": "native", "max_search_bytes": 4096}},
        {"config": {"mode": "proxy", "strategy": "regex"}},
    ],
)
def test_search_config_rejects_conflicting_branches(value) -> None:
    with pytest.raises(ValidationError):
        config(tool_search=value)
