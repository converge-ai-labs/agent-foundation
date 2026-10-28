from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path
from typing import Literal

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState, RunBindings
from a13n_harness.content import request_input_content
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_context import CurrentAgentContextCapability
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import RootOperationStatus
from pydantic_ai import Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


def _identity(request: ModelMessage) -> str:
    assert isinstance(request, ModelRequest)
    values = [item for item in request_input_content(request) if item.metadata.source_id == "a13n.agent-context"]
    assert len(values) == 1
    assert values[0].metadata.display is False
    assert isinstance(values[0].value, str)
    return values[0].value


async def test_identity_is_plain_text_and_preserves_tool_results_and_history() -> None:
    seen: list[list[ModelMessage]] = []

    def advance() -> str:
        return "advanced"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        seen.append(deepcopy(messages))
        if len(seen) == 1:
            yield {0: DeltaToolCall(name="advance", json_args="{}", tool_call_id="call-advance")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream, model_name="provider-model"),
        capabilities=(CurrentAgentContextCapability(), Capability(tools=[Tool(advance)])),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert result.state is not None
    expected = (
        '<agent-context source="a13n-harness-ui">\n'
        f"Current Thread: {result.state.thread_id}\n"
        "Current Model: provider-model\n"
        "Use the current Model name when attributing work to a model.\n"
        "</agent-context>"
    )
    assert _identity(seen[0][-1]) == expected
    followup = seen[1][-1]
    assert isinstance(followup, ModelRequest)
    assert isinstance(followup.parts[0], ToolReturnPart)
    assert followup.parts[0].content == "advanced"
    assert _identity(followup).startswith(expected)
    assert "automatic runtime metadata, not a new user message" in _identity(followup)
    assert "Continue the existing task only if work remains" in _identity(followup)
    assert result.run_id not in _identity(followup)
    assert "harness_run_id" not in _identity(followup)
    assert ModelMessagesTypeAdapter.dump_json(seen[1][: len(seen[0])]) == ModelMessagesTypeAdapter.dump_json(seen[0])
    assert any(item.metadata.source_id == "a13n.environment-mounts" for item in request_input_content(followup))
    assert _identity(result.state.message_history[-2]) == _identity(followup)


async def test_identity_is_fresh_after_history_replacement_and_escapes_xml_text() -> None:
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(messages)
        yield "done"

    previous = HarnessState(
        schema_version="1",
        thread_id="thread_retained",
        message_history=(
            ModelRequest(parts=[UserPromptPart("Retained task summary")]),
            ModelResponse(parts=[TextPart("Continue the task")]),
        ),
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream, model_name="vendor/model&<revision>"),
        capabilities=(CurrentAgentContextCapability(),),
    )
    result = await executable.run("continue", previous_state=previous, bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    identity = _identity(seen[0][-1])
    assert "Current Thread: thread_retained\n" in identity
    assert "Current Model: vendor/model&amp;&lt;revision&gt;\n" in identity
    assert result.run_id not in identity


@pytest.mark.parametrize("host_mode", ["cli", "webui"])
async def test_app_projects_current_model_without_inspection_and_refreshes_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host_mode: Literal["cli", "webui"]
) -> None:
    root = _write_configuration(tmp_path)
    model_path = tmp_path / "models/primary.yaml"
    seen: list[tuple[str, list[ModelMessage]]] = []

    async def resolve(self, context, model_id):
        recipe = self._recipes[model_id]
        name = recipe.route.partition(":")[2]

        async def stream(messages, info):
            seen.append((name, deepcopy(messages)))
            assert "Current Model: " + name + "\n" in _identity(messages[-1])
            assert "model-primary" not in _identity(messages[-1])
            assert model_id not in _identity(messages[-1])
            if len(seen) == 1:
                # A new accepted generation must not become this invocation's identity.
                model_path.write_text(model_path.read_text().replace("openai:gpt-5", "openai:gpt-5-next"))
                await app.reload_configuration()
                assert "Current Model: gpt-5\n" in _identity(messages[-1])
                assert "note_write" in {tool.name for tool in info.function_tools}
                yield {
                    0: DeltaToolCall(
                        name="note_write", json_args='{"key":"progress","value":"continue"}', tool_call_id="call-note"
                    )
                }
            else:
                yield "done"

        return FunctionModel(stream_function=stream, model_name=name)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode=host_mode) as app:
        thread = await app.create_thread()
        for prompt in ("first", "second"):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
            operation = await app.wait_root_operation(receipt.receipt_id)
            assert operation.status is RootOperationStatus.completed, operation
        assert [name for name, _ in seen] == ["gpt-5", "gpt-5", "gpt-5-next"]
        for _, messages in seen:
            assert f"Current Thread: {thread.thread_id}\n" in _identity(messages[-1])
        # The new identity is appended; old captured requests are not rewritten.
        assert "Current Model: gpt-5\n" in _identity(seen[-1][1][0])


async def test_reconstructed_children_project_their_own_model_and_thread(tmp_path: Path) -> None:
    from a13n_harness_ui.composition import AgentCompositionResolver, AgentReconstructor
    from a13n_harness_ui.configuration import load_harness_ui_configuration

    from .test_composition import _catalog, _selection, _UnusedOperator, _write_source

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    children = tuple(
        child.model_copy(
            update={
                "definition": child.definition.model_copy(
                    update={
                        "model": child.definition.model.model_copy(update={"route": f"openai:model-{child.name}"}),
                        "capabilities": (),
                        "harness_plugins": (),
                        "mcp_servers": (),
                        "tools": (),
                    }
                )
            }
        )
        for child in composition.root.children
    )
    composition = composition.model_copy(
        update={
            "root": composition.root.model_copy(
                update={"children": children, "capabilities": (), "harness_plugins": (), "mcp_servers": (), "tools": ()}
            )
        }
    )
    rebuilt = AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())
    observed: list[str] = []

    async def stream(messages, info):
        observed.append(_identity(messages[-1]))
        yield "done"

    async def resolve(context, model_id):
        recipe = rebuilt.model_resolver._recipes[model_id]
        return FunctionModel(stream_function=stream, model_name=recipe.route.partition(":")[2])

    nodes = [(rebuilt.executable, "gpt-5")] + [
        (child.executable, f"model-{name}") for name, child in rebuilt.executable.subagents.items()
    ]
    threads = []
    for executable, name in nodes:
        result = await executable.run("identify yourself", bindings=RunBindings.embedded(model_resolver=resolve))
        assert result.output_or_raise() == "done"
        assert result.state is not None
        assert f"Current Model: {name}\n" in observed[-1]
        assert f"Current Thread: {result.state.thread_id}\n" in observed[-1]
        threads.append(result.state.thread_id)
    assert len(set(threads)) == len(nodes)
