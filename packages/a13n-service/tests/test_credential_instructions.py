"""Model-visible credential guidance survives real Harness history compaction."""

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
from a13n_harness.environment.models import EnvironmentPermissionSet
from a13n_service.resources.environment_instances.session_configuration import SessionConfiguration
from a13n_service.runs.environments.egress.instructions import CredentialInstructionsCapability, render
from a13n_service.runs.schemas import EnvironmentMount
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from .test_session_credentials import selection

pytestmark = pytest.mark.anyio


def mount(env="GH_TOKEN"):
    return EnvironmentMount(
        name="workspace",
        environment_id="env_test",
        session_configuration=SessionConfiguration.model_validate(selection(env=env)),
    )


def test_guidance_only_describes_selected_command_credentials():
    selected = mount()
    text = render([selected])
    assert text is not None
    assert "${GH_TOKEN}" in text and "api.github.com" in text and "workspace" in text
    assert "ecred_" not in text
    assert "Availability is checked when the environment is prepared" in text
    assert render([selected.model_copy(update={"permission_ceiling": EnvironmentPermissionSet(operations=())})]) is None
    assert render([selected.model_copy(update={"session_configuration": SessionConfiguration()})]) is None
    assert render([]) is None


async def test_each_request_has_current_guidance_after_compaction_and_selection_changes():
    calls = []

    async def stream(messages, info):
        calls.append((messages, info.instructions or ""))
        yield "The task is still pending."

    def build(env):
        text = render([mount(env)]) if env is not None else None
        return HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=stream),
            capabilities=(CredentialInstructionsCapability(text),) if text is not None else (),
        )

    agent = build("GH_TOKEN")
    first = await agent.run("Inspect the repository")
    assert first.status == "completed", first.failure
    second = await agent.run("Continue", previous_state=first.state)
    assert second.status == "completed", second.failure
    compacted = await agent.compact(previous_state=second.state)
    assert compacted.status == "completed", compacted.failure
    saved = HarnessState.model_validate_json(compacted.state.model_dump_json())
    resumed = await agent.run("Continue after compaction", previous_state=saved)
    assert resumed.status == "completed", resumed.failure
    for index in (0, 1, 3):
        messages, instructions = calls[index]
        assert instructions.count("Configured HTTPS credentials for this Run:") == 1
        assert "${GH_TOKEN}" in instructions
        # The guidance is not appended as ordinary user content on each request.
        assert all(
            "Configured HTTPS credentials" not in str(part.content)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )

    changed = await build("GITHUB_API_TOKEN").run("Use the new selection", previous_state=resumed.state)
    assert changed.status == "completed", changed.failure
    assert "${GITHUB_API_TOKEN}" in calls[-1][1]
    assert "GH_TOKEN" not in calls[-1][1]
    removed = await build(None).run("Continue without credentials", previous_state=changed.state)
    assert removed.status == "completed", removed.failure
    assert "Configured HTTPS credentials" not in calls[-1][1]
