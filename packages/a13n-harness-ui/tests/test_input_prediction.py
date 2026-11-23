import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace

import pytest
from a13n_harness_ui import input_prediction
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.input_prediction import PREDICTION_PROMPT, InputPredictions
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import RootOperationStatus
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@pytest.mark.parametrize("answer", [TextPart("  Keep   going. "), ToolCallPart("not_executed", {})])
async def test_prediction_replays_settled_prefix_and_persists_only_text(tmp_path, monkeypatch, answer):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "auto_chat_title: false\ninput_prediction: {enabled: true}\n")
    foreground = []
    samples = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        foreground.append((list(messages), info))
        yield "I wrote the draft."

    async def sample(messages, info):
        samples.append((messages, info))
        return ModelResponse(parts=[answer])

    async def run_model(self, context, model_id):
        return FunctionModel(stream_function=stream)

    async def prediction_model(self, model_id, *, thread_id, prediction=False, prediction_account=None):
        return FunctionModel(function=sample)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", run_model)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", prediction_model)
    settings = _settings(tmp_path / "state")
    expected = "Keep going." if isinstance(answer, TextPart) else None
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread_id = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Draft a note.")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        detail = await app.get_thread(thread_id)
        continuation_id = detail.continuation_id
        assert continuation_id and detail.thread.title is None
        assert not samples

        async def manual_title(thread_id, messages, source, model_id):
            assert messages == ["Draft a note."]
            return "Manual title"

        monkeypatch.setattr(app, "_generate_chat_title", manual_title)
        assert await app.suggest_chat_title(thread_id, model_id="model-primary") == "Manual title"
        assert await app.predict_input(thread_id, "stale") is None
        assert not samples
        assert await app.predict_input(thread_id, continuation_id) == expected
        assert await app.predict_input(thread_id, continuation_id) == expected
        assert len(samples) == 1
        messages, info = samples[0]
        assert messages[:-2] == foreground[0][0]
        assert messages[-2].parts[0].content == "I wrote the draft."
        assert messages[-1].parts[0].content == PREDICTION_PROMPT
        assert info.model_settings == foreground[0][1].model_settings
        assert info.function_tools == [replace(tool, metadata=None) for tool in foreground[0][1].function_tools]
        draft = await app.shared_draft(thread_id)
        assert draft.unsent_since is None
        assert (draft.prediction.text if draft.prediction else None) == expected
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert await app.predict_input(thread_id, continuation_id) == expected
        assert len(samples) == 1
        await app.clear_thread_context(thread_id=thread_id, expected_continuation_id=continuation_id)
        assert (await app.shared_draft(thread_id)).prediction is None
        assert await app.predict_input(thread_id, continuation_id) is None


async def test_warm_predictions_share_a_call_until_the_last_waiter_cancels(monkeypatch):
    now = 100.0
    monkeypatch.setattr(input_prediction, "monotonic", lambda: now)
    started = asyncio.Event()
    stopped = asyncio.Event()
    calls = 0

    async def sample(thread_id, *, expires_at):
        nonlocal calls
        calls += 1
        assert expires_at == 400
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    snapshot = SimpleNamespace(prefix=SimpleNamespace(received_at=now), sample=sample)
    owner = InputPredictions(capacity=1)
    owner.retain("thread", "head", snapshot)
    a = asyncio.create_task(owner.get("thread", "head"))
    b = asyncio.create_task(owner.get("thread", "head"))
    await asyncio.wait_for(started.wait(), timeout=2)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    assert calls == 1 and not stopped.is_set()
    b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await b
    await asyncio.wait_for(stopped.wait(), timeout=2)
    now = 400.0
    assert await owner.get("thread", "head") is None
    assert calls == 1


async def test_disabled_then_enabled_chat_does_not_replay_a_prefix_captured_while_disabled(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "auto_chat_title: false\n")

    async def stream(messages, info):
        yield "Ready."

    async def run_model(self, context, model_id):
        return FunctionModel(stream_function=stream)

    async def no_sample(self, *args, **kwargs):
        pytest.fail("A disabled or cold Thread must not sample")

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", run_model)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", no_sample)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        thread_id = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Hello.")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        continuation_id = (await app.get_thread(thread_id)).continuation_id
        assert continuation_id
        assert not (await app.status()).input_prediction_enabled
        assert await app.predict_input(thread_id, continuation_id) is None
        root.write_text(root.read_text() + "input_prediction: {enabled: true}\n")
        await app.reload_configuration()
        assert (await app.status()).input_prediction_enabled
        assert await app.predict_input(thread_id, continuation_id) is None
