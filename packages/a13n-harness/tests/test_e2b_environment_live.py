"""Opt-in end-to-end native E2B execution through the ordinary Harness Toolset."""

import json
import os
import secrets
from collections.abc import AsyncIterator

import pytest
from a13n_environment import E2BEnvironment, E2BProviderConfiguration, E2BProviderRuntime
from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration
from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from pydantic import SecretStr
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


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        return InvocationPolicyDecision.allow()


async def test_harness_shell_uses_native_e2b_and_closes_without_destroying():
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
    configuration = E2BProviderConfiguration(timeout_seconds=120)
    runtime = E2BProviderRuntime(api_key=SecretStr(os.environ["A13N_TEST_E2B_API_KEY"]))
    identity = "environment-harness-" + secrets.token_hex(8)
    environment = E2BEnvironment(configuration, environment_id=identity, state=None, runtime=runtime)
    try:
        result = await executable.run(
            "Execute the test command",
            environment=environment,
            bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=_Allow()),)),
        )
        assert result.output == "done"
        assert "native-e2b-harness" in json.dumps(results)
        assert environment.dump_state() is not None
        control = E2BEnvironment(
            configuration, environment_id=identity, state=environment.dump_state(), runtime=runtime
        )
        assert await control.reconcile() == "running"
    finally:
        try:
            await environment.close()
        finally:
            state = environment.dump_state()
            control = E2BEnvironment(configuration, environment_id=identity, state=state, runtime=runtime)
            await control.destroy()
            if state is not None:
                probe = E2BEnvironment(configuration, environment_id=identity, state=state, runtime=runtime)
                assert await probe.reconcile() == "absent"


async def test_harness_recovers_native_command_in_a_fresh_run_and_attaches_output():
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

    configuration = E2BProviderConfiguration(timeout_seconds=120)
    runtime = E2BProviderRuntime(api_key=SecretStr(os.environ["A13N_TEST_E2B_API_KEY"]))
    identity = "environment-harness-" + secrets.token_hex(8)
    first = E2BEnvironment(configuration, environment_id=identity, state=None, runtime=runtime)
    second = None
    try:
        await executable([lambda _: ("shell_exec", {"command": "cat", "yield_time_seconds": 0})]).run(
            "Start an interactive command", environment=first, bindings=bindings()
        )
        old_reference = results[-1]["process_id"]
        state = first.dump_state()
        assert state is not None
        second = E2BEnvironment(configuration, environment_id=identity, state=state, runtime=runtime)
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
        ).run("Recover and finish the existing command", environment=second, bindings=bindings())
        completed = results[-1]
        assert completed["status"]["exit_code"] == 0
        assert completed["stdout"]["text"] == "recovered-native-command\n"
        assert completed["stdout"]["origin"] == "sdk_text"
        assert completed["stdout"]["coverage"] == "partial"
    finally:
        try:
            if second is not None:
                await second.close()
            await first.close()
        finally:
            state = first.dump_state()
            if state is not None:
                control = E2BEnvironment(configuration, environment_id=identity, state=state, runtime=runtime)
                await control.destroy()
                probe = E2BEnvironment(configuration, environment_id=identity, state=state, runtime=runtime)
                assert await probe.reconcile() == "absent"
