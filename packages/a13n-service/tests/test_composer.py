"""Composer's provider card uses the native external-tool lifecycle."""

import json

import pytest
from a13n_harness import DeferredToolResume, HarnessBuilder, RunBindings
from a13n_harness.tools import ClientToolsCapability, ClientToolsSpec
from a13n_service.resources.agents.composer import configuration
from a13n_service.resources.agents.definition import client_toolset
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "name,arguments,result",
    [
        (
            "configure_provider",
            {"action": "create", "kind": "model", "type": "openai", "name": "Research", "config": {}},
            {"status": "saved", "provider_id": "mp_fixture"},
        ),
        (
            "configure_webhook",
            {
                "request": {
                    "action": "create",
                    "name": "Runs",
                    "url": "https://example.com/hook",
                    "kinds": ["run.completed"],
                }
            },
            {"status": "saved", "subscription_id": "sub_fixture", "version": 1},
        ),
        (
            "configure_egress_credential",
            {"request": {"action": "create", "name": "GitHub", "allowed_hosts": ["api.github.com"]}},
            {"status": "saved", "credential_id": "cred_fixture", "version": 1, "credential_version": 1},
        ),
    ],
)
async def test_private_configuration_suspends_and_accepts_only_the_client_result(name, arguments, result):

    async def model(messages, info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            assert returns[-1].content == result
            yield "Configured"
        else:
            assert {tool.name for tool in info.function_tools} == {
                "configure_provider",
                "configure_webhook",
                "configure_egress_credential",
            }
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id="card")}

    toolset = client_toolset(configuration("model"))
    assert toolset is not None
    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(ClientToolsCapability(spec=ClientToolsSpec(default_toolsets=(toolset,))),),
    )
    waiting = await executable.run("Configure a provider", bindings=RunBindings.embedded())
    assert waiting.status == "suspended"
    assert waiting.deferred is not None and waiting.state is not None
    assert waiting.deferred.approvals == []
    [entry] = waiting.deferred.calls
    call = entry.target_call
    assert call.tool_name == name and call.args_as_dict() == arguments
    resumed = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=waiting.state,
        deferred_resume=DeferredToolResume(
            waiting.deferred, waiting.deferred.build_results(calls={call.tool_call_id: result})
        ),
    )
    assert resumed.status == "completed" and resumed.output == "Configured"
