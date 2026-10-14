"""Media views respect both tool and provider limits without oversized range requests."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import HarnessModelCharacteristics, ModelCapability
from a13n_harness.environment.mounts import EnvironmentPath
from a13n_harness.environment.providers import FileScopeSelection
from a13n_harness.environment.virtual_files import VirtualFileOperator, _PreparedFile
from a13n_harness.metering import ModelUsageBinding
from a13n_harness.providers.environment._guest import files as guest_helpers
from a13n_harness.providers.environment._guest_files import GuestFiles
from a13n_harness.providers.environment.e2b.configuration import E2BEnvironmentConfiguration
from a13n_harness.toolsets.file_media import (
    MAX_IMAGE_UNDERSTANDING_BYTES,
    MAX_MEDIA_UNDERSTANDING_BYTES,
    AgentMediaUnderstandingProvider,
    MediaUnderstandingResult,
    media_source_byte_limit,
)
from a13n_harness.toolsets.files import FileToolset
from PIL import Image
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(sys.platform == "win32", reason="Guest filesystem helpers execute on POSIX sandboxes"),
]


class Commands:
    def __init__(self, root: Path, *, maximum: int | None = None) -> None:
        self.configuration = E2BEnvironmentConfiguration(root=str(root))
        if maximum is not None:
            self.configuration = self.configuration.model_copy(update={"max_file_bytes": maximum})
        self.closed = False
        self.started = asyncio.Event()
        self.block = False
        self.reported_size: int | None = None
        self.hide_size = False

    async def files(self, action, arguments, **kwargs):
        result = guest_helpers.execute(
            {"configuration": self.configuration.model_dump(), "action": action, "arguments": arguments}
        )
        if action == "stat" and self.hide_size:
            result["size"] = self.reported_size
        return result

    async def read_stream(self, path):
        try:
            with Path(path).open("rb") as source:
                while chunk := source.read(65_536):
                    self.started.set()
                    yield chunk
                    if self.block:
                        await asyncio.Event().wait()
        finally:
            self.closed = True


class Understanding:
    def __init__(self, commands: Commands) -> None:
        self.commands = commands
        self.requests = []

    async def understand(self, request, *, usage=None):
        assert self.commands.closed
        self.requests.append(request)
        return MediaUnderstandingResult(text="media understood")


def context(*, native: str | None = None):
    return cast(
        Any,
        SimpleNamespace(
            deps=SimpleNamespace(
                model_characteristics=(
                    HarnessModelCharacteristics(capabilities={ModelCapability(f"{native}_understanding")})
                    if native
                    else None
                ),
                usage_attribution=ModelUsageBinding.standalone(source="test").ledger,
            ),
            tool_call_id="media-view",
        ),
    )


@pytest.mark.parametrize("kind,extension", [("image", "jpg"), ("video", "mp4"), ("audio", "wav")])
@pytest.mark.parametrize("size", [128, MAX_MEDIA_UNDERSTANDING_BYTES])
async def test_guest_media_view_reaches_understanding_at_default_limits(tmp_path, kind, extension, size):
    data = b"a" * size
    (tmp_path / f"media.{extension}").write_bytes(data)
    commands = Commands(tmp_path)
    provider = Understanding(commands)
    result = await FileToolset(cast(Any, GuestFiles(commands)), media_understanding=provider).view(
        context(), f"/media.{extension}", instructions="Describe this media."
    )
    assert result.return_value == "media understood"
    assert len(provider.requests) == 1
    assert provider.requests[0].source_bytes == data
    assert provider.requests[0].kind == kind
    assert provider.requests[0].instructions == "Describe this media."


async def test_guest_media_view_preserves_native_delivery(tmp_path):
    (tmp_path / "image.png").write_bytes(b"image bytes")
    commands = Commands(tmp_path)
    provider = Understanding(commands)
    result = await FileToolset(cast(Any, GuestFiles(commands)), media_understanding=provider).view(
        context(native="image"), "/image.png"
    )
    assert result.content[0].data == b"image bytes"
    assert result.content[0].media_type == "image/png"
    assert commands.closed
    assert not provider.requests


@pytest.mark.parametrize("reported_size", [None, 1])
@pytest.mark.parametrize("provider_limit", [MAX_MEDIA_UNDERSTANDING_BYTES, MAX_MEDIA_UNDERSTANDING_BYTES * 2])
async def test_oversized_media_closes_all_readers_and_mount_leases(tmp_path, reported_size, provider_limit):
    (tmp_path / "media.mp4").write_bytes(b"a" * (MAX_MEDIA_UNDERSTANDING_BYTES + 1))
    commands = Commands(tmp_path, maximum=provider_limit)
    commands.hide_size = True
    commands.reported_size = reported_size
    backend = GuestFiles(commands)
    active = 0

    def resolve(path):
        return FileScopeSelection(
            path, EnvironmentPath(mount_id="mnt-test", execution_id="exec-test", path=path), "test"
        )

    @asynccontextmanager
    async def prepare(selection, action):
        nonlocal active
        active += 1
        try:
            yield _PreparedFile(selection.resolved_path, backend, lambda result: None, lambda path: path)
        finally:
            active -= 1

    provider = Understanding(commands)
    toolset = FileToolset(VirtualFileOperator(resolve, prepare), media_understanding=provider)
    result = await toolset.view(context(), "/media.mp4")
    assert result["error"]["code"] == "environment_too_large"
    assert commands.closed
    assert active == 0
    assert not provider.requests


@pytest.mark.parametrize("extension", ["png", "mp4", "wav"])
async def test_media_view_respects_smaller_provider_limit(tmp_path, extension):
    path = f"/media.{extension}"
    (tmp_path / path.removeprefix("/")).write_bytes(b"a" * 129)
    commands = Commands(tmp_path, maximum=128)
    provider = Understanding(commands)
    result = await FileToolset(cast(Any, GuestFiles(commands)), media_understanding=provider).view(context(), path)
    assert result["error"]["code"] == "environment_too_large"
    assert commands.closed
    assert not provider.requests


async def test_cancelled_media_view_closes_provider_reader(tmp_path):
    (tmp_path / "media.mp4").write_bytes(b"a" * 128)
    commands = Commands(tmp_path)
    commands.block = True
    provider = Understanding(commands)
    toolset = FileToolset(cast(Any, GuestFiles(commands)), media_understanding=provider)
    task = asyncio.create_task(toolset.view(context(), "/media.mp4"))
    await commands.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert commands.closed
    assert not provider.requests


@pytest.mark.parametrize("native", [False, True])
async def test_image_view_accepts_exactly_100_mib(tmp_path, native):
    maximum = MAX_IMAGE_UNDERSTANDING_BYTES
    assert maximum == 100 * 1024 * 1024
    with (tmp_path / "image.png").open("wb") as source:
        source.truncate(maximum)
    commands = Commands(tmp_path)
    provider = Understanding(commands)
    result = await FileToolset(cast(Any, GuestFiles(commands)), media_understanding=provider).view(
        context(native="image" if native else None), "/image.png"
    )
    assert commands.closed
    if native:
        assert len(result.content[0].data) == maximum
        assert not provider.requests
    else:
        assert result.return_value == "media understood"
        assert len(provider.requests[0].source_bytes) == maximum


@pytest.mark.parametrize("kind,extension", [("image", "png"), ("video", "mp4"), ("audio", "wav")])
@pytest.mark.parametrize("reported_size", ["actual", None, 1])
async def test_kind_limit_rejects_metadata_or_stream_overflow(tmp_path, kind, extension, reported_size):
    maximum = media_source_byte_limit(kind)
    with (tmp_path / f"media.{extension}").open("wb") as source:
        source.truncate(maximum + 1)
    commands = Commands(tmp_path, maximum=maximum * 2)
    commands.hide_size = reported_size != "actual"
    commands.reported_size = reported_size
    provider = Understanding(commands)
    result = await FileToolset(cast(Any, GuestFiles(commands)), media_understanding=provider).view(
        context(), f"/media.{extension}"
    )
    assert result["error"]["code"] == "environment_too_large"
    assert commands.started.is_set() == (reported_size != "actual")
    assert commands.closed == (reported_size != "actual")
    assert not provider.requests


async def test_large_image_view_reaches_default_compression_before_model(tmp_path, reviewer_context):
    path = tmp_path / "image.png"
    with Image.new("RGB", (2500, 2500), "red") as image:
        image.save(path, format="PNG", compress_level=0)
    original = path.read_bytes()
    assert MAX_MEDIA_UNDERSTANDING_BYTES < len(original) < MAX_IMAGE_UNDERSTANDING_BYTES
    commands = Commands(tmp_path)
    calls = []

    def understand(messages, info):
        assert commands.closed
        images = [
            item
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, list)
            for item in part.content
            if isinstance(item, BinaryContent)
        ]
        assert len(images) == 1
        assert images[0].media_type == "image/jpeg"
        assert len(images[0].data) <= (5 * 1024 * 1024 // 4) * 3
        calls.append(images[0])
        return ModelResponse(parts=[TextPart("large image understood")])

    provider = AgentMediaUnderstandingProvider(models={"image": FunctionModel(understand)})
    ctx = cast(Any, SimpleNamespace(deps=reviewer_context, tool_call_id="large-image-view"))
    result = await FileToolset(cast(Any, GuestFiles(commands)), media_understanding=provider).view(ctx, "/image.png")
    assert not isinstance(result, dict), result
    assert result.return_value == "large image understood"
    assert len(calls) == 1
    assert path.read_bytes() == original
