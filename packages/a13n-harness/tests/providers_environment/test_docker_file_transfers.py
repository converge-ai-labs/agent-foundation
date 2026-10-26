"""Opt-in raw Docker transfers against a disposable, non-root Linux container."""

import asyncio
import hashlib
import json
import os
import socket
import struct

import pytest
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment._guest_files import GuestFiles
from a13n_harness.providers.environment.docker.commands import DockerCommands
from a13n_harness.providers.environment.docker.configuration import DockerEnvironmentConfiguration
from a13n_harness.providers.environment.docker.runtime import DockerSDKEngine

pytestmark = pytest.mark.anyio
SIZE = 14_106_928


@pytest.fixture
async def guest():
    image = os.environ.get("A13N_TEST_NATIVE_GUEST_IMAGE")
    if not image:
        pytest.skip("Set A13N_TEST_NATIVE_GUEST_IMAGE to an approved local Linux Python image")
    import docker

    client = docker.from_env()
    container = await asyncio.to_thread(client.containers.run, image, ["sleep", "300"], detach=True)
    try:
        result = await asyncio.to_thread(
            container.exec_run,
            [
                "sh",
                "-c",
                "mkdir -p /workspace /tmp/a13n; chown 1000:1000 /workspace /tmp/a13n; echo secret >/root-only; chmod 600 /root-only",
            ],
        )
        assert result.exit_code == 0
        commands = DockerCommands(
            DockerSDKEngine(client),
            container.id,
            DockerEnvironmentConfiguration(user="1000:1000", request_timeout_seconds=15),
        )
        calls = []
        create = client.api.exec_create

        def record(*args, **kwargs):
            calls.append(kwargs)
            assert kwargs["user"] == "1000:1000"
            return create(*args, **kwargs)

        client.api.exec_create = record
        yield GuestFiles(commands), calls, container
    finally:
        await asyncio.to_thread(container.remove, force=True)
        client.close()


async def content(size):
    block = bytes(range(256)) * 256
    for start in range(0, size, len(block)):
        yield block[: min(len(block), size - start)]


@pytest.mark.parametrize("size", [0, SIZE])
async def test_real_docker_large_binary_and_ranges_use_constant_exec_count(guest, size):
    files, calls, _ = guest
    expected = hashlib.sha256()
    async for chunk in content(size):
        expected.update(chunk)
    await files.write_bytes_stream("/workspace/binary", content(size), mode="create")
    assert len(calls) == 4  # resolve, stage, one raw writer, publish
    calls.clear()
    received = hashlib.sha256()
    async for chunk in files.read_bytes_stream("/workspace/binary"):
        assert len(chunk) <= 65536
        received.update(chunk)
    assert received.digest() == expected.digest()
    assert len(calls) == 2  # resolve, one raw reader
    calls.clear()
    offset = max(0, size - 8)
    assert await files.read_bytes("/workspace/binary", offset=offset, length=8) == bytes(
        index % 256 for index in range(offset, size)
    )
    assert len(calls) == 2


async def test_real_docker_permissions_and_write_failures_preserve_target(guest):
    files, _, container = guest
    with pytest.raises(EnvironmentError) as caught:
        await files.read_bytes("/root-only")
    assert caught.value.code == "environment_denied"
    with pytest.raises(EnvironmentError):
        await files.write_bytes_stream("/root-denied", content(1), mode="create")
    await files.write_text("/workspace/file", "winner", mode="create")
    with pytest.raises(EnvironmentError) as caught:
        await files.write_text("/workspace/file", "loser", mode="create")
    assert caught.value.code == "environment_conflict"
    with pytest.raises(EnvironmentError) as caught:
        await files.write_text("/workspace/missing", "loser", mode="replace")
    assert caught.value.code == "environment_not_found"
    assert await files.read_bytes("/workspace/file") == b"winner"
    result = await asyncio.to_thread(container.exec_run, ["find", "/workspace", "-name", ".a13n-*"], user="1000:1000")
    assert not result.output


async def test_real_docker_cancel_upload_joins_writer_before_stage_removal(guest, monkeypatch):
    files, _, container = guest
    started = asyncio.Event()
    write = files.commands.write_stream

    class SlowSource:
        def __init__(self, source):
            self.source = source

        def tell(self):
            return self.source.tell()

        def seek(self, *args):
            return self.source.seek(*args)

        def read(self, size):
            # Model a slow producer without blocking the asyncio loop. The
            # worker must join before the spool is closed and stage removed.
            import time

            time.sleep(0.01)
            return self.source.read(size)

    async def slow_write(path, source):
        started.set()
        await write(path, SlowSource(source))

    monkeypatch.setattr(files.commands, "write_stream", slow_write)
    task = asyncio.create_task(files.write_bytes_stream("/workspace/file", content(SIZE), mode="create"))
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    result = await asyncio.to_thread(container.exec_run, ["find", "/workspace", "-type", "f"], user="1000:1000")
    assert not result.output


async def test_real_docker_early_download_closes_and_limit_is_enforced(guest):
    files, _, _ = guest
    await files.write_bytes_stream("/workspace/binary", content(SIZE), mode="create")
    stream = files.read_bytes_stream("/workspace/binary")
    await anext(stream)
    await asyncio.wait_for(stream.aclose(), 5)
    files.commands.configuration = type(files.commands.configuration)(100)
    with pytest.raises(EnvironmentError) as caught:
        await files.read_bytes("/workspace/binary")
    assert caught.value.code == "environment_too_large"


def docker_frames(frames):

    return b"".join(struct.pack(">BxxxI", channel, len(data)) + data for channel, data in frames)


@pytest.fixture
async def wire_guest():
    from types import SimpleNamespace
    from unittest.mock import Mock

    left, right = socket.socketpair()
    api = SimpleNamespace(
        exec_create=Mock(return_value={"Id": "test-reader"}),
        exec_start=Mock(return_value=left),
        exec_inspect=Mock(return_value={"ExitCode": 0}),
    )
    commands = DockerCommands(
        DockerSDKEngine(SimpleNamespace(api=api)), "test-container", DockerEnvironmentConfiguration()
    )
    try:
        yield commands, right, left
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("damage", ["truncated_frame", "missing_completion", "wrong_hash", "truncated_completion"])
async def test_docker_raw_download_requires_verified_completion(wire_guest, damage):

    commands, peer, connection = wire_guest
    complete = json.dumps({"size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}).encode()
    if damage == "truncated_frame":
        wire = struct.pack(">BxxxI", 1, 8) + b"abc"
    elif damage == "missing_completion":
        wire = docker_frames([(1, b"abc")])
    elif damage == "wrong_hash":
        wire = docker_frames([(1, b"xyz"), (2, complete)])
    else:
        wire = docker_frames([(1, b"abc")]) + struct.pack(">BxxxI", 2, len(complete)) + complete[:12]
    peer.sendall(wire)
    peer.shutdown(socket.SHUT_WR)
    with pytest.raises(EnvironmentError):
        _ = [chunk async for chunk in commands.read_stream("/file")]
    assert connection.fileno() == -1


@pytest.mark.parametrize("valid", [False, True])
async def test_docker_range_download_drains_its_completion_record(wire_guest, valid):
    from unittest.mock import AsyncMock

    commands, peer, connection = wire_guest
    complete = json.dumps({"size": 3, "sha256": hashlib.sha256(b"abc" if valid else b"xyz").hexdigest()}).encode()
    peer.sendall(docker_frames([(1, b"abc"), (2, complete[:12]), (2, complete[12:])]))
    peer.shutdown(socket.SHUT_WR)
    commands.files = AsyncMock(return_value={"path": "/file"})
    if valid:
        assert await commands.read_range("/file", 5, 3) == b"abc"
    else:
        with pytest.raises(EnvironmentError):
            await commands.read_range("/file", 5, 3)
    assert connection.fileno() == -1


async def test_docker_download_allows_completion_to_arrive_before_stdout(wire_guest):
    commands, peer, connection = wire_guest
    completion = json.dumps({"size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}).encode()
    # The Engine multiplexes two pipes; stderr can be read before the final
    # stdout pipe has drained even though the guest flushes stdout first.
    peer.sendall(docker_frames([(2, completion), (1, b"abc")]))
    peer.shutdown(socket.SHUT_WR)
    assert b"".join([chunk async for chunk in commands.read_stream("/file")]) == b"abc"
    assert connection.fileno() == -1
