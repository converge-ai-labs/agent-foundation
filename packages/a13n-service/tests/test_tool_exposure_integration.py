"""Service compilation preserves native discovery, invocation, and connection authority."""

import pytest
from a13n_harness import RunBindings
from a13n_harness.errors import DefinitionError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.runs.agent import build
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import NativeToolSearchCallPart, NativeToolSearchReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.profiles.anthropic import anthropic_model_profile
from pydantic_ai.toolsets import FunctionToolset

from .test_agents import PLUGINS, config, resolved

pytestmark = pytest.mark.anyio
CONNECTION = new_object_id("con")


@pytest.mark.parametrize("permission", ["allow", "ask", "deny"])
async def test_native_discovery_restores_history_and_keeps_connection_permissions(permission: str) -> None:
    effects = []

    def lookup(value: int) -> int:
        effects.append(value)
        return value * 2

    source = Capability(toolsets=[FunctionToolset([lookup], id=CONNECTION)])
    selected = config(
        toolsets={},
        connection_tools=[
            {
                "connection_id": CONNECTION,
                "permission": permission,
                "exposure": "progressive",
                "description": "Knowledge lookup",
            }
        ],
        progressive_search="native",
    )
    root = resolved(new_object_id("ap"), new_object_id("apr"), selected, connection_types={CONNECTION: "connector"})
    phase = "discover"
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        tools = {tool.name: tool for tool in info.function_tools}
        assert "load_capability" not in tools
        assert "call_tool" not in tools and "search_tools" not in tools
        assert tools["lookup"].defer_loading
        assert any(
            tool.kind == "tool_search" and not tool.optional for tool in info.model_request_parameters.native_tools
        )
        if phase == "discover":
            yield {0: NativeToolSearchCallPart(args={"queries": ["lookup"]}, tool_call_id="search")}
            yield {
                1: NativeToolSearchReturnPart(content={"discovered_tools": [{"name": "lookup"}]}, tool_call_id="search")
            }
            yield "found"
        elif calls == 0:
            calls += 1
            yield {0: DeltaToolCall(name="lookup", json_args='{"value":4}', tool_call_id="invoke")}
        else:
            yield "done"

    async def model_resolver(context, model_id):
        return FunctionModel(stream_function=stream, profile=anthropic_model_profile("claude-sonnet-4-6"))

    executable = build(
        root,
        capabilities=lambda _: [source],
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
    root = resolved(
        new_object_id("ap"),
        new_object_id("apr"),
        config(progressive_search="native"),
        connection_types={CONNECTION: "mcp"},
    )

    async def stream(messages, info):
        raise AssertionError("unsupported model must not receive a request")
        yield "unreachable"

    async def model_resolver(context, model_id):
        return FunctionModel(stream_function=stream, profile=ModelProfile(supported_native_tools=frozenset()))

    executable = build(
        root,
        capabilities=lambda _: [source],
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
            connection_tools=[
                {
                    "connection_id": connection_id,
                    "exposure": "progressive",
                    "description": "Knowledge tools",
                    "permission": "allow",
                }
            ],
            progressive_search="native",
        )
        selected = AgentConfig.model_validate(await revision_config(service, agent))
        assert selected.connection_tools[0].exposure == "progressive"
        root = resolved(agent["id"], agent["default_revision_id"], selected, connection_types={connection_id: "mcp"})
        actor, workspace = admin(service), scope(service)
        run = RunRow(memory_mounts=[], mcp_headers={connection_id: {"x-trace": "native-search"}})
        async with short_session(service.runtime.storage) as session:
            plan = await resolve_host(
                session, run, actor, workspace, root, authority=execution_authority(actor, workspace)
            )
        [planned] = plan.parts[root.revision_id].connections
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
                assert set(tools) == {"echo"}
                assert tools["echo"].defer_loading
                if phase == "discover":
                    yield {0: NativeToolSearchCallPart(args={"queries": ["echo"]}, tool_call_id="search")}
                    yield {
                        1: NativeToolSearchReturnPart(
                            content={"discovered_tools": [{"name": "echo"}]}, tool_call_id="search"
                        )
                    }
                    yield "found"
                elif not called:
                    called = True
                    yield {0: DeltaToolCall(name="echo", json_args='{"text":"hello"}', tool_call_id="invoke")}
                else:
                    yield "done"

            async def model_resolver(context, model_id):
                return FunctionModel(stream_function=stream, profile=anthropic_model_profile("claude-sonnet-4-6"))

            executable = build(
                root,
                capabilities=lambda _: capabilities,
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
    from .test_agents import _Factory, _Plugin

    contributions = []
    effects = []

    def lookup() -> str:
        effects.append("called")
        return "found"

    def get_capabilities(self):
        contributions.append(self.plugin_id)
        return [Capability(toolsets=[FunctionToolset([lookup], id="audit-tools")])]

    monkeypatch.setattr(_Plugin, "get_capabilities", get_capabilities)
    monkeypatch.setattr(_Factory, "describe_toolsets", lambda self, context: {"audit-tools": "Look up audit facts."})
    selected = config(
        toolsets={},
        plugins=[{"instance_name": "audit", "plugin_key": "test.audit", "exposure": "progressive"}],
        progressive_search="native" if native else "harness",
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
                        content={"discovered_tools": [{"name": "lookup"}]}, tool_call_id="search"
                    )
                }
                yield {2: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="call")}
            else:
                assert {tool.name for tool in info.function_tools} == {
                    "list_toolsets",
                    "list_tools",
                    "search_tools",
                    "describe_tools",
                    "execute_tool",
                }
                yield {
                    0: DeltaToolCall(
                        name="execute_tool",
                        json_args='{"name":"lookup","arguments":{}}',
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
