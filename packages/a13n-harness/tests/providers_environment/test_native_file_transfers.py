"""Recorded binary APIs with real guest path/publication helpers, never cloud targets."""

import asyncio
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import tracemalloc
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.native.commands import NativeCommands
from a13n_harness.providers.environment.native.configuration import NamedTargetState, TargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.transfers import CHUNK_BYTES, file_chunks, gzip_file, multipart_file
from a13n_harness.providers.environment.runloop.execution import RunloopExecution
from a13n_harness.providers.environment.runloop.shared import (
    RunloopConnectionConfiguration,
    RunloopEnvironmentConfiguration,
)
from a13n_harness.providers.environment.sprites.execution import SpritesExecution
from a13n_harness.providers.environment.sprites.shared import (
    SpritesConnectionConfiguration,
    SpritesEnvironmentConfiguration,
)
from a13n_harness.providers.environment.vercel.execution import VercelExecution
from a13n_harness.providers.environment.vercel.shared import (
    Session,
    VercelConnectionConfiguration,
    VercelEnvironmentConfiguration,
)

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(sys.platform == "win32", reason="POSIX guest helpers")]
SIZE = 14_106_928


class ResponseStream(httpx2.AsyncByteStream):
    def __init__(self, path, peer):
        self.path, self.peer = path, peer
        self.closed = False
        self.produced = 0

    async def __aiter__(self):
        with self.path.open("rb") as source:
            while chunk := source.read(CHUNK_BYTES):
                self.produced += len(chunk)
                if self.peer.block_read:
                    self.peer.started.set()
                    await asyncio.Event().wait()
                if self.peer.fail_read:
                    raise httpx2.ReadError("isolated failure")
                yield chunk

    async def aclose(self):
        self.closed = True


@pytest.fixture(params=["sprites", "vercel", "runloop"])
async def peer(request, tmp_path):
    kind, root_name = request.param if isinstance(request.param, tuple) else (request.param, "")
    root = tmp_path / root_name
    root.mkdir(exist_ok=True)
    config_type, connection_type, execution_type = {
        "sprites": (SpritesEnvironmentConfiguration, SpritesConnectionConfiguration, SpritesExecution),
        "vercel": (VercelEnvironmentConfiguration, VercelConnectionConfiguration, VercelExecution),
        "runloop": (RunloopEnvironmentConfiguration, RunloopConnectionConfiguration, RunloopExecution),
    }[kind]
    config = config_type(root=str(root), python=sys.executable)
    connection = connection_type(
        **({"team_id": "team", "project_id": "project"} if kind == "vercel" else {"organization": "test"})
    )
    execution = execution_type(config, "env-test", None, NativeRuntime(connection, TokenCredential(api_key="test")))
    state = dict(target_id="target-test", environment_id="env-test", configuration_fingerprint=config.fingerprint)
    execution.target = (
        TargetState(**state) if kind == "runloop" else NamedTargetState(**state, backing_id="backing-test")
    )
    if kind == "vercel":
        execution.session = Session(id="session-test", status="running", timeout=3600)
    peer = SimpleNamespace(
        kind=kind,
        execution=execution,
        calls=[],
        controls=[],
        streams=[],
        max_chunk=0,
        fail_write=False,
        block_write=False,
        fail_read=False,
        block_read=False,
        started=asyncio.Event(),
        status=200,
    )

    async def handler(request):
        peer.calls.append((request.method, request.url, dict(request.headers)))
        route = request.url.path
        if route.endswith(("/fs/read", "/download_file")):
            path = request.url.params.get("path") if kind == "sprites" else json.loads(await request.aread())["path"]
            stream = ResponseStream(Path(path), peer)
            peer.streams.append(stream)
            return httpx2.Response(200, headers={"Content-Type": "application/octet-stream"}, stream=stream)
        assert route.endswith(("/fs/write", "/upload_file"))
        with tempfile.TemporaryFile("w+b") as body:
            async for chunk in request.stream:
                peer.max_chunk = max(peer.max_chunk, len(chunk))
                body.write(chunk)
                if peer.fail_write:
                    raise httpx2.WriteError("isolated uncertain upload")
                if peer.block_write:
                    peer.started.set()
                    await asyncio.Event().wait()
            body.seek(0)
            if kind == "sprites":
                path = Path(request.url.params["path"])
                assert request.url.params["workingDir"] == str(root)
                assert request.headers["content-type"] == "application/octet-stream"
                with path.open("wb") as destination:
                    shutil.copyfileobj(body, destination, CHUNK_BYTES)
            elif kind == "vercel":
                assert request.headers["content-type"] == "application/gzip"
                with tarfile.open(fileobj=body, mode="r:gz") as archive:
                    members = archive.getmembers()
                    assert len(members) == 1 and members[0].isfile()
                    assert not Path(members[0].name).is_absolute()
                    path = Path(request.headers["x-cwd"]) / members[0].name
                    assert path.is_relative_to(root)
                    with archive.extractfile(members[0]) as content, path.open("wb") as destination:
                        shutil.copyfileobj(content, destination, CHUNK_BYTES)
                    path.chmod(members[0].mode)
            else:
                assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
                message = BytesParser(policy=policy.default).parsebytes(
                    ("Content-Type: " + request.headers["content-type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode()
                    + body.read()
                )
                parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
                path = Path(parts["path"].get_payload(decode=True).decode())
                path.write_bytes(parts["file"].get_payload(decode=True))
        return httpx2.Response(peer.status, json={})

    transport = execution.transport()
    await transport.client.aclose()

    class StreamingTransport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return await handler(request)

    transport.client = httpx2.AsyncClient(base_url="https://isolated.test", transport=StreamingTransport())

    async def execute(argv, timeout):
        # Real shipped helper under an unprivileged local uid and deterministic umask.
        peer.controls.append(argv)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import os,sys; os.umask(0o027); os.execv(sys.argv[1],sys.argv[1:])",
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        assert process.returncode == 0, stderr.decode()
        return stdout.decode()

    await execution.open_operations(execute, "target-test", "exec-test")
    peer.files = execution.operations.files
    yield peer
    await execution.close()


def content(size):
    async def chunks():
        block = bytes(range(256)) * 256
        for offset in range(0, size, len(block)):
            yield block[: min(len(block), size - offset)]

    return chunks()


async def digest(stream):
    value = hashlib.sha256()
    async for chunk in stream:
        value.update(chunk)
    return value.hexdigest()


@pytest.mark.parametrize("size", [0, SIZE])
async def test_roundtrip_uses_one_binary_request_each_way(peer, tmp_path, size):
    assert os.geteuid() != 0  # This test's actual helper uid cannot bypass permissions.
    await peer.files.write_bytes_stream("/binary", content(size), mode="create")
    assert await digest(peer.files.read_bytes_stream("/binary")) == await digest(content(size))
    assert len(peer.calls) == 2
    assert len(peer.controls) == 6  # resolve, stage, metadata, restore mode, publish, resolve
    assert peer.max_chunk <= CHUNK_BYTES + 128
    assert all(stream.closed for stream in peer.streams)
    assert stat.S_IMODE((tmp_path / "binary").stat().st_mode) == 0o640
    assert not list(tmp_path.glob(".a13n-*"))
    # File bytes never appear in the argv/control channel.
    assert all(len(arg) < 100_000 for argv in peer.controls for arg in argv)


@pytest.mark.parametrize(
    ("peer", "path"),
    [
        pytest.param(("vercel", "workspace"), "/nested/资料/file.bin", id="unicode-parent"),
        pytest.param(("vercel", "工作区"), "/nested/file.bin", id="unicode-root"),
        pytest.param(("vercel", "工作区"), "/资料/文件.bin", id="unicode-root-and-parent"),
    ],
    indirect=["peer"],
)
async def test_vercel_unicode_paths_roundtrip(peer, path):
    destination = Path(peer.execution.config.root) / path.lstrip("/")
    destination.parent.mkdir(parents=True)
    await peer.files.write_bytes_stream(path, content(256), mode="create")
    assert await peer.files.read_bytes(path) == bytes(range(256))
    assert destination.read_bytes() == bytes(range(256))
    assert len(peer.calls) == 2
    assert stat.S_IMODE(destination.stat().st_mode) == 0o640
    assert not list(destination.parent.glob(".a13n-*"))


@pytest.mark.parametrize("mode", ["create", "replace", "upsert", "append"])
async def test_write_modes_keep_atomic_publication_and_permissions(peer, tmp_path, mode):
    path = tmp_path / "file"
    path.write_bytes(b"old")
    path.chmod(0o755)
    if mode == "create":
        with pytest.raises(EnvironmentError) as caught:
            await peer.files.write_bytes_stream("/file", content(3), mode=mode)
        assert caught.value.code == "environment_conflict"
        assert path.read_bytes() == b"old"
    else:
        await peer.files.write_bytes_stream("/file", content(3), mode=mode)
        assert path.read_bytes() == (b"old" if mode == "append" else b"") + bytes(range(3))
    assert stat.S_IMODE(path.stat().st_mode) == 0o755
    assert not list(tmp_path.glob(".a13n-*"))


@pytest.mark.parametrize("failure", ["transport", "cancel", "server"])
async def test_upload_failure_never_publishes_or_replays_and_cleans_stage(peer, tmp_path, failure):
    (tmp_path / "file").write_bytes(b"winner")
    peer.fail_write = failure == "transport"
    peer.block_write = failure == "cancel"
    peer.status = 500 if failure == "server" else 200
    task = asyncio.create_task(peer.files.write_bytes_stream("/file", content(SIZE), mode="upsert"))
    if failure == "cancel":
        await asyncio.wait_for(peer.started.wait(), 5)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else EnvironmentProviderError):
        await task
    assert len(peer.calls) == 1
    assert (tmp_path / "file").read_bytes() == b"winner"
    assert not list(tmp_path.glob(".a13n-*"))


@pytest.mark.parametrize("failure", ["transport", "cancel", "early_close", "limit"])
async def test_download_closes_without_exec_fallback(peer, tmp_path, failure):
    (tmp_path / "file").write_bytes(b"x" * (CHUNK_BYTES * 3))
    peer.fail_read = failure == "transport"
    peer.block_read = failure == "cancel"
    if failure == "limit":
        peer.execution.commands.configuration = peer.execution.config.model_copy(update={"max_file_bytes": 100})
    if failure == "early_close":
        stream = peer.files.read_bytes_stream("/file")
        await anext(stream)
        assert peer.streams[0].produced == CHUNK_BYTES
        await stream.aclose()
    else:
        task = asyncio.create_task(peer.files.read_bytes("/file"))
        if failure == "cancel":
            await asyncio.wait_for(peer.started.wait(), 5)
            task.cancel()
        with pytest.raises((asyncio.CancelledError, EnvironmentError, EnvironmentProviderError)):
            await task
    assert len(peer.calls) == len(peer.controls) == 1
    assert peer.streams[0].closed


async def test_ranges_use_one_raw_download_and_close_early(peer, tmp_path):
    (tmp_path / "file").write_bytes(bytes(range(256)) * 512)
    assert await peer.files.read_bytes("/file", offset=65530, length=20) == (bytes(range(256)) * 512)[65530:65550]
    assert len(peer.calls) == 1 and len(peer.controls) == 1
    assert peer.streams[0].closed


async def test_path_and_read_permission_checks_precede_privileged_transport(peer, tmp_path):
    with pytest.raises(EnvironmentError):
        await peer.files.read_bytes("/../outside")
    outside = tmp_path.parent / "outside"
    outside.write_bytes(b"secret")
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(EnvironmentError):
        await peer.files.read_bytes("/link")
    (tmp_path / "denied").write_bytes(b"secret")
    (tmp_path / "denied").chmod(0)
    try:
        with pytest.raises(EnvironmentError) as caught:
            await peer.files.read_bytes("/denied")
        assert caught.value.code == "environment_denied"
    finally:
        (tmp_path / "denied").chmod(0o600)
    assert peer.calls == []


async def test_sprites_limit_is_explicit_and_does_not_fallback(peer, tmp_path):
    if peer.kind != "sprites":
        return
    # Exercise the vendor ceiling even when the Host configured a higher limit.
    peer.execution.commands.configuration = peer.execution.config.model_copy(
        update={"max_file_bytes": 32 * 1024 * 1024}
    )
    with pytest.raises(EnvironmentError) as caught:
        await peer.files.write_bytes_stream("/file", content(16 * 1024 * 1024 + 1), mode="create")
    assert caught.value.code == "environment_too_large"
    assert peer.calls == [] and not list(tmp_path.iterdir())


async def test_missing_native_transport_is_unsupported_without_content_exec():
    commands = NativeCommands(AsyncMock(), SpritesEnvironmentConfiguration(), "generation", "exec")
    with pytest.raises(EnvironmentError) as caught:
        await commands.write_stream("/candidate", BytesIO(b"secret"))
    assert caught.value.code == "environment_unsupported"
    commands.execute.assert_not_awaited()


@pytest.mark.parametrize("kind", ["raw", "gzip", "multipart"])
async def test_request_body_memory_and_backpressure_are_bounded(kind):
    class Source:
        def __init__(self):
            self.position = 0
            self.reads = 0

        def tell(self):
            return self.position

        def seek(self, offset, whence=0):
            self.position = SIZE if whence == 2 else offset
            return self.position

        def read(self, size):
            assert 0 < size <= CHUNK_BYTES
            self.reads += 1
            count = min(size, SIZE - self.position)
            self.position += count
            return os.urandom(count)

    source = Source()
    stream = (
        file_chunks(source)
        if kind == "raw"
        else gzip_file("/candidate", source, 0o640)
        if kind == "gzip"
        else multipart_file("/candidate", source)[1]
    )
    assert source.reads == 0
    tracemalloc.start()
    try:
        async for chunk in stream:
            assert len(chunk) <= CHUNK_BYTES + 128
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert source.position == SIZE
    assert peak < 2 * 1024 * 1024


@pytest.mark.parametrize("status", [405, 501])
async def test_missing_provider_file_channel_is_explicit_without_replay(peer, tmp_path, status):
    peer.status = status
    with pytest.raises(EnvironmentProviderError) as caught:
        await peer.files.write_bytes_stream("/file", content(3), mode="create")
    assert caught.value.category.value == "unsupported"
    assert len(peer.calls) == 1
    assert not list(tmp_path.iterdir())
