"""Count E2B dispatches through the real adapter and helper with a local SDK double."""

import asyncio
import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment._guest_files import GuestFiles
from a13n_harness.providers.environment.e2b.commands import GuestCommands
from a13n_harness.providers.environment.e2b.configuration import E2BEnvironmentConfiguration

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(sys.platform == "win32", reason="Guest helpers execute on POSIX sandboxes"),
]


class Reader:
    def __init__(self, path):
        self.path = path
        self.closed = False
        self.consumed = 0
        self.started = asyncio.Event()
        self.block = False
        self.failure = None

    async def __aenter__(self):
        self.source = self.path.open("rb")
        return self

    async def __aexit__(self, *args):
        self.source.close()
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.consumed:
            if self.failure:
                raise self.failure
            if self.block:
                self.started.set()
                await asyncio.Event().wait()
        chunk = self.source.read(65536)
        if not chunk:
            raise StopAsyncIteration
        self.consumed += len(chunk)
        return chunk


@pytest.fixture
def guest(tmp_path):
    actions = []
    reader = Reader(tmp_path / "file")

    async def run(command, **kwargs):
        argv = shlex.split(command)
        actions.append(json.loads(argv[-1])["action"])
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        return SimpleNamespace(stdout=stdout.decode())

    async def read(path, **kwargs):
        assert Path(path) == reader.path
        assert kwargs["format"] == "stream"
        return reader

    async def write(path, source, **kwargs):
        Path(path).write_bytes(source.read())

    sandbox = SimpleNamespace(
        commands=SimpleNamespace(run=AsyncMock(side_effect=run)),
        files=SimpleNamespace(read=AsyncMock(side_effect=read), write=AsyncMock(side_effect=write)),
    )
    configuration = E2BEnvironmentConfiguration(root=str(tmp_path), python=sys.executable)
    commands = GuestCommands(sandbox, configuration)
    return SimpleNamespace(
        files=GuestFiles(commands), commands=commands, sandbox=sandbox, reader=reader, actions=actions
    )


@pytest.mark.parametrize("size", [0, 1, 65535, 65536, 65537, 1024 * 1024, 16 * 1024 * 1024, 100 * 1024 * 1024])
async def test_complete_read_uses_one_resolution_and_one_native_download(guest, size):
    payload = (bytes(range(256)) * ((size + 255) // 256))[:size]
    guest.reader.path.write_bytes(payload)
    assert await guest.files.read_bytes("/file") == payload
    assert guest.actions == ["resolve"]
    guest.sandbox.files.read.assert_awaited_once()
    assert guest.reader.closed


async def test_default_limit_rejects_above_100_mib_and_closes_reader(guest):
    maximum = guest.commands.configuration.max_file_bytes
    assert maximum == 100 * 1024 * 1024
    with guest.reader.path.open("wb") as source:
        source.truncate(maximum + 1)
    with pytest.raises(EnvironmentError) as caught:
        await guest.files.read_bytes("/file")
    assert caught.value.code == "environment_too_large"
    assert guest.reader.closed
    assert guest.reader.consumed == maximum + 1
    assert guest.actions == ["resolve"]
    guest.sandbox.files.read.assert_awaited_once()


@pytest.mark.parametrize("maximum,size", [(4096, 4096), (65536, 65536), (65536, 65537), (4096, 200000)])
async def test_complete_read_enforces_actual_bytes_and_closes_on_overflow(guest, maximum, size):
    guest.commands.configuration = guest.commands.configuration.model_copy(update={"max_file_bytes": maximum})
    guest.reader.path.write_bytes(b"x" * size)
    if size <= maximum:
        assert len(await guest.files.read_bytes("/file")) == size
    else:
        with pytest.raises(EnvironmentError) as caught:
            await guest.files.read_bytes("/file")
        assert caught.value.code == "environment_too_large"
    assert guest.reader.closed
    assert guest.reader.consumed <= maximum + 65536
    assert guest.actions == ["resolve"]


@pytest.mark.parametrize("failure", ["cancel", "transport"])
async def test_complete_read_closes_native_reader_on_interruption(guest, failure):
    guest.reader.path.write_bytes(b"x" * 200000)
    if failure == "cancel":
        guest.reader.block = True
        task = asyncio.create_task(guest.files.read_bytes("/file"))
        await asyncio.wait_for(guest.reader.started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        guest.reader.failure = EnvironmentError("Download failed", code="environment_unavailable")
        with pytest.raises(EnvironmentError) as caught:
            await guest.files.read_bytes("/file")
        assert caught.value is guest.reader.failure
    assert guest.reader.closed
    assert guest.reader.consumed == 65536
    assert guest.actions == ["resolve"]  # Failed downloads never retry through a helper.


@pytest.mark.parametrize(
    "offset,length",
    [(0, 0), (0, 3), (0, 131072), (65530, 20), (65537, None), (131072, None), (200000, 5), (200000, None)],
)
async def test_ranges_keep_seek_and_eof_semantics_without_downloading_prefix(guest, offset, length):
    payload = bytes(range(256)) * 512
    guest.reader.path.write_bytes(payload)
    result = await guest.files.read_bytes("/file", offset=offset, length=length)
    assert result == payload[offset : None if length is None else offset + length]
    guest.sandbox.files.read.assert_not_awaited()
    assert all(action == "read" for action in guest.actions)
    if length == 0:
        assert guest.actions == []


@pytest.mark.parametrize("offset,length", [(-1, None), (0, -1), (0, 4097)])
async def test_invalid_ranges_rejected_before_io(guest, offset, length):
    guest.commands.configuration = guest.commands.configuration.model_copy(update={"max_file_bytes": 4096})
    with pytest.raises(EnvironmentError) as caught:
        await guest.files.read_bytes("/file", offset=offset, length=length)
    assert caught.value.code == "environment_request_invalid"
    assert guest.actions == []
    guest.sandbox.files.read.assert_not_awaited()


async def test_nonzero_offset_limit_applies_to_remaining_bytes(guest):
    guest.commands.configuration = guest.commands.configuration.model_copy(update={"max_file_bytes": 4096})
    guest.reader.path.write_bytes(b"x" * 8192)
    assert len(await guest.files.read_bytes("/file", offset=4096)) == 4096
    assert len(await guest.files.read_bytes("/file", offset=1, length=4096)) == 4096
    with pytest.raises(EnvironmentError) as caught:
        await guest.files.read_bytes("/file", offset=4095)
    assert caught.value.code == "environment_too_large"
    guest.sandbox.files.read.assert_not_awaited()


@pytest.mark.parametrize(
    "kind,code",
    [
        ("missing", "environment_not_found"),
        ("directory", "environment_request_invalid"),
        ("escape", "environment_denied"),
    ],
)
async def test_complete_read_retains_path_validation_before_download(guest, tmp_path, kind, code):
    if kind == "directory":
        guest.reader.path.mkdir()
    elif kind == "escape":
        guest.reader.path.symlink_to(tmp_path.parent)
    with pytest.raises(EnvironmentError) as caught:
        await guest.files.read_bytes("/file")
    assert caught.value.code == code
    guest.sandbox.files.read.assert_not_awaited()


@pytest.mark.parametrize("operation", ["text", "append", "patch"])
async def test_text_append_and_patch_use_native_complete_read(guest, operation):
    guest.reader.path.write_text("old\n")
    if operation == "text":
        assert (await guest.files.read_text("/file")).text == "old\n"
    elif operation == "append":
        await guest.files.write_text("/file", "new\n", mode="append")
        assert guest.reader.path.read_text() == "old\nnew\n"
    else:
        await guest.files.patch_text("/file", "@@ -1 +1 @@\n-old\n+new\n")
        assert guest.reader.path.read_text() == "new\n"
    # Text append validates UTF-8, then the byte append path reads the original.
    assert guest.sandbox.files.read.await_count == (2 if operation == "append" else 1)
    assert "read" not in guest.actions
    assert guest.reader.closed
