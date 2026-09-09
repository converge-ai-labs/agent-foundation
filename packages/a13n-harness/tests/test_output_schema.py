"""Declarative output constraints apply to model retries and plugin replacements."""

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    DefinitionError,
    HarnessBuilder,
    HarnessRunResult,
    PluginError,
    RunBindings,
    RunCleanupError,
)
from a13n_harness.plugins import PluginRunExchange, PluginRunNext, PluginRunResponse
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"$ref": "#/$defs/item"}, "minItems": 1},
        "note": {"type": ["string", "null"]},
    },
    "required": ["items"],
    "additionalProperties": False,
    "$defs": {
        "item": {
            "type": "object",
            "properties": {"value": {"type": "integer", "minimum": 1}, "kind": {"enum": ["ok"]}},
            "required": ["value", "kind"],
            "additionalProperties": False,
        }
    },
}
VALID = {"items": [{"value": 2, "kind": "ok"}], "note": None}
INVALID = [
    {},
    {"items": []},
    {"items": [{"value": "2", "kind": "ok"}]},
    {"items": [{"value": True, "kind": "ok"}]},
    {"items": [{"value": 0, "kind": "ok"}]},
    {"items": [{"value": 2, "kind": "bad"}]},
    {"items": [{"value": 2}]},
    {**VALID, "extra": 1},
]


def output_model(payloads: list[dict[str, Any]], calls: list[int]) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
        del messages
        payload = payloads[min(len(calls), len(payloads) - 1)]
        calls.append(len(calls))
        yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args=json.dumps(payload), tool_call_id="output")}

    return FunctionModel(stream_function=stream)


@pytest.mark.parametrize("invalid", INVALID)
async def test_model_retries_schema_violations_then_accepts_valid_output(invalid: dict[str, Any]) -> None:
    calls: list[int] = []
    executable = HarnessBuilder().build(
        AgentSpec(output_schema=SCHEMA), output_type=None, model=output_model([invalid, VALID], calls)
    )
    result = await executable.run("produce output", bindings=RunBindings.embedded())
    assert result.output_or_raise() == VALID
    assert len(calls) == 2


async def test_repeated_invalid_output_exhausts_native_retry_budget() -> None:
    calls: list[int] = []
    executable = HarnessBuilder().build(
        AgentSpec(output_schema=SCHEMA), output_type=None, model=output_model([{}], calls)
    )
    result = await executable.run("produce output", bindings=RunBindings.embedded())
    assert result.status == "failed"
    assert len(calls) == 2


class ReplaceOutput(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "replace-output"

    def wrap_run(self, exchange: PluginRunExchange, call_next: PluginRunNext) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessRunResult) and item.status == "completed":
                    item = item.replace(output={"items": [{"value": "wrong", "kind": "ok"}]})
                yield item

        return PluginRunResponse(iterate())


async def test_plugin_cannot_replace_valid_output_with_schema_violation() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(output_schema=SCHEMA),
        output_type=None,
        model=output_model([VALID], []),
        plugins=(ReplaceOutput(),),
    )
    with pytest.raises(RunCleanupError) as error:
        await executable.run("produce output", bindings=RunBindings.embedded())
    assert any(isinstance(cause, PluginError) and cause.code == "plugin_result_invalid" for cause in error.value.causes)


async def test_invalid_schema_keyword_is_rejected_at_build() -> None:
    with pytest.raises(DefinitionError) as error:
        HarnessBuilder().build(
            AgentSpec(output_schema={"type": "object", "required": "value"}),
            output_type=None,
            model=output_model([{}], []),
        )
    assert error.value.code == "agent_build_failed"


async def test_external_reference_does_not_trigger_network_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    def unexpected_retrieval(*args, **kwargs):
        pytest.fail("Output validation attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected_retrieval)
    calls: list[int] = []
    executable = HarnessBuilder().build(
        AgentSpec(output_schema={"type": "object", "properties": {"value": {"$ref": "https://example.test/schema"}}}),
        output_type=None,
        model=output_model([{"value": 1}], calls),
    )
    result = await executable.run("produce output", bindings=RunBindings.embedded())
    assert result.status == "failed"
    assert len(calls) == 2
