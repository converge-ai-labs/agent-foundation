from __future__ import annotations

from collections.abc import Callable

import pytest
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.domain import (
    AgentConfig,
    AgentRunOverride,
    CreateAgentRequest,
)
from a13n_service.agents.errors import AgentError
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.validation import (
    AgentConfigValidationError,
    AgentProtocolPolicy,
    validate_agent_config,
)

from .conftest import WORKSPACE_ID, actor, agent_config


def _with_protocol(configure: Callable[[dict[str, object]], None]) -> AgentConfig:
    payload = agent_config().model_dump(mode="python", by_alias=True)
    protocol = dict(payload["protocol"])
    configure(protocol)
    payload["protocol"] = protocol
    return AgentConfig.model_validate(payload)


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
async def test_create_validates_v1_revision_against_protocol_policy(
    agent_management: AgentManagement,
    config: AgentConfig,
    reason: str,
    path: str,
) -> None:
    with pytest.raises(AgentError) as rejected:
        await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key=f"create-invalid-protocol-{reason}",
            request=CreateAgentRequest(name=f"Invalid {reason}", config=config),
        )

    assert rejected.value.code == "agent_revision_create_failed"
    assert rejected.value.details == {"reason": reason, "path": path}


def test_protocol_hard_ceilings_are_deployment_policy() -> None:
    config = agent_config()

    with pytest.raises(AgentConfigValidationError) as rejected:
        validate_agent_config(
            config,
            protocol_policy=AgentProtocolPolicy(max_input_bytes=1024),
        )

    assert rejected.value.reason == "protocol_limit_exceeds_deployment"
    assert rejected.value.path == "protocol.limits.max_input_bytes"


def test_protocol_client_tool_policies_are_unique() -> None:
    payload = agent_config().model_dump(mode="python", by_alias=True)
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
            AgentConfig.model_validate(payload),
            protocol_policy=AgentProtocolPolicy(),
        )

    assert rejected.value.reason == "protocol_client_tool_duplicate"
    assert rejected.value.path == "protocol.client_tools.1.name"


@pytest.mark.anyio
async def test_run_override_revalidates_replaced_output_schema(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-run-output-validation",
        request=CreateAgentRequest(name="Run Output Validation", config=agent_config()),
    )
    with pytest.raises(AgentError) as rejected:
        await agent_invocation_resolver.preparation.prepare(
            actor=actor(),
            agent_id=created.agent.id,
            agent_revision_id=created.revision.id,
            config_override=AgentRunOverride.model_validate(
                {"output_spec": {"schema": {"type": "not-a-json-schema-type"}}}
            ),
        )

    assert rejected.value.code == "agent_revision_not_executable"
    assert rejected.value.details == {"reason": "json_schema_invalid"}


@pytest.mark.anyio
async def test_run_override_cannot_remove_a_protocol_client_tool(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
) -> None:
    payload = agent_config().model_dump(mode="python", by_alias=True)
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
    config = AgentConfig.model_validate(payload)
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-client-tool-validation",
        request=CreateAgentRequest(name="Client Tool Validation", config=config),
    )
    with pytest.raises(AgentError) as rejected:
        await agent_invocation_resolver.preparation.prepare(
            actor=actor(),
            agent_id=created.agent.id,
            agent_revision_id=created.revision.id,
            config_override=AgentRunOverride.model_validate({"client_tools": []}),
        )

    assert rejected.value.code == "agent_revision_not_executable"
    assert rejected.value.details == {"reason": "protocol_client_tool_unavailable"}
