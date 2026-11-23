import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace

import httpx2
import pytest
from a13n_harness.models.transport import create_model_http_client
from a13n_harness.providers.model import routes
from a13n_harness_ui.app import HarnessUiApp, open_harness_ui_app
from a13n_harness_ui.input_prediction import PREDICTION_PROMPT, PredictionCapture
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.shared_drafts import composer_options, options_document
from a13n_harness_ui.surfaces import RootOperationStatus, ThreadMetadataMutation, ThreadMetadataPatch
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModelSettings

from .test_app import _settings, _write_configuration

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


LONG_PREDICTION = "Please add an example for each of the proposed changes. " * 12


@pytest.mark.parametrize("native_search", [False, True], ids=["plain", "native-search"])
@pytest.mark.parametrize("answer", [TextPart(LONG_PREDICTION), ToolCallPart("not_executed", {}), RuntimeError])
async def test_prediction_replays_settled_prefix_and_persists_only_text(tmp_path, monkeypatch, answer, native_search):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "auto_chat_title: false\ninput_prediction: {enabled: true}\n")
    if native_search:
        with (tmp_path / "agents/assistant.yaml").open("a") as agent:
            agent.write("capabilities:\n  - capability: NativeTool\n    configuration: {kind: web_search}\n")
    foreground = []
    samples = []
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    generate = HarnessUiApp._generate_prediction

    async def observe_generation(self, *args):
        try:
            await generate(self, *args)
        finally:
            finished.set()

    monkeypatch.setattr(HarnessUiApp, "_generate_prediction", observe_generation)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        foreground.append((list(messages), info))
        yield "I wrote the draft."

    async def sample(messages, info):
        samples.append((messages, info))
        started.set()
        await release.wait()
        if answer is RuntimeError:
            raise RuntimeError("Provider failed")
        return ModelResponse(parts=[answer])

    async def run_model(self, context, model_id):
        return FunctionModel(stream_function=stream)

    async def prediction_model(self, model_id, *, thread_id, prediction=False, prediction_account=None):
        # Model.prepare_request merges its defaults back into the direct request.
        defaults = (
            OpenAIResponsesModelSettings(openai_native_tools=({"type": "web_search"},)) if native_search else None
        )
        return FunctionModel(function=sample, settings=defaults)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", run_model)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", prediction_model)
    settings = _settings(tmp_path / "state")
    expected = LONG_PREDICTION.strip() if isinstance(answer, TextPart) else None
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread_id = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Draft a note.")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        detail = await app.get_thread(thread_id)
        continuation_id = detail.continuation_id
        assert continuation_id and detail.thread.title is None
        await asyncio.wait_for(started.wait(), timeout=2)

        async def manual_title(thread_id, messages, source, model_id):
            assert messages == ["Draft a note."]
            return "Manual title"

        monkeypatch.setattr(app, "_generate_chat_title", manual_title)
        assert await app.suggest_chat_title(thread_id, model_id="model-primary") == "Manual title"
        latest_root = await app._store.usage.latest_root_request(thread_id=thread_id)
        assert latest_root is not None and latest_root.source == "agent"
        assert await app.predict_input(thread_id, "stale") is None
        assert await app.predict_input(thread_id, continuation_id) is None
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=2)
        assert await app.predict_input(thread_id, continuation_id) == expected
        assert await app.predict_input(thread_id, continuation_id) == expected
        assert len(samples) == 1
        usage = await app.thread_usage(thread_id=thread_id)
        prediction_groups = [group for group in usage.groups if group.source == "input_prediction"]
        if answer is not RuntimeError:
            assert len(prediction_groups) == 1
            assert prediction_groups[0].totals.model_requests == 1
        assert await app._store.usage.latest_root_request(thread_id=thread_id) == latest_root
        messages, info = samples[0]
        assert messages[:-2] == foreground[0][0]
        assert messages[-2].parts[0].content == "I wrote the draft."
        assert messages[-1].parts[0].content == PREDICTION_PROMPT
        foreground_settings = foreground[0][1].model_settings
        expected_settings = (
            {**(foreground_settings or {}), "openai_native_tools": ()} if native_search else foreground_settings
        )
        assert info.model_settings == expected_settings
        assert bool(foreground[0][1].model_request_parameters.native_tools) == native_search
        assert not info.model_request_parameters.native_tools
        assert info.function_tools == [replace(tool, metadata=None) for tool in foreground[0][1].function_tools]
        draft = await app.shared_draft(thread_id)
        assert draft.unsent_since is None
        assert (draft.prediction.text if draft.prediction else None) == expected
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert await app.predict_input(thread_id, continuation_id) == expected
        assert len(samples) == 1
        usage = await app.thread_usage(thread_id=thread_id)
        if answer is not RuntimeError:
            assert any(group.source == "input_prediction" for group in usage.groups)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        if expected is not None:
            stored = await app._store.drafts.get(thread_id)
            assert stored is not None and stored.options_update is not None
            options = options_document()
            options.apply_update(stored.options_update)
            assert (prediction := composer_options(options).prediction) is not None
            assert prediction.text == expected
        await app.clear_thread_context(thread_id=thread_id, expected_continuation_id=continuation_id)
        if expected is not None:
            stored = await app._store.drafts.get(thread_id)
            assert stored is not None and stored.options_update is not None
            options = options_document()
            options.apply_update(stored.options_update)
            assert composer_options(options).prediction is None
        assert await app.predict_input(thread_id, continuation_id) is None


@pytest.mark.parametrize(
    ("settings", "part"),
    [
        ({"openai_conversation_id": "conv-test"}, TextPart("Reply")),
        ({"openai_background": True}, TextPart("Reply")),
        ({"extra_body": {"conversation": "conv-test"}}, TextPart("Reply")),
        ({"extra_body": {"tools": []}}, TextPart("Reply")),
        ({"extra_body": {"background": True}}, TextPart("Reply")),
        ({"extra_body": {"web_search_options": {}}}, TextPart("Reply")),
        ({"anthropic_container": "container-test"}, TextPart("Reply")),
        ({"google_cached_content": "cachedContents/retrieval"}, TextPart("Reply")),
        (None, ToolCallPart("test_tool", {})),
    ],
    ids=[
        "conversation",
        "background",
        "body-conversation",
        "body-tools",
        "body-background",
        "body-web-search",
        "anthropic-container",
        "google-cached-content",
        "tool-call",
    ],
)
async def test_prediction_capture_discards_unreplayable_requests(settings, part):
    capture = PredictionCapture()
    ctx = SimpleNamespace(run_id="run-test")
    request = ModelRequestContext(
        model=FunctionModel(function=lambda _messages, _info: ModelResponse(parts=[TextPart("Reply")])),
        model_id="model-test",
        messages=[],
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )

    async def run():
        await capture.after_model_request(
            ctx, request_context=request, response=ModelResponse(parts=[TextPart("Reply")])
        )
        assert capture.prefix is not None
        excluded = replace(request, model_settings=settings)
        response = ModelResponse(parts=[part])
        assert await capture.after_model_request(ctx, request_context=excluded, response=response) is response
        assert capture.prefix is None

    await capture.wrap_run(ctx, handler=run)


@pytest.mark.parametrize("invalidate", ["new_run", "clear", "archive", "shutdown"])
async def test_invalidating_a_thread_cancels_pending_prediction(tmp_path, monkeypatch, invalidate):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "auto_chat_title: false\ninput_prediction: {enabled: true}\n")
    started = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0

    async def stream(messages, info):
        yield "Ready."

    async def run_model(self, context, model_id):
        return FunctionModel(stream_function=stream)

    async def prediction_model(self, model_id, *, thread_id, prediction=False, prediction_account=None):
        async def sample(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            return ModelResponse(parts=[TextPart("")])

        return FunctionModel(function=sample)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", run_model)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", prediction_model)
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread_id = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Hello.")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        continuation_id = (await app.get_thread(thread_id)).continuation_id
        assert continuation_id
        await asyncio.wait_for(started.wait(), timeout=2)
        if invalidate == "new_run":
            receipt = await app.submit_thread(thread_id=thread_id, prompt="Next.")
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        elif invalidate == "clear":
            await app.clear_thread_context(thread_id=thread_id, expected_continuation_id=continuation_id)
        elif invalidate == "archive":
            detail = await app.get_thread(thread_id)
            await app.update_thread_metadata(
                thread_id=thread_id,
                mutation=ThreadMetadataMutation(
                    expected_version=detail.thread.metadata_version, patch=ThreadMetadataPatch(archived=True)
                ),
            )
        if invalidate != "shutdown":
            await asyncio.wait_for(cancelled.wait(), timeout=2)
            assert await app.predict_input(thread_id, continuation_id) is None
    assert cancelled.is_set()
    if invalidate == "shutdown":
        async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
            assert await app.predict_input(thread_id, continuation_id) is None
        assert calls == 1


async def test_prediction_does_not_retry_failed_provider_request(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "auto_chat_title: false\ninput_prediction: {enabled: true}\n")
    monkeypatch.setenv("OPENAI_API_KEY", "local-fixture-key")
    calls = []
    finished = asyncio.Event()
    generate = HarnessUiApp._generate_prediction

    async def stream(messages, info):
        yield "Ready."

    async def run_model(self, context, model_id):
        return FunctionModel(stream_function=stream)

    async def observe_generation(self, *args):
        try:
            await generate(self, *args)
        finally:
            finished.set()

    def reject(request):
        calls.append(request.url.path)
        return httpx2.Response(503, json={"error": {"message": "Unavailable"}})

    def scripted_client(**kwargs):
        assert kwargs["retry"] is None
        return create_model_http_client(**kwargs, transport=httpx2.MockTransport(reject))

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", run_model)
    monkeypatch.setattr(HarnessUiApp, "_generate_prediction", observe_generation)
    monkeypatch.setattr(routes, "create_model_http_client", scripted_client)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        thread_id = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Hello.")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        continuation_id = (await app.get_thread(thread_id)).continuation_id
        assert continuation_id
        await asyncio.wait_for(finished.wait(), timeout=2)
        assert await app.predict_input(thread_id, continuation_id) is None
        assert calls == ["/v1/responses"]


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
