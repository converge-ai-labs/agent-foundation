"""Service compiles presentation independently of selection, permissions, and pending history."""

import pytest
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_harness.tools.exposure import INVOCATION_TARGET_KEY
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents.definition import tool_exposure, tool_permissions
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride, apply_override
from a13n_service.runs.deferred import dump, load, pending
from pydantic import ValidationError
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests

PLUGINS = HarnessPluginFactoryCatalog([])


def test_exposure_compiles_builtin_and_connection_native_ids() -> None:
    connection = new_object_id("con")
    config = AgentConfig.model_validate(
        {
            "model": "test",
            "toolsets": {
                "shell": {"exposure": "progressive", "tool_exposure": {"shell_exec": "direct"}},
                "memory": {"exposure": "progressive"},
                "agents": {"enabled": True, "exposure": "progressive"},
            },
            "connection_tools": [
                {
                    "connection_id": connection,
                    "exposure": "progressive",
                    "description": "Query orders.",
                    "tool_exposure": {"lookup": "direct"},
                    "permission": "ask",
                }
            ],
        }
    )
    assert AgentConfig.model_validate_json(config.model_dump_json()) == config
    policy = tool_exposure(config, PLUGINS)
    assert policy.progressive_search == "harness"
    assert policy.toolsets["a13n-shell-tools"].resolve("shell_exec") == "direct"
    assert policy.toolsets["a13n-shell-tools"].resolve("shell_wait") == "progressive"
    assert policy.toolsets["a13n-memory-file-tools"].exposure == "progressive"
    assert policy.toolsets["a13n-memory-record-tools"].exposure == "progressive"
    assert policy.toolsets["platform"].tool_exposure
    assert all(value == "progressive" for value in policy.toolsets["platform"].tool_exposure.values())
    assert policy.toolsets[connection].resolve("lookup") == "direct"
    assert policy.toolsets[connection].resolve("new_tool") == "progressive"
    direct = config.model_copy(
        update={
            "connection_tools": tuple(
                item.model_copy(update={"exposure": "direct", "tool_exposure": {}}) for item in config.connection_tools
            )
        }
    )
    assert tool_permissions(config, {connection: "mcp"}) == tool_permissions(direct, {connection: "mcp"})


def test_client_toolsets_keep_native_identity_and_exposure() -> None:
    config = AgentConfig.model_validate(
        {
            "model": "test",
            "client_toolsets": [
                {
                    "toolset_id": "editor",
                    "description": "Read and edit the current selection.",
                    "exposure": "progressive",
                    "tools": [
                        {
                            "name": "get_selection",
                            "description": "Read selection.",
                            "parameters_json_schema": {"type": "object"},
                        }
                    ],
                }
            ],
        }
    )
    assert tool_exposure(config, PLUGINS).toolsets["editor"].exposure == "progressive"
    assert config.client_toolsets[0].tools[0].name == "get_selection"


def test_override_replaces_source_policy_without_changing_other_sources() -> None:
    config = AgentConfig.model_validate({"model": "test", "toolsets": {"shell": {"exposure": "progressive"}}})
    changed = apply_override(config, AgentOverride.model_validate({"toolsets": {"shell": {"exposure": "direct"}}}))
    assert changed.toolsets["shell"].exposure == "direct"
    assert changed.toolsets["files"] == config.toolsets["files"]
    assert apply_override(config, AgentOverride()) == config


@pytest.mark.parametrize(
    "obsolete",
    [
        {"tool_search": {}},
        {"client_tools": []},
        {"connection_tools": [{"connection_id": new_object_id("con"), "defer_loading": True}]},
    ],
)
def test_obsolete_configuration_is_rejected(obsolete) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        AgentConfig.model_validate({"model": "test", **obsolete})


@pytest.mark.parametrize("category", ["calls", "approvals"])
def test_pending_projection_exposes_target_and_preserves_outer_checkpoint(category: str) -> None:
    call = ToolCallPart("execute_tool", {"name": "get_selection", "arguments": {}}, tool_call_id="outer")
    requests = DeferredToolRequests(
        **{category: [call]},
        metadata={
            "outer": {
                INVOCATION_TARGET_KEY: {
                    "name": "get_selection",
                    "arguments": {},
                    "kind": "external" if category == "calls" else "unapproved",
                },
            }
        },
    )
    restored = load(dump(requests))
    projected = pending(restored)
    selected = projected.calls if category == "calls" else projected.approvals
    assert selected[0].tool_name == "get_selection"
    assert selected[0].tool_call_id == "outer"
    assert selected[0].arguments == {}
    original = restored.calls if category == "calls" else restored.approvals
    assert original[0].tool_name == "execute_tool"


@pytest.mark.parametrize("strategy", [None, "bm25", "regex"])
def test_native_strategy_roundtrip_and_explicit_reset(strategy) -> None:
    config = AgentConfig(model="test", progressive_search="native", progressive_search_strategy=strategy)
    restored = AgentConfig.model_validate_json(config.model_dump_json())
    assert tool_exposure(restored, PLUGINS).strategy == strategy
    assert apply_override(config, AgentOverride()).progressive_search_strategy == strategy
    reset = apply_override(config, AgentOverride(progressive_search="harness", progressive_search_strategy=None))
    assert reset.progressive_search == "harness" and reset.progressive_search_strategy is None


def test_child_compiles_its_own_source_policy() -> None:
    from a13n_service.runs.agent import ResolvedSubagent, build

    from .test_agents import HELPER, HELPER_REVISION, config, resolved

    selected = config(
        toolsets={"files": {"exposure": "progressive"}},
        subagents={"helper": {"agent_id": HELPER, "revision_id": HELPER_REVISION}},
    )
    child = resolved(HELPER, HELPER_REVISION, config(toolsets={"shell": {"exposure": "progressive"}}))
    root = resolved(
        new_object_id("ap"),
        new_object_id("apr"),
        selected,
        subagents={"helper": ResolvedSubagent(selected.subagents["helper"], HELPER_REVISION, "Help", child)},
    )
    executable = build(root, capabilities=lambda _: [], plugins=PLUGINS, instrumentation=None)
    parent_policy = executable.definition.tool_exposure
    child_policy = executable.definition.subagents[0].agent.tool_exposure
    assert parent_policy.toolsets["a13n-file-tools"].exposure == "progressive"
    assert child_policy.toolsets["a13n-file-tools"].exposure == "direct"
    assert child_policy.toolsets["a13n-shell-tools"].exposure == "progressive"


@pytest.mark.parametrize("source_id", ["a13n-file-tools", "duplicate"])
def test_native_identity_collisions_are_rejected(source_id) -> None:
    from a13n_service.infra.errors import ServiceError

    connection = new_object_id("con")
    native_id = connection if source_id == "duplicate" else source_id
    config = AgentConfig.model_validate(
        {
            "model": "test",
            "client_toolsets": [
                {
                    "toolset_id": native_id,
                    "tools": [
                        {"name": "lookup", "description": "Look up.", "parameters_json_schema": {"type": "object"}}
                    ],
                }
            ],
            "connection_tools": [{"connection_id": connection}],
        }
    )
    with pytest.raises(ServiceError, match="unique across sources"):
        tool_exposure(config, PLUGINS)


def test_platform_aggregate_purpose_is_bounded() -> None:
    from a13n_service.resources.agents.toolsets import default_toolsets

    selections = {
        name: {"enabled": True, "description": "x" * 512, "exposure": "progressive"} for name in default_toolsets()
    }
    config = AgentConfig.model_validate({"model": "test", "toolsets": selections})
    assert len(tool_exposure(config, PLUGINS).toolsets["platform"].description) <= 512
