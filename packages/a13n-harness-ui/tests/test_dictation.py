import base64

import pytest
from a13n_harness_ui.dictation import DictationInput, transcribe_audio
from pydantic_ai import BinaryContent, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel


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
        assert isinstance(audio, BinaryContent) and audio.media_type == "audio/wav"
        assert "language from the audio" in info.instructions
        assert not info.function_tools
        return ModelResponse(parts=[TextPart("Please run Pyright.")])

    audio = b"RIFF" + bytes(4) + b"WAVE" + bytes(40)
    request = DictationInput(audio=base64.b64encode(audio).decode(), title="类型检查", context="Human: Pyright")
    assert await transcribe_audio(request, FunctionModel(reply), {}) == "Please run Pyright."
