"""Opt-in end-to-end native E2B execution through the ordinary Harness Toolset."""

import json
import os
import secrets
from collections.abc import AsyncIterator

import pytest
from a13n_environment.e2b.configuration import E2BEnvironmentConfiguration
from a13n_environment.e2b.provider import E2B
from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration
from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not os.environ.get("A13N_TEST_E2B_API_KEY"), reason="Requires A13N_TEST_E2B_API_KEY"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def managed_environment():
    configuration = E2BEnvironmentConfiguration(timeout_seconds=120)
    identity = "env-harness-" + secrets.token_hex(8)
    async with await E2B.open_provider(credential={"api_key": os.environ["A13N_TEST_E2B_API_KEY"]}) as provider:
        state = await provider.create(configuration, environment_id=identity, operation_id="op-create")
        connector = provider.execution_connector(configuration, environment_id=identity, state=state)
        try:
            yield connector
            assert (await provider.inspect(configuration, environment_id=identity, state=state)).status == "running"
        finally:
            await provider.destroy(configuration, environment_id=identity, state=state, operation_id="op-delete")
            assert (await provider.inspect(configuration, environment_id=identity, state=state)).status == "absent"


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        return InvocationPolicyDecision.allow()


async def test_harness_shell_uses_native_e2b_and_closes_without_destroying(managed_environment):
    results: list[object] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            results.extend(returns)
            yield "done"
        else:
            assert "shell_exec" in {tool.name for tool in info.function_tools}
            yield {
                0: DeltaToolCall(
                    name="shell_exec",
                    json_args=json.dumps({"command": "printf native-e2b-harness", "yield_time_seconds": 10}),
                    tool_call_id="shell-1",
                )
            }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),),
    )
    result = await executable.run(
        "Execute the test command",
        environment=managed_environment,
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=_Allow()),)),
    )
    assert result.output == "done"
    assert "native-e2b-harness" in json.dumps(results)


async def test_harness_recovers_native_command_in_a_fresh_run_and_attaches_output(managed_environment):
    results = []

    def executable(calls):
        index = 0

        async def stream(messages, info):
            nonlocal index
            returns = [
                part.content
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if returns:
                assert isinstance(returns[-1], dict)
                assert returns[-1]["ok"], returns[-1]
                results.append(returns[-1])
            if index == len(calls):
                yield "done"
                return
            tool, args = calls[index](returns)
            index += 1
            assert tool in {definition.name for definition in info.function_tools}
            yield {0: DeltaToolCall(name=tool, json_args=json.dumps(args), tool_call_id=f"call-{index}")}

        return HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=stream),
            capabilities=(DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),),
        )

    def bindings():
        return RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=_Allow()),))

    await executable([lambda _: ("shell_exec", {"command": "cat", "yield_time_seconds": 0})]).run(
        "Start an interactive command", environment=managed_environment, bindings=bindings()
    )
    old_reference = results[-1]["process_id"]
    selected = None

    def attach(returns):
        nonlocal selected
        listing = returns[-1]
        assert len(listing["processes"]) == 1
        selected = listing["processes"][0]["process_id"]
        assert selected != old_reference
        return "shell_wait", {"process_id": selected, "timeout_seconds": 0}

    def feed(returns):
        assert returns[-1]["status"]["phase"] == "running"
        assert returns[-1]["stdout"]["origin"] == "sdk_text"
        assert returns[-1]["stdout"]["coverage"] == "partial"
        return "shell_input", {"process_id": selected, "data": "recovered-native-command\n", "close_stdin": True}

    await executable(
        [
            lambda _: ("shell_info", {}),
            attach,
            feed,
            lambda _: ("shell_wait", {"process_id": selected, "timeout_seconds": 15}),
        ]
    ).run("Recover and finish the existing command", environment=managed_environment, bindings=bindings())
    completed = results[-1]
    assert completed["status"]["exit_code"] == 0
    assert completed["stdout"]["text"] == "recovered-native-command\n"
    assert completed["stdout"]["origin"] == "sdk_text"
    assert completed["stdout"]["coverage"] == "partial"
