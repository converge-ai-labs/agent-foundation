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
from a13n_harness.toolsets.file_media import MAX_MEDIA_UNDERSTANDING_BYTES, MediaUnderstandingResult
from a13n_harness.toolsets.files import FileToolset

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(sys.platform == "win32", reason="Guest filesystem helpers execute on POSIX sandboxes"),
]


class Commands:
    def __init__(self, root: Path, *, maximum: int = MAX_MEDIA_UNDERSTANDING_BYTES) -> None:
        self.configuration = E2BEnvironmentConfiguration(root=str(root), max_file_bytes=maximum)
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


async def test_media_view_respects_smaller_provider_limit(tmp_path):
    (tmp_path / "media.mp4").write_bytes(b"a" * 129)
    commands = Commands(tmp_path, maximum=128)
    provider = Understanding(commands)
    result = await FileToolset(cast(Any, GuestFiles(commands)), media_understanding=provider).view(
        context(), "/media.mp4"
    )
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
