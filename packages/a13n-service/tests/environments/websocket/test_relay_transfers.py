from __future__ import annotations

import asyncio
import base64

import pytest
from a13n_environment.direct_local.files import LocalFileOperator
from a13n_environment.direct_local.provider import _DirectLocalFilePolicy
from a13n_environment.models import EnvironmentAction
from a13n_service.environments.websocket.relay_file_operations import RelayFileOperations
from a13n_service.environments.websocket.relay_files import FileRelayDispatch
from a13n_service.environments.websocket.relay_protocol import RelayChunk
from a13n_service.environments.websocket.relay_storage import RelayStoreError
from a13n_service.environments.websocket.relay_waiters import RelayOperationError
from a13n_service.ids import new_object_id

pytestmark = pytest.mark.anyio


@pytest.fixture
def files(tmp_path):
    return LocalFileOperator(
        root=tmp_path,
        policy=_DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="mount",
        generation="generation",
    )


@pytest.mark.parametrize("size", [0, 1, 65_536, 65_537, 4 * 1024 * 1024 + 11])
async def test_binary_transfer_exceeds_window_without_accumulating_ledger_entries(
    control_relay, files, tmp_path, relay_redis, size
):
    data = (bytes(range(256)) * ((size + 255) // 256))[:size]

    async def source():
        yield data

    async with control_relay(FileRelayDispatch(files, frozenset(EnvironmentAction))) as (client, owner, _, _):
        worker = RelayFileOperations(client)
        written = await worker.write_bytes_stream("/binary", source(), mode="create")
        assert written.bytes_written == size and written.receipt.stage == "completed"
        assert (tmp_path / "binary").read_bytes() == data
        assert await worker.read_bytes("/binary") == data
        assert await worker.read_bytes("/binary", offset=7, length=65_539) == data[7:65_546]
        streamed = [chunk async for chunk in worker.read_bytes_stream("/binary", chunk_size=997)]
        assert all(0 < len(chunk) <= 997 for chunk in streamed)
        assert b"".join(streamed) == data
        assert int(await relay_redis.hget(owner.ledger_key, "_count")) == 4
        assert int(await relay_redis.hget(owner.ledger_key, "_bytes")) < 20_000


async def test_upload_lost_input_reply_retries_same_frame_without_duplicate_bytes(
    control_relay, files, tmp_path, monkeypatch
):
    async with control_relay(FileRelayDispatch(files, frozenset(EnvironmentAction))) as (client, owner, _, _):
        original = owner.send_input
        lost = False

        async def lose(request, frame):
            nonlocal lost
            result = await original(request, frame)
            if isinstance(frame, RelayChunk) and not lost:
                lost = True
                raise RelayStoreError("relay_unavailable")
            return result

        monkeypatch.setattr(owner, "send_input", lose)

        async def source():
            yield b"once\x00\xff"

        result = await RelayFileOperations(client).write_bytes_stream("/once", source(), mode="create")
        assert result.bytes_written == 6
        assert (tmp_path / "once").read_bytes() == b"once\x00\xff"


async def test_missing_upload_finish_is_not_success(control_relay, files, tmp_path):
    async with control_relay(FileRelayDispatch(files, frozenset(EnvironmentAction))) as (client, _, _, _):
        transfer_id = new_object_id("etr")
        with pytest.raises(RelayOperationError) as error:
            async with client.request(
                "file.write_bytes",
                {"transfer_id": transfer_id, "path": "/unfinished", "mode": "create"},
                streaming="upload",
                timeout_seconds=0.2,
            ) as pending:
                window = await pending.upload_slot()
                await client.send_input(
                    pending,
                    RelayChunk(
                        request_id=pending.request.request_id,
                        scope=pending.request.scope,
                        transfer=window.sent(1),
                        data=base64.b64encode(b"a").decode(),
                    ),
                )
                await pending.result()
        assert error.value.code in {"environment_timeout", "environment_cancelled"}
        assert error.value.failure.certainty == "unknown"


async def test_slow_download_does_not_block_other_operations(control_relay, files, tmp_path):
    (tmp_path / "large").write_bytes(b"x" * 2 * 1024 * 1024)
    async with control_relay(FileRelayDispatch(files, frozenset(EnvironmentAction))) as (client, _, consumer, _):
        stream = RelayFileOperations(client).read_bytes_stream("/large")
        assert len(await anext(stream)) == 65_536
        await asyncio.sleep(0.05)
        async with asyncio.timeout(1):
            assert (await RelayFileOperations(client).stat("/large")).size == 2 * 1024 * 1024
        await stream.aclose()
        async with asyncio.timeout(1):
            while consumer._executing:
                await asyncio.sleep(0.01)


async def test_upload_buffers_initial_window_while_start_is_pending(control_relay, files, tmp_path, monkeypatch):
    async with control_relay(FileRelayDispatch(files, frozenset(EnvironmentAction))) as (client, owner, consumer, _):
        start = owner.start
        release = asyncio.Event()
        completions = []
        complete = owner.complete

        async def delayed_start(request, entry):
            if request.operation == "file.write_bytes":
                await release.wait()
            return await start(request, entry)

        async def record_completion(request, entry, terminal):
            completions.append(terminal)
            return await complete(request, entry, terminal)

        monkeypatch.setattr(owner, "start", delayed_start)
        monkeypatch.setattr(owner, "complete", record_completion)
        data = bytes(range(256)) * (owner.limits.chunk_bytes // 256) * (owner.limits.input_window + 2)

        async def source():
            yield data

        async with asyncio.TaskGroup() as tasks:
            written = tasks.create_task(
                RelayFileOperations(client).write_bytes_stream("/delayed", source(), mode="create")
            )
            async with asyncio.timeout(2):
                while (
                    not consumer._executing
                    or len(next(iter(consumer._executing.values())).inputs) < owner.limits.input_window
                ):
                    await asyncio.sleep(0)
            assert not (tmp_path / "delayed").exists()
            release.set()
        assert written.result().bytes_written == len(data)
        assert (tmp_path / "delayed").read_bytes() == data
        assert len(completions) == 1
        assert completions[0].error is None
        assert await owner.read(pending=True) == ()
