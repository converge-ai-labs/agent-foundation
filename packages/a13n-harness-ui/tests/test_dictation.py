import base64
import json
from contextlib import asynccontextmanager

import anyio
import pytest
from a13n_harness.configuration import RunConfiguration
from a13n_harness_ui import dictation
from a13n_harness_ui.dictation import DictationDraft, DictationInput, clean_transcript, transcribe_audio
from pydantic_ai import BinaryContent, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel


def recording(**kwargs):
    audio = b"RIFF" + bytes(4) + b"WAVE" + bytes(40)
    return DictationInput(audio=base64.b64encode(audio).decode(), **kwargs)


@pytest.mark.anyio
async def test_audio_is_sent_as_audio_with_reference_context_and_no_tools():
    async def reply(messages, info):
        prompt = next(
            part
            for m in messages
            if isinstance(m, ModelRequest)
            for part in m.parts
            if isinstance(part, UserPromptPart)
        )
        context, audio = prompt.content
        assert "Pyright" in context and "类型检查" in context
        assert json.loads(context)["draft"]["selected"] == "Mypy"
        assert isinstance(audio, BinaryContent) and audio.media_type == "audio/wav"
        assert "language from the audio" in info.instructions
        assert not info.function_tools
        return ModelResponse(parts=[TextPart("Please run Pyright.")])

    request = recording(title="类型检查", context="Human: Pyright", draft=DictationDraft(selected="Mypy"))
    assert await transcribe_audio(request, FunctionModel(reply), {}) == "Please run Pyright."


@pytest.mark.anyio
async def test_cleanup_is_text_only_with_frozen_draft_and_no_tools_or_history():
    async def reply(messages, info):
        prompts = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)]
        assert len(prompts) == 1 and isinstance(prompts[0].content, str)
        payload = json.loads(prompts[0].content)
        assert payload == {
            "transcript": "Um run pie right",
            "chat_title": "Types",
            "nearby_messages": "Pyright",
            "draft": {"before": "Please ", "selected": "Mypy", "after": " now."},
        }
        assert not info.function_tools
        assert info.model_settings["openai_reasoning_effort"] == "low"
        assert info.model_settings["openai_store"] is False
        assert "Do not answer questions" in info.instructions
        return ModelResponse(parts=[TextPart("Run Pyright")])

    request = recording(
        title="Types", context="Pyright", draft=DictationDraft(before="Please ", selected="Mypy", after=" now.")
    )
    result = await clean_transcript(request, " Um run pie right ", FunctionModel(reply))
    assert result.text == "Run Pyright" and result.warning is None


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["empty", "error"])
async def test_cleanup_failure_preserves_raw_transcript(failure):
    async def reply(messages, info):
        if failure == "error":
            raise RuntimeError("provider failed")
        return ModelResponse(parts=[TextPart(" ")])

    result = await clean_transcript(recording(), " raw speech ", FunctionModel(reply))
    assert result.text == "raw speech" and result.warning == "cleanup_failed"


@pytest.mark.anyio
async def test_empty_transcript_skips_cleanup():
    async def reply(messages, info):
        pytest.fail("Empty ASR must not invoke a model")

    assert (await clean_transcript(recording(), " \n", FunctionModel(reply))).text == ""


@pytest.mark.anyio
async def test_cleanup_cancellation_discards_result():
    started = anyio.Event()

    async def reply(messages, info):
        started.set()
        await anyio.sleep_forever()

    with anyio.CancelScope() as scope:
        async with anyio.create_task_group() as tasks:

            async def run():
                await clean_transcript(recording(), "raw", FunctionModel(reply))
                pytest.fail("Cancellation must propagate")

            tasks.start_soon(run)
            await started.wait()
            scope.cancel()


@pytest.mark.anyio
@pytest.mark.parametrize("raw", ["", "raw speech"])
async def test_subscription_uses_same_source_and_skips_empty_cleanup(monkeypatch, raw):
    source = object()
    client = object()
    events = []

    async def asr(audio, credential_source, *, configuration):
        assert credential_source is source
        assert audio[:4] == b"RIFF"
        events.append("asr")
        return raw

    @asynccontextmanager
    async def factory(**kwargs):
        assert kwargs["retry"] is None
        events.append("client")
        yield client

    def model(name, **kwargs):
        assert name == "preset-model"
        assert kwargs["credential_source"] is source and kwargs["http_client"] is client
        events.append("model")
        return FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("clean speech")]))

    monkeypatch.setattr("a13n_harness.models.codex_dictation.transcribe", asr)
    monkeypatch.setattr(dictation, "create_model_http_client", factory)
    monkeypatch.setattr("a13n_harness.models.codex.CodexRequestModel", model)
    result = await dictation.transcribe_subscription(
        recording(), source, "preset-model", configuration=RunConfiguration()
    )
    assert result.text == ("clean speech" if raw else "")
    assert events == (["asr", "client", "model"] if raw else ["asr"])
