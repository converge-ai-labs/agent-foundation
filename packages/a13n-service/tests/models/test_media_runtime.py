import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.errors import ModelResolutionError
from a13n_harness.toolsets.file_media import (
    AgentMediaUnderstandingProvider,
    MediaUnderstandingError,
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
)
from a13n_service.models.media_runtime import FileMediaUnderstanding
from a13n_service.models.runtime import SnapshotRunModelResolver
from pydantic_ai.models.test import TestModel

from ..interactions.conftest import effective_agent_config

pytestmark = pytest.mark.anyio


def request(kind="image"):
    return MediaUnderstandingRequest(
        kind=kind, media_type=f"{kind}/test", source_name="sample", source_bytes=b"test", instructions="Describe"
    )


async def test_partial_defaults_use_environment_only_for_unset_kind(monkeypatch):
    resolver = Mock(spec=SnapshotRunModelResolver)
    resolver.resolve = AsyncMock(return_value=TestModel(custom_output_text="configured"))
    media = FileMediaUnderstanding({"image": effective_agent_config().resolved_model}, resolver)
    media.bind_thread("executing-thread")
    resolver.resolve.assert_not_called()
    ambient_provider = Mock()
    ambient_provider.understand = AsyncMock(return_value=MediaUnderstandingResult(text="environment"))
    ambient = Mock(return_value=ambient_provider)
    monkeypatch.setattr(AgentMediaUnderstandingProvider, "from_environment", ambient)
    assert (await media.understand(request())).text == "configured"
    ambient.assert_not_called()
    resolver.resolve.assert_awaited_once_with(
        effective_agent_config().resolved_model.execution.model_id, thread_id="executing-thread"
    )
    assert (await media.understand(request("video"))).text == "environment"
    ambient.assert_called_once_with(kind="video")


@pytest.mark.parametrize(
    "error", [ModelResolutionError("disabled", code="model_provider_disabled"), ValueError("invalid")]
)
async def test_configured_failure_never_falls_back(monkeypatch, error):
    resolver = Mock(spec=SnapshotRunModelResolver)
    resolver.resolve = AsyncMock(side_effect=error)
    media = FileMediaUnderstanding({"image": effective_agent_config().resolved_model}, resolver)
    media.bind_thread("thread")
    ambient = Mock()
    monkeypatch.setattr(AgentMediaUnderstandingProvider, "from_environment", ambient)
    with pytest.raises(MediaUnderstandingError, match="media_understanding_configuration_invalid"):
        await media.understand(request())
    ambient.assert_not_called()
    resolver.resolve.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await media.understand(request())


async def test_legacy_effective_config_omits_empty_defaults_from_digest():
    config = effective_agent_config()
    assert "media_understanding" not in config.model_dump(mode="json")
    assert config.model_validate_json(config.model_dump_json()) == config
