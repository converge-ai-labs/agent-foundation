"""Service selects presentation; Harness owns discovery and local invocation."""

import json
from typing import Any

import pytest
from a13n_harness import DeferredToolResume, RunBindings
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents import definition, toolsets
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride, apply_override
from a13n_service.runs.agent import build
from pydantic import ValidationError
from pydantic_ai.capabilities import Capability
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.profiles.anthropic import anthropic_model_profile
from pydantic_ai.toolsets import FunctionToolset

from .test_agents import HELPER, HELPER_REVISION, PLUGINS, config, resolved

pytestmark = pytest.mark.anyio
CONNECTION = new_object_id("con")


def selection(**changes: Any) -> AgentConfig:
    return config(
        connection_tools=[{"connection_id": CONNECTION}],
        tool_search={
            "config": {"mode": "proxy"},
            "groups": {"knowledge": {"description": "Knowledge lookup", "connections": [CONNECTION]}},
        },
        **changes,
    )


def test_configuration_preserves_defaults_and_validates_membership() -> None:
    assert "tool_search" not in config().model_dump(mode="json")
    selected = selection()
    assert AgentConfig.model_validate_json(selected.model_dump_json()) == selected
    assert selected.tool_search is not None
    assert selected.tool_search.connection_ids == {CONNECTION}
    for groups, message in (
        ({"bad__name": {"description": "Tools"}}, "ToolSearch group"),
        ({"tools": {"description": " "}}, "non-blank"),
        ({"tools": {"description": "Tools", "connections": [new_object_id("con")]}}, "unselected connection"),
        ({"tools": {"description": "Tools", "plugins": ["missing"]}}, "unselected plugin"),
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


def test_discovery_rejects_removed_environment_selector() -> None:
    with pytest.raises(ValidationError):
        config(tool_search={"groups": {"tools": {"description": "Tools", "capabilities": ["environment"]}}})


def test_automatic_groups_follow_selections_and_preserve_exclusions() -> None:
    added = new_object_id("con")
    source = Capability(tools=[lambda: "source"], id="environment")
    selected = config(
        connection_tools=[{"connection_id": CONNECTION}, {"connection_id": added}],
        tool_search={"automatic": {"exclude_connections": [CONNECTION], "capabilities": ["computer", "browser"]}},
        plugins=[{"instance_name": "audit", "plugin_key": "test.audit"}],
        toolsets={"agents": {"enabled": True}, "computer": {"enabled": True}},
    )
    selected = AgentConfig.model_validate_json(selected.model_dump_json())
    sources = {
        CONNECTION: source,
        added: source,
        "environment": source,
        "web": source,
        "browser": Capability(id="browser"),
        "platform": source,
    }
    connections = {
        CONNECTION: toolsets.ConnectionInfo(name="Knowledge", type="mcp"),
        added: toolsets.ConnectionInfo(name="Docs", type="mcp"),
    }
    plan = definition.tool_search(selected, sources, connections)
    group = toolsets.connection_discovery(added, "Docs").group_name
    assert plan is not None
    assert set(plan.groups) == {group, "computer", "browser", "platform"}
    assert plan.groups[group].capabilities == (source,)
    assert plan.groups[group].description == "Tools from Docs."
    for selections in ([{"connection_id": added}, {"connection_id": CONNECTION}], [{"connection_id": added}]):
        reordered = apply_override(selected, AgentOverride.model_validate({"connection_tools": selections}))
        next_plan = definition.tool_search(reordered, sources, connections)
        assert next_plan is not None and next_plan.groups[group] == plan.groups[group]
    assert toolsets.connection_discovery(added, "Renamed").group_name == group
    assert all(not group.plugins for group in plan.groups.values())
    changed = apply_override(selected, AgentOverride.model_validate({"connection_tools": []}))
    after = definition.tool_search(changed, {"environment": source}, {})
    assert after is not None and set(after.groups) == {"computer", "browser", "platform"}
    assert after.groups["computer"].tools["environment"] == frozenset(
        tool.model_name
        for group in toolsets.catalog(frozenset()).items
        if group.key == "computer"
        for tool in group.tools
        if tool.execution_id != toolsets.COMPUTER_NOTIFY_TOOL_ID
    )
    assert not after.groups["browser"].capabilities
    assert not after.groups["platform"].capabilities
    with pytest.raises(ValidationError, match="cannot also define custom groups"):
        config(tool_search={"automatic": {}, "groups": {"custom": {"description": "Custom"}}})


def test_plan_uses_selected_instances_and_child_has_its_own_configuration() -> None:
    from a13n_service.runs.agent import ResolvedSubagent

    connection = Capability(tools=[lambda: "connection"])
    platform = Capability(tools=[lambda: "platform"])
    environment = Capability(tools=[lambda: "environment"], id="environment")
    selected = config(
        connection_tools=[{"connection_id": CONNECTION}],
        plugins=[{"instance_name": "audit", "plugin_key": "test.audit"}],
        toolsets={"agents": {"enabled": True}},
        tool_search={
            "config": {"mode": "proxy"},
            "groups": {
                "tools": {
                    "description": "Selected tools",
                    "connections": [CONNECTION],
                    "plugins": ["audit"],
                    "platform": True,
                    "capabilities": ["files", "shell", "computer", "web"],
                }
            },
        },
        subagents={"mode": "inline", "named": {"helper": {"agent_id": HELPER, "revision_id": HELPER_REVISION}}},
    )
    sources = {CONNECTION: connection, "platform": platform, "environment": environment}
    plan = definition.tool_search(
        selected, sources, {CONNECTION: toolsets.ConnectionInfo(name="Knowledge", type="mcp")}
    )
    assert plan is not None and plan.config.mode == "proxy"
    # An unavailable optional capability is not activated by group membership.
    assert plan.groups["tools"].capabilities == (connection, environment, platform)
    assert plan.groups["tools"].plugins == ("audit",)
    child = resolved(HELPER, HELPER_REVISION, config())
    root = resolved(
        new_object_id("ap"),
        new_object_id("apr"),
        selected,
        connections={CONNECTION: toolsets.ConnectionInfo(name="Knowledge", type="mcp")},
        subagents={"helper": ResolvedSubagent(selected.subagents.named["helper"], HELPER_REVISION, "Help", child)},
    )
    executable = build(
        root,
        capabilities=lambda node: list(sources.values()) if node is root else [],
        tool_sources=lambda node: sources if node is root else {},
        plugins=PLUGINS,
        instrumentation=None,
    )
    built = executable.definition.tool_search
    assert built is not None
    assert built.groups["tools"].capabilities[0] is connection
    assert built.groups["tools"].capabilities[-1] is platform
    from a13n_harness.environment import DynamicEnvironmentCapability

    assert isinstance(built.groups["tools"].capabilities[1], DynamicEnvironmentCapability)
    assert built.groups["tools"].plugins == ("audit",)
    assert executable.definition.subagents[0].agent.tool_search is None


@pytest.mark.parametrize("permission", ["allow", "ask", "deny"])
@pytest.mark.parametrize("automatic", [False, True])
async def test_proxy_discovery_restores_history_and_keeps_connection_permissions(
    permission: str, automatic: bool
) -> None:
    effects = []

    def lookup(value: int) -> int:
        effects.append(value)
        return value * 2

    source = Capability(toolsets=[FunctionToolset([lookup], id=CONNECTION)])
    selected = config(
        toolsets={},
        connection_tools=[{"connection_id": CONNECTION, "permission": permission}],
        tool_search={"automatic": {}} if automatic else selection().tool_search,
    )
    root = resolved(
        new_object_id("ap"),
        new_object_id("apr"),
        selected,
        connections={CONNECTION: toolsets.ConnectionInfo(name="Knowledge", type="connector")},
    )
    phase = "discover"
    calls = 0
    group = toolsets.connection_discovery(CONNECTION, "Knowledge").group_name if automatic else "knowledge"
    assert group is not None
    canonical_name = f"{group.replace('_', '_u')}__lookup"

    async def stream(messages, info):
        nonlocal calls
        tools = {tool.name: tool for tool in info.function_tools}
        assert "load_capability" not in tools
        assert "call_tool" in tools and canonical_name not in tools
        if calls == 0:
            calls += 1
            if phase == "discover":
                yield {
                    0: DeltaToolCall(
                        name="describe_tool",
                        json_args=json.dumps({"group": group, "tool": "lookup"}),
                        tool_call_id="search",
                    )
                }
            else:
                yield {
                    0: DeltaToolCall(
                        name="call_tool",
                        json_args=json.dumps({"group": group, "tool": "lookup", "arguments": {"value": 4}}),
                        tool_call_id="invoke",
                    )
                }
        else:
            yield "found" if phase == "discover" else "done"

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
    calls = 0
    result = await executable.run("call", previous_state=state, bindings=bindings)
    if permission == "ask":
        assert result.status == "suspended"
        assert effects == []
        pending = result.deferred
        assert pending is not None and not pending.calls
        [request] = pending.approvals
        assert request.model_call.tool_name == "call_tool"
        assert request.model_call.tool_call_id == "invoke"
        assert request.target_call.tool_name == canonical_name
        assert request.target_call.args_as_dict() == {"value": 4}
        state = type(result.state).model_validate_json(result.state.model_dump_json())
        result = await executable.run(
            previous_state=state,
            bindings=bindings,
            deferred_resume=DeferredToolResume(
                pending,
                pending.build_results(approvals={request.target_call.tool_call_id: True}),
            ),
        )
    assert result.status == "completed"
    assert result.output_or_raise() == "done"
    assert effects == ([] if permission == "deny" else [4])


@pytest.mark.parametrize("native", [False, True])
async def test_revision_and_host_mcp_keep_dispatch_and_live_connection_checks(service, native: bool) -> None:
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
        catalog_response = await service.client.get(f"{service.api}/toolsets", params={"connection_ids": connection_id})
        assert catalog_response.status_code == 200, catalog_response.text
        descriptor = next(item for item in catalog_response.json()["discovery_sources"] if item["id"] == connection_id)
        assert descriptor == toolsets.connection_discovery(connection_id, connection["name"]).model_dump()
        missing = await service.client.get(f"{service.api}/toolsets", params={"connection_ids": new_object_id("conn")})
        assert missing.status_code == 404
        model_key = await create_model(service)
        agent = await create_agent(
            service,
            "Proxy search",
            model_key,
            toolsets={},
            connection_tools=[{"connection_id": connection_id, "permission": "allow"}],
            tool_search={"mode": "native"}
            if native
            else {
                "config": {"mode": "proxy"},
                "groups": {
                    "knowledge": {
                        "description": "Knowledge tools",
                        "connections": [connection_id],
                    }
                },
            },
        )
        selected = AgentConfig.model_validate(await revision_config(service, agent))
        root = resolved(
            agent["id"],
            agent["default_revision_id"],
            selected,
            connections={connection_id: toolsets.ConnectionInfo(name="Knowledge", type="mcp")},
        )
        actor, workspace = admin(service), scope(service)
        run = RunRow(
            options={},
            memory_mounts=[],
            environment_mounts=[],
            mcp_headers={connection_id: {"x-trace": "proxy-search"}},
        )
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
                if native:
                    assert {"search_tools", "list_images"} <= set(tools) and "call_tool" not in tools
                    if not called:
                        called = True
                        if phase == "discover":
                            assert "echo" not in tools
                            yield {
                                0: DeltaToolCall(
                                    name="search_tools", json_args='{"queries":["echo"]}', tool_call_id="search"
                                )
                            }
                        else:
                            assert "echo" in tools
                            name = "echo"
                            yield {0: DeltaToolCall(name=name, json_args='{"text":"hello"}', tool_call_id="invoke")}
                    else:
                        yield "found" if phase == "discover" else "done"
                    return
                assert set(tools) == {
                    "discover_tools",
                    "describe_tool",
                    "call_tool",
                    "list_tools",
                    "list_tool_groups",
                    "list_images",
                }
                if not called:
                    called = True
                    if phase == "discover":
                        yield {
                            0: DeltaToolCall(name="list_tools", json_args='{"detail":"full"}', tool_call_id="search")
                        }
                    else:
                        yield {
                            0: DeltaToolCall(
                                name="call_tool",
                                json_args='{"group":"knowledge","tool":"echo","arguments":{"text":"hello"}}',
                                tool_call_id="invoke",
                            )
                        }
                else:
                    yield "found" if phase == "discover" else "done"

            async def model_resolver(context, model_id):
                from pydantic_ai.profiles import ModelProfile

                return FunctionModel(stream_function=stream, profile=ModelProfile(supported_native_tools=frozenset()))

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
            called = False
            invoked = await executable.run("call", previous_state=found.state, bindings=bindings)
            assert invoked.output_or_raise() == "done"
            assert server.echoed == ["hello"]
            assert len(dispatches) == 1
            assert dispatches[0].connection_id == connection_id
            assert dispatches[0].tool_name == "echo"
            assert server.requests[-1]["x-trace"] == "proxy-search"
            assert server.requests[-1]["x-api-key"] == SECRET
            await patch(service, connection, {"enabled": False})
            called = False
            refused = await executable.run("call again", previous_state=invoked.state, bindings=bindings)
            assert refused.status == "completed"
            assert server.echoed == ["hello"] and len(dispatches) == 1


async def test_plugin_tools_compose_once_for_proxy_search(monkeypatch) -> None:
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
            "config": {"mode": "proxy"},
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
            assert {tool.name for tool in info.function_tools} == {
                "discover_tools",
                "describe_tool",
                "call_tool",
                "list_tools",
                "list_tool_groups",
                "list_images",
            }
            yield {
                0: DeltaToolCall(
                    name="call_tool",
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


@pytest.mark.parametrize(
    "value",
    [
        {"native_search": False, "config": {"mode": "proxy"}},
        {"config": {"mode": "native"}},
        {"config": {"mode": "proxy", "strategy": "regex"}},
    ],
)
def test_search_config_rejects_conflicting_branches(value) -> None:
    with pytest.raises(ValidationError):
        config(tool_search=value)


def test_split_environment_sources_and_opt_in_capabilities() -> None:
    source = Capability(id="environment")
    selected = config(
        tool_search={
            "automatic": {
                "capabilities": ["files", "shell", "web", "memory", "assets"],
                "connections": False,
                "exclude_platform": True,
            }
        }
    )
    sources = {"environment": source, **{key: Capability(id=key) for key in ("web", "memory", "assets")}}
    plan = definition.tool_search(
        selected, sources, {CONNECTION: toolsets.ConnectionInfo(name="Knowledge", type="mcp")}
    )
    assert plan is not None and set(plan.groups) == {"files", "shell", "web", "memory", "assets"}
    assert (
        plan.groups["files"].tools["environment"]
        == toolsets.discovery_members(selected.toolsets, ("files",))["environment"]
    )
    assert (
        plan.groups["shell"].tools["environment"]
        == toolsets.discovery_members(selected.toolsets, ("shell",))["environment"]
    )
    assert plan.groups["memory"].capabilities == (sources["memory"],)
    plan._compose(tuple(sources.values()), {})
    with pytest.raises(ValidationError, match="multiple groups"):
        config(
            tool_search={
                "groups": {
                    "one": {"description": "One", "capabilities": ["files"]},
                    "new": {"description": "New", "capabilities": ["files"]},
                }
            }
        )
    dormant = config(tool_search={"groups": {"platform": {"description": "Platform", "platform": True}}})
    dormant_plan = definition.tool_search(dormant, {}, {})
    assert dormant_plan is not None and not dormant_plan.groups["platform"].capabilities


@pytest.mark.parametrize("key", ["files", "shell", "computer"])
async def test_catalog_discovery_names_match_the_dynamic_environment_surface(key) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    from a13n_harness import AgentContext
    from a13n_harness.environment import DynamicEnvironmentCapability, EnvironmentAction, EnvironmentPermissionSet
    from a13n_harness.environment.advanced import BoundEnvironment
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    # Grant all operations to inspect the real tool surface without invoking a provider.
    environment = Mock(spec=BoundEnvironment)
    environment.snapshot = SimpleNamespace(
        mounts=(SimpleNamespace(permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction))),),
        default_mount="workspace",
    )
    deps = Mock(spec=AgentContext, environment=environment, run_id="catalog-contract")
    deps._run_capability.return_value = None
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())
    selected = config(toolsets={item: {"enabled": item == key} for item in ("files", "shell", "computer")})
    capability = DynamicEnvironmentCapability(toolsets.environment_configuration(selected.toolsets))
    bound = await capability.for_run(ctx)
    native = bound.get_toolset()
    assert native is not None
    native = await native.for_run(ctx)
    async with native:
        step = await native.for_run_step(ctx)
        prepared = await step.get_tools(ctx)
    declared = toolsets.discovery_members(selected.toolsets, (key,))["environment"]
    assert declared and declared == prepared.keys()


async def test_computer_group_includes_service_handback_notifications() -> None:
    from unittest.mock import Mock

    from a13n_service.runs.environments.desktop.notify import HandbackNotifications

    selected = config(toolsets={"computer": {"enabled": True}}, tool_search={"automatic": {}})
    notifications = HandbackNotifications(Mock(), Mock())
    plan = definition.tool_search(selected, {"computer-notify": notifications}, {})
    assert plan is not None
    assert plan.groups["computer"].capabilities == (notifications,)
    assert toolsets.discovery_members(selected.toolsets, ("computer",))["computer-notify"] == {"computer_notify"}
    disabled = config(toolsets={"computer": {"enabled": True, "tools": {"notify": {"enabled": False}}}})
    assert "computer-notify" not in toolsets.discovery_members(disabled.toolsets, ("computer",))
