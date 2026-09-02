from __future__ import annotations

from collections.abc import Callable

import pytest
from a13n_service.agent_presets.domain import (
    AgentPresetCommandRequest,
    AgentPresetConfig,
    AgentRunOverride,
    CreateAgentPresetRequest,
)
from a13n_service.agent_presets.errors import AgentPresetError
from a13n_service.agent_presets.invocation_resolution import AgentPresetInvocationResolver
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.agent_presets.validation import (
    AgentConfigValidationError,
    AgentProtocolPolicy,
    validate_agent_config,
)

from .conftest import WORKSPACE_ID, actor, preset_config


def _with_protocol(configure: Callable[[dict[str, object]], None]) -> AgentPresetConfig:
    payload = preset_config().model_dump(mode="python", by_alias=True)
    protocol = dict(payload["protocol"])
    configure(protocol)
    payload["protocol"] = protocol
    return AgentPresetConfig.model_validate(payload)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("config", "reason", "path"),
    [
        (
            _with_protocol(lambda protocol: protocol.update(output_modes=[])),
            "protocol_output_modes_empty",
            "protocol.output_modes",
        ),
        (
            _with_protocol(lambda protocol: protocol.update(output_modes=["unregistered"])),
            "protocol_output_mode_unsupported",
            "protocol.output_modes.0",
        ),
        (
            _with_protocol(lambda protocol: protocol.update(event_visibility=["private.worker.event"])),
            "protocol_event_unsupported",
            "protocol.event_visibility.0",
        ),
        (
            _with_protocol(lambda protocol: protocol.update(client_tools=[{"name": "missing"}])),
            "protocol_client_tool_unavailable",
            "protocol.client_tools.0.name",
        ),
        (
            _with_protocol(
                lambda protocol: protocol.update(
                    a2a_skills=[
                        {"id": "support", "name": "Support"},
                        {"id": "support", "name": "Support Again"},
                    ]
                )
            ),
            "protocol_a2a_skill_duplicate",
            "protocol.a2a_skills.1.id",
        ),
        (
            _with_protocol(
                lambda protocol: protocol.update(input_data_schema={"$ref": "https://example.test/schema.json"})
            ),
            "json_schema_external_reference",
            "protocol.input_data_schema",
        ),
    ],
)
async def test_create_accepts_authoring_shape_but_publish_applies_protocol_policy(
    agent_preset_service: AgentPresetService,
    config: AgentPresetConfig,
    reason: str,
    path: str,
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key=f"create-invalid-protocol-{reason}",
        request=CreateAgentPresetRequest(name=f"Invalid {reason}", config=config),
    )

    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_service.publish(
            actor=actor(),
            preset_id=preset.id,
            idempotency_key=f"publish-invalid-protocol-{reason}",
            request=AgentPresetCommandRequest(expected_resource_version=1),
        )

    assert rejected.value.code == "preset_publish_failed"
    assert rejected.value.details == {"reason": reason, "path": path}


def test_protocol_hard_ceilings_are_deployment_policy() -> None:
    config = preset_config()

    with pytest.raises(AgentConfigValidationError) as rejected:
        validate_agent_config(
            config,
            protocol_policy=AgentProtocolPolicy(max_input_bytes=1024),
        )

    assert rejected.value.reason == "protocol_limit_exceeds_deployment"
    assert rejected.value.path == "protocol.limits.max_input_bytes"


def test_protocol_client_tool_policies_are_unique() -> None:
    payload = preset_config().model_dump(mode="python", by_alias=True)
    payload["client_tools"] = [
        {
            "name": "lookup_order",
            "description": "Look up one order.",
            "parameters_json_schema": {"type": "object"},
        }
    ]
    protocol = dict(payload["protocol"])
    protocol["client_tools"] = [{"name": "lookup_order"}, {"name": "lookup_order"}]
    payload["protocol"] = protocol

    with pytest.raises(AgentConfigValidationError) as rejected:
        validate_agent_config(
            AgentPresetConfig.model_validate(payload),
            protocol_policy=AgentProtocolPolicy(),
        )

    assert rejected.value.reason == "protocol_client_tool_duplicate"
    assert rejected.value.path == "protocol.client_tools.1.name"


@pytest.mark.anyio
async def test_run_override_revalidates_replaced_output_schema(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-run-output-validation",
        request=CreateAgentPresetRequest(name="Run Output Validation", config=preset_config()),
    )
    await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-run-output-validation",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )

    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_invocation_resolver.prepare(
            actor=actor(),
            agent_preset_id=preset.id,
            config_override=AgentRunOverride.model_validate(
                {"output_spec": {"schema": {"type": "not-a-json-schema-type"}}}
            ),
        )

    assert rejected.value.code == "preset_revision_not_executable"
    assert rejected.value.details == {"reason": "json_schema_invalid"}


@pytest.mark.anyio
async def test_run_override_cannot_remove_a_protocol_client_tool(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
) -> None:
    payload = preset_config().model_dump(mode="python", by_alias=True)
    payload["client_tools"] = [
        {
            "name": "lookup_order",
            "description": "Look up one order.",
            "parameters_json_schema": {"type": "object"},
        }
    ]
    protocol = dict(payload["protocol"])
    protocol["client_tools"] = [{"name": "lookup_order", "required": True}]
    payload["protocol"] = protocol
    config = AgentPresetConfig.model_validate(payload)
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-client-tool-validation",
        request=CreateAgentPresetRequest(name="Client Tool Validation", config=config),
    )
    await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-client-tool-validation",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )

    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_invocation_resolver.prepare(
            actor=actor(),
            agent_preset_id=preset.id,
            config_override=AgentRunOverride.model_validate({"client_tools": []}),
        )

    assert rejected.value.code == "preset_revision_not_executable"
    assert rejected.value.details == {"reason": "protocol_client_tool_unavailable"}
