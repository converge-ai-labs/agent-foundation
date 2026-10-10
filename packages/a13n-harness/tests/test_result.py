from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any
from unittest.mock import patch

import pytest
from a13n_harness import AgentDefinition, HarnessBuilder, HarnessRunResult, HarnessState
from a13n_harness import result as result_module
from a13n_harness.plugins import AbstractHarnessPlugin
from a13n_harness.usage import RunUsageSummary
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, TypeAdapter
from pydantic_ai import AgentSpec
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.output import TextOutput

pytestmark = pytest.mark.anyio


def test_replacements_share_only_immutable_history_and_keep_public_views_detached() -> None:
    messages = (ModelRequest(parts=[UserPromptPart("original")]),)
    result = HarnessRunResult(
        thread_id="thread_test",
        run_id="run-1",
        status="completed",
        output={"items": [1]},
        state=HarnessState.new(thread_id="thread_test", message_history=messages),
        usage=RunUsageSummary(details={"custom": 1}),
        _messages=messages,
    )
    with (
        patch.object(result_module, "encode_messages", side_effect=AssertionError("unexpected encoding")),
        patch.object(result_module, "decode_messages", side_effect=AssertionError("unexpected decoding")),
    ):
        replacement = result.replace(output={"items": [2]})
        cancelled = replacement.replace(status="cancelled", output=None, state=None)
    assert cancelled.state is None
    assert (cancelled.thread_id, cancelled.run_id) == ("thread_test", "run-1")
    assert replacement._messages is result._messages
    result.output["items"].append(3)
    replacement.output["items"].append(4)
    replacement.usage.details["custom"] = 9
    assert result.output == {"items": [1]}
    assert replacement.output == {"items": [2]}
    assert replacement.usage.details == {"custom": 1}
    messages[0].parts[0].content = "changed input"
    replacement.all_messages()[0].parts[0].content = "changed view"
    assert result.all_messages()[0].parts[0].content == "original"
    assert replacement.new_messages()[0].parts[0].content == "original"
    with pytest.raises(ValueError, match="Invalid HarnessRunResult"):
        result.replace(state=None)


@dataclass
class _Passthrough(AbstractHarnessPlugin):
    name: str

    @property
    def plugin_id(self) -> str:
        return self.name


async def test_plugin_boundaries_do_not_reserialize_normalized_history() -> None:
    async def response(messages, info):
        yield "done"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentDefinition(
            agent=AgentSpec(),
            model=FunctionModel(stream_function=response),
            output_type=str,
            plugins=tuple(_Passthrough(str(index)) for index in range(3)),
        )
    )
    with (
        patch.object(result_module, "encode_messages", wraps=result_module.encode_messages) as encode,
        patch.object(result_module, "decode_messages", wraps=result_module.decode_messages) as decode,
    ):
        result = await executable.run("hello")
    assert result.output_or_raise() == "done"
    assert len(result.all_messages()) == 2
    assert encode.call_count == 1
    assert decode.call_count == 0


@pytest.mark.parametrize("invalid_suffix", [False, True])
async def test_plugin_result_subclass_history_is_normalized_and_validated(invalid_suffix) -> None:
    from a13n_harness import RunCleanupError

    class CustomResult(HarnessRunResult):
        def new_messages(self):
            if invalid_suffix:
                return (ModelRequest(parts=[UserPromptPart("not in history")]),)
            return super().new_messages()

    class Plugin(_Passthrough):
        async def wrap_run(self, exchange, call_next):
            item = await call_next(exchange)
            return CustomResult(
                thread_id=item.thread_id,
                run_id=item.run_id,
                status=item.status,
                output=item.output,
                state=item.state,
                usage=item.usage,
                _messages=item.all_messages(),
            )

    async def response(messages, info):
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=response),
        plugins=(Plugin("custom-result"),),
    )
    if invalid_suffix:
        with pytest.raises(RunCleanupError) as error:
            await executable.run("hello")
        assert error.value.outcome.output == "done"
        assert error.value.causes[0].code == "plugin_result_invalid"
    else:
        result = await executable.run("hello")
        assert type(result) is HarnessRunResult
        assert result.output_or_raise() == "done"
        assert result.new_messages() == result.all_messages()


def test_cleanup_error_notes_name_cause_types_only():
    from a13n_harness import RunCleanupError

    cause = SyntaxError("invalid syntax", ("fixture.py", 1, 1, "private_source_line"))
    error = RunCleanupError("cleanup failed", outcome=None, causes=(cause, ValueError("private detail")))

    assert error.__notes__ == ["Cleanup cause: SyntaxError", "Cleanup cause: ValueError"]


async def test_output_projection_keeps_declared_serializers_after_plugin_replacement() -> None:
    def output(value: str) -> list[Annotated[int, PlainSerializer(lambda number: f"id-{number}", return_type=str)]]:
        return [int(value)]

    class ReplaceOutput(_Passthrough):
        async def wrap_run(self, exchange, call_next):
            result = await call_next(exchange)
            # A newly constructed candidate must acquire the current owner's
            # contract, not rely on a serializer carried by the inner result.
            return HarnessRunResult(
                thread_id=result.thread_id,
                run_id=result.run_id,
                status="completed",
                output=[8],
                state=result.state,
                usage=result.usage,
                _messages=result.all_messages(),
                _output_adapter=TypeAdapter(Any),
            )

    async def response(messages, info):
        yield "7"

    result = (
        await HarnessBuilder()
        .build(
            AgentSpec(),
            output_type=TextOutput(output),
            model=FunctionModel(stream_function=response),
            plugins=(ReplaceOutput("replace-output"),),
        )
        .run("hello")
    )
    assert result.output_or_raise() == [8]
    assert result.output_json() == ["id-8"]
    assert result.replace(output=[9]).output_json() == ["id-9"]
    projected = result.output_json()
    projected.append("changed")
    assert result.output_json() == ["id-8"]


def test_manual_result_projection_preserves_structured_serializers_and_exclusions() -> None:
    @dataclass
    class Value:
        value: Annotated[int, PlainSerializer(lambda number: f"id-{number}", return_type=str, when_used="json")]

    class Aliased(BaseModel):
        model_config = ConfigDict(serialize_by_alias=True)
        value: int = Field(serialization_alias="publicValue")
        private: str = Field(exclude=True)

    output = {"values": [Value(7), Aliased(value=8, private="hidden")]}
    result = HarnessRunResult(
        thread_id="thread_test",
        run_id="run-test",
        status="completed",
        output=output,
        state=HarnessState.new(thread_id="thread_test"),
        usage=RunUsageSummary(),
    )
    assert result.output_json() == {"values": [{"value": "id-7"}, {"publicValue": 8}]}
    assert result.output_or_raise()["values"][0] == Value(7)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_output_projection_rejects_non_json_without_changing_native_result(value) -> None:
    result = HarnessRunResult(
        thread_id="thread_test",
        run_id="run-test",
        status="completed",
        output=value,
        state=HarnessState.new(thread_id="thread_test"),
        usage=RunUsageSummary(),
    )
    with pytest.raises((TypeError, ValueError)):
        result.output_json()
    assert result.status == "completed"


def test_runtime_projection_rejects_cycles_and_non_finite_serializer_results() -> None:
    from a13n_harness._json import project_json

    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="cycles"):
        project_json(cycle)

    @dataclass
    class NonFinite:
        value: Annotated[int, PlainSerializer(lambda value: float("nan"), return_type=float)]

    with pytest.raises(ValueError, match="finite"):
        project_json({"nested": [NonFinite(7)]})
