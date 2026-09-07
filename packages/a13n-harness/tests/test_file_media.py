from __future__ import annotations

import asyncio
from collections.abc import Mapping
from importlib.resources import files

import a13n_harness.toolsets.file_media as file_media_module
import pytest
from a13n_harness import RunError
from a13n_harness.toolsets import (
    AUDIO_UNDERSTANDING_MODEL_ENV,
    IMAGE_UNDERSTANDING_MODEL_ENV,
    VIDEO_UNDERSTANDING_MODEL_ENV,
    VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV,
    AgentMediaUnderstandingProvider,
    MediaUnderstandingError,
    MediaUnderstandingRequest,
)
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, SystemPromptPart, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("kind", "media_type", "model_environment"),
    (
        ("image", "image/png", IMAGE_UNDERSTANDING_MODEL_ENV),
        ("video", "video/mp4", VIDEO_UNDERSTANDING_MODEL_ENV),
        ("audio", "audio/mpeg", AUDIO_UNDERSTANDING_MODEL_ENV),
    ),
)
async def test_environment_provider_runs_default_agent_for_each_media_kind(
    kind: str,
    media_type: str,
    model_environment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[ModelMessage]] = []

    def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        assert info.model_settings is not None
        assert info.model_settings["temperature"] == 0.1
        return ModelResponse(
            parts=[TextPart(f"understood {kind}")],
            model_name=f"{kind}-model",
            provider_name="function",
            usage=RequestUsage(input_tokens=7, output_tokens=2, output_audio_tokens=4),
        )

    selected: list[str] = []
    model = FunctionModel(understand)

    def infer_model(model_key: str):
        selected.append(model_key)
        return model

    monkeypatch.setattr(file_media_module, "infer_model", infer_model)
    provider = AgentMediaUnderstandingProvider.from_environment({model_environment: f"test:{kind}-model"})

    assert provider is not None
    result = await provider.understand(
        MediaUnderstandingRequest(
            kind=kind,
            media_type=media_type,
            source_name=f"sample.{kind}",
            source_bytes=b"media-bytes",
        )
    )

    assert selected == [f"test:{kind}-model"]
    assert result.text == f"understood {kind}"
    assert len(result.usage) == 1
    assert result.usage[0].product == model.model_name
    assert {measure.unit: measure.quantity for measure in result.usage[0].measures} == {
        "requests": 1,
        "input_tokens": 7,
        "output_tokens": 2,
        "output_audio_tokens": 4,
    }
    binary = _binary_content(calls[0])
    assert binary.data == b"media-bytes"
    assert binary.media_type == media_type
    system_prompt = next(
        part.content
        for message in calls[0]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, SystemPromptPart)
    )
    expected_system_prompt = (
        files("a13n_harness.toolsets.prompts").joinpath(f"{kind}_understanding.md").read_text(encoding="utf-8").strip()
    )
    assert system_prompt
    assert system_prompt == expected_system_prompt
    default_instruction = next(
        item
        for message in calls[0]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, list)
        for item in part.content
        if isinstance(item, str)
    )
    assert "Unclear or Omitted" in default_instruction


async def test_default_agent_retries_invalid_output_within_its_own_budget() -> None:
    calls = 0

    def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        del messages, info
        calls += 1
        return ModelResponse(parts=[TextPart("   " if calls < 3 else "valid analysis")])

    provider = AgentMediaUnderstandingProvider(models={"image": FunctionModel(understand)})
    result = await provider.understand(
        MediaUnderstandingRequest(
            kind="image",
            media_type="image/png",
            source_name="sample.png",
            source_bytes=b"media-bytes",
        )
    )

    assert result.text == "valid analysis"
    assert calls == 3


async def test_default_agent_preserves_usage_when_output_validation_exhausts() -> None:
    entered = 0
    exited = 0

    class LifecycleFunctionModel(FunctionModel):
        async def __aenter__(self):
            nonlocal entered
            entered += 1
            return await super().__aenter__()

        async def __aexit__(self, *args):
            nonlocal exited
            exited += 1
            return await super().__aexit__(*args)

    def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(
            parts=[TextPart("   ")],
            usage=RequestUsage(input_tokens=2, output_tokens=1, output_audio_tokens=4),
        )

    provider = AgentMediaUnderstandingProvider(models={"image": LifecycleFunctionModel(understand)})
    with pytest.raises(MediaUnderstandingError) as exc_info:
        await provider.understand(
            MediaUnderstandingRequest(
                kind="image",
                media_type="image/png",
                source_name="sample.png",
                source_bytes=b"media-bytes",
            )
        )

    assert exc_info.value.code == "media_understanding_response_invalid"
    assert len(exc_info.value.usage) == 1
    assert {measure.unit: measure.quantity for measure in exc_info.value.usage[0].measures} == {
        "requests": 3,
        "input_tokens": 6,
        "output_tokens": 3,
        "output_audio_tokens": 12,
    }
    assert entered == 1
    assert exited == 1


async def test_default_agent_maps_timeout_without_fabricating_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    exited = 0

    class CleanupFailingFunctionModel(FunctionModel):
        async def __aexit__(self, *args):
            nonlocal exited
            exited += 1
            await super().__aexit__(*args)
            raise RuntimeError("cleanup failed")

    async def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setitem(file_media_module._TIMEOUT_SECONDS_BY_KIND, "image", 0.01)
    provider = AgentMediaUnderstandingProvider(models={"image": CleanupFailingFunctionModel(understand)})

    with pytest.raises(MediaUnderstandingError) as exc_info:
        await provider.understand(
            MediaUnderstandingRequest(
                kind="image",
                media_type="image/png",
                source_name="sample.png",
                source_bytes=b"media-bytes",
            )
        )

    assert started.is_set()
    assert exc_info.value.code == "media_understanding_timeout"
    assert exc_info.value.usage == ()
    assert exited == 1


async def test_default_agent_preserves_timeout_that_starts_during_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_started = asyncio.Event()

    class CleanupBlockingFunctionModel(FunctionModel):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            cleanup_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exc:
                raise RuntimeError("cleanup replaced cancellation") from exc

    def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart("valid analysis")])

    monkeypatch.setitem(file_media_module._TIMEOUT_SECONDS_BY_KIND, "image", 0.01)
    provider = AgentMediaUnderstandingProvider(models={"image": CleanupBlockingFunctionModel(understand)})

    with pytest.raises(MediaUnderstandingError) as exc_info:
        await provider.understand(
            MediaUnderstandingRequest(
                kind="image",
                media_type="image/png",
                source_name="sample.png",
                source_bytes=b"media-bytes",
            )
        )

    assert cleanup_started.is_set()
    assert exc_info.value.code == "media_understanding_timeout"


async def test_default_agent_preserves_external_cancellation_that_starts_during_cleanup() -> None:
    cleanup_started = asyncio.Event()

    class CleanupBlockingFunctionModel(FunctionModel):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            cleanup_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exc:
                raise RuntimeError("cleanup replaced cancellation") from exc

    def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart("valid analysis")])

    provider = AgentMediaUnderstandingProvider(models={"image": CleanupBlockingFunctionModel(understand)})
    task = asyncio.create_task(
        provider.understand(
            MediaUnderstandingRequest(
                kind="image",
                media_type="image/png",
                source_name="sample.png",
                source_bytes=b"media-bytes",
            )
        )
    )
    await cleanup_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_default_agent_propagates_harness_failures() -> None:
    failure = RunError("usage invariant failed", code="usage_record_conflict")

    def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        raise failure

    provider = AgentMediaUnderstandingProvider(models={"image": FunctionModel(understand)})

    with pytest.raises(RunError) as exc_info:
        await provider.understand(
            MediaUnderstandingRequest(
                kind="image",
                media_type="image/png",
                source_name="sample.png",
                source_bytes=b"media-bytes",
            )
        )

    assert exc_info.value is failure


async def test_default_agent_maps_provider_failure_with_known_usage() -> None:
    def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        raise RuntimeError("provider secret")

    provider = AgentMediaUnderstandingProvider(models={"image": FunctionModel(understand)})

    with pytest.raises(MediaUnderstandingError) as exc_info:
        await provider.understand(
            MediaUnderstandingRequest(
                kind="image",
                media_type="image/png",
                source_name="sample.png",
                source_bytes=b"media-bytes",
            )
        )

    assert exc_info.value.code == "media_understanding_failed"
    assert exc_info.value.usage == ()


async def test_default_agent_preserves_external_cancellation_and_closes_model() -> None:
    started = asyncio.Event()
    entered = 0
    exited = 0

    class LifecycleFunctionModel(FunctionModel):
        async def __aenter__(self):
            nonlocal entered
            entered += 1
            return await super().__aenter__()

        async def __aexit__(self, *args):
            nonlocal exited
            exited += 1
            await super().__aexit__(*args)
            raise RuntimeError("cleanup failed")

    async def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    provider = AgentMediaUnderstandingProvider(models={"image": LifecycleFunctionModel(understand)})
    task = asyncio.create_task(
        provider.understand(
            MediaUnderstandingRequest(
                kind="image",
                media_type="image/png",
                source_name="sample.png",
                source_bytes=b"media-bytes",
            )
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert entered == 1
    assert exited == 1


async def test_default_agent_shares_one_model_lifecycle_across_parallel_calls() -> None:
    entered = 0
    exited = 0
    active_requests = 0
    both_started = asyncio.Event()
    release = asyncio.Event()

    class LifecycleFunctionModel(FunctionModel):
        async def __aenter__(self):
            nonlocal entered
            entered += 1
            return await super().__aenter__()

        async def __aexit__(self, *args):
            nonlocal exited
            exited += 1
            return await super().__aexit__(*args)

    async def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal active_requests
        del messages, info
        active_requests += 1
        if active_requests == 2:
            both_started.set()
        await release.wait()
        return ModelResponse(parts=[TextPart("parallel analysis")])

    provider = AgentMediaUnderstandingProvider(models={"image": LifecycleFunctionModel(understand)})
    request = MediaUnderstandingRequest(
        kind="image",
        media_type="image/png",
        source_name="sample.png",
        source_bytes=b"media-bytes",
    )
    first = asyncio.create_task(provider.understand(request))
    second = asyncio.create_task(provider.understand(request))
    await both_started.wait()
    release.set()

    results = await asyncio.gather(first, second)

    assert [result.text for result in results] == ["parallel analysis", "parallel analysis"]
    assert entered == 1
    assert exited == 1


def test_environment_provider_is_absent_without_configured_models() -> None:
    assert AgentMediaUnderstandingProvider.from_environment({}) is None


def test_environment_provider_reads_only_the_requested_media_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str] = []
    image_model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("image")]))

    def infer_model(model_key: str):
        selected.append(model_key)
        return image_model

    monkeypatch.setattr(file_media_module, "infer_model", infer_model)
    environment = {
        IMAGE_UNDERSTANDING_MODEL_ENV: "test:image-model",
        VIDEO_UNDERSTANDING_MODEL_ENV: "test:video-model",
        VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV: "[]",
    }

    provider = AgentMediaUnderstandingProvider.from_environment(environment, kind="image")

    assert provider is not None
    assert selected == ["test:image-model"]
    assert AgentMediaUnderstandingProvider.from_environment(environment, kind="audio") is None


def test_environment_provider_maps_invalid_model_selection_to_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_model(model_key: str):
        del model_key
        raise ValueError("invalid model")

    monkeypatch.setattr(file_media_module, "infer_model", reject_model)

    with pytest.raises(MediaUnderstandingError) as exc_info:
        AgentMediaUnderstandingProvider.from_environment(
            {IMAGE_UNDERSTANDING_MODEL_ENV: "invalid:model"},
            kind="image",
        )

    assert exc_info.value.code == "media_understanding_configuration_invalid"


def test_environment_provider_rejects_invalid_model_settings() -> None:
    environment: Mapping[str, str] = {
        IMAGE_UNDERSTANDING_MODEL_ENV: "test:image-model",
        "A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS": "[]",
    }

    with pytest.raises(MediaUnderstandingError) as exc_info:
        AgentMediaUnderstandingProvider.from_environment(environment)

    assert exc_info.value.code == "media_understanding_configuration_invalid"


def _binary_content(messages: list[ModelMessage]) -> BinaryContent:
    return next(
        item
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, list)
        for item in part.content
        if isinstance(item, BinaryContent)
    )
