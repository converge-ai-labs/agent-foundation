import pytest
from a13n_service.agents.domain import AgentRunOverride, OutputSpec
from a13n_service.agents.invocation import merge_agent_run_override
from a13n_service.environments.selection import Omitted
from a13n_service.gateway.requests import ContinueRunRequest, ForkRunRequest, StartRunRequest

from tests.agents.conftest import agent_config


@pytest.mark.parametrize("request_type", [StartRunRequest, ContinueRunRequest, ForkRunRequest])
@pytest.mark.parametrize(
    "override",
    [
        {},
        {"instructions": "Only this instruction changes."},
        {"model": {"settings": {"temperature": 0.2}}},
        {"retries": {"tools": 0}},
        {"output_spec": None, "skills": []},
        {"subagents": {"helper": {"description": "New description"}}},
    ],
)
def test_native_conversion_preserves_override_inheritance(request_type, override) -> None:
    payload = {
        "input": {"schema_version": "2", "content": [{"type": "text", "text": "hello"}]},
        "config_override": override,
    }
    if request_type is StartRunRequest:
        payload["agent_id"] = "ap_1234567890abcdef"
    if request_type is ContinueRunRequest:
        payload["expected_thread_version"] = 1
    request = request_type.model_validate(payload)

    command = request.to_command()

    assert command.environment is Omitted.UNSET
    assert command.config_override is not None
    assert command.config_override.model_dump(mode="json", exclude_unset=True) == override
    base = agent_config(subagents={"helper": {"agent_id": "ap_1234567890abcdef", "description": "Original"}})
    base = base.model_copy(update={"output_spec": OutputSpec.model_validate({"schema": {"type": "object"}})})
    expected = merge_agent_run_override(base, AgentRunOverride.model_validate(override))
    assert merge_agent_run_override(base, command.config_override) == expected
