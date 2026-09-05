"""Opt-in end-to-end native E2B execution through the ordinary Harness Toolset."""

import json
import os
import secrets
from collections.abc import AsyncIterator

import pytest
from a13n_environment_provider import E2BEnvironment, E2BProviderConfiguration, E2BProviderRuntime
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
            control = E2BEnvironment(
                configuration, environment_id=identity, state=environment.dump_state(), runtime=runtime
            )
            await control.destroy()
