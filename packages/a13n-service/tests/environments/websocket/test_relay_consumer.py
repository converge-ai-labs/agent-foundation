from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from time import monotonic

import pytest
from a13n_environment.direct_local.files import LocalFileOperator
from a13n_environment.direct_local.provider import _DirectLocalFilePolicy
from a13n_environment.models import EnvironmentAction
from a13n_service.environments.websocket.authority import ConnectionIdentity, DispatchAuthority, UseIdentity
from a13n_service.environments.websocket.coordination import ConfirmedObservation, ConnectionObservation, UseGrant
from a13n_service.environments.websocket.relay_client import RelayUseClient
from a13n_service.environments.websocket.relay_consumer import RelayControlConsumer
from a13n_service.environments.websocket.relay_files import FileRelayDispatch
from a13n_service.environments.websocket.relay_storage import (
    ConnectionRelayStore,
    RelayStoreError,
    WorkerResponseMailbox,
)
from a13n_service.environments.websocket.relay_waiters import RelayOperationError, RelayResponseDispatcher

pytestmark = pytest.mark.anyio
CONNECTION = ConnectionIdentity("org", "env", "connection", "epoch", "control")
USE = UseIdentity(CONNECTION, "use", "run", "attempt", 1, "worker")


@pytest.fixture
def files(tmp_path):
    return LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=4096),
        mount_id="mount",
        generation="generation",
    )


@pytest.fixture
async def relay(relay_redis, files):
    owner = ConnectionRelayStore(relay_redis, CONNECTION)
    mailbox = WorkerResponseMailbox(relay_redis, USE.worker_instance_id)
    await mailbox.prepare()
    await owner.prepare()
    started = monotonic()
    seconds, micros = await relay_redis.time()
    now = seconds * 1000 + micros // 1000
    observed = ConfirmedObservation(
        ConnectionObservation(
            code="ok",
            now_ms=now,
            status="online",
            connection=CONNECTION,
            expires_at_ms=now + 5000,
            barrier_ms=0,
            retiring=None,
            use=UseGrant(identity=USE, expires_at_ms=now + 5000),
            error=None,
        ),
        started,
        0.005,
    )
    responses = RelayResponseDispatcher(mailbox)
    client = RelayUseClient(USE, observed, owner, responses, check_authority=lambda: None)
    reader = asyncio.create_task(responses.run())

    @asynccontextmanager
    async def serving(*, permissions=frozenset(EnvironmentAction), concurrency=32, dispatch=None):
        authority = DispatchAuthority(USE, observed.deadline(use=True))
        consumer = RelayControlConsumer(
            owner,
            authority,
            observed,
            dispatch or FileRelayDispatch(files, permissions),
            concurrency=concurrency,
        )
        task = asyncio.create_task(consumer.run())
        try:
            yield client, owner, consumer, task
        finally:
            await consumer.close()
            await task

    try:
        yield serving
    finally:
        await client.invalidate()
        responses.close()
        await reader


async def test_file_mutations_and_observations_use_real_streams_and_provider(relay, tmp_path):
    async with relay() as (client, owner, _, _):
        written = await client.call(
            "file.write_text", {"path": "/note.txt", "text": "hello\nworld\n", "mode": "create"}
        )
        assert written["bytes_written"] == 12
        assert written["receipt"]["stage"] == "completed"
        read = await client.call("file.read_text", {"path": "/note.txt", "line_offset": 1})
        assert read["text"] == "world\n"
        await client.call("file.mkdir", {"path": "/sub"})
        await client.call("file.copy", {"source": "/note.txt", "destination": "/sub/copy.txt"})
        await client.call("file.move", {"source": "/sub/copy.txt", "destination": "/sub/moved.txt"})
        entries = await client.call("file.list", {"path": "/sub", "max_results": 10})
        assert [entry["path"] for entry in entries["entries"]] == ["/sub/moved.txt"]
        await client.call("file.remove", {"path": "/sub", "recursive": True})
        assert (tmp_path / "note.txt").read_text() == "hello\nworld\n"
        assert not (tmp_path / "sub").exists()
        assert await owner.read(pending=True) == ()


@pytest.mark.parametrize(
    ("operation", "payload", "code"),
    [
        ("file.write_text", {"path": "/denied", "text": "no", "mode": "create"}, "environment_forbidden"),
        ("file.stat", {"path": "/none", "operation": "file.write_text"}, "environment_request_invalid"),
        ("file.stat", {"path": "/none", "native_endpoint": "secret"}, "environment_request_invalid"),
        ("file.remove", {"path": "/none"}, "environment_forbidden"),
        ("file.stat", {"path": "/none"}, "environment_not_found"),
    ],
)
async def test_invalid_and_forbidden_calls_have_no_effect_and_preserve_provider_errors(
    relay, tmp_path, operation, payload, code
):
    async with relay(permissions=frozenset({EnvironmentAction.FILE_STAT})) as (client, owner, _, _):
        with pytest.raises(RelayOperationError) as error:
            await client.call(operation, payload)
        assert error.value.code == code
        assert not list(tmp_path.iterdir())
        assert await owner.read(pending=True) == ()


async def test_lost_completion_reply_never_repeats_file_append(relay, monkeypatch, tmp_path):
    (tmp_path / "append").write_text("")
    async with relay() as (client, owner, _, _):
        complete = owner.complete
        seen = []

        async def lost_reply(request, entry, terminal):
            seen.append(terminal)
            result = await complete(request, entry, terminal)
            if len(seen) == 1:
                raise RelayStoreError("relay_unavailable")
            return result

        monkeypatch.setattr(owner, "complete", lost_reply)
        await client.call("file.write_text", {"path": "/append", "text": "once", "mode": "append"})
        async with asyncio.timeout(1):
            while len(seen) < 2:
                await asyncio.sleep(0)
        assert seen[0] == seen[1]
        assert (tmp_path / "append").read_text() == "once"


async def test_foreign_attempt_cannot_execute_on_current_use(relay, monkeypatch, tmp_path):
    async with relay() as (client, owner, _, _):
        append = owner.append

        async def forged(message):
            return await append(message.model_copy(update={"use": replace(USE, attempt_fence=2)}))

        monkeypatch.setattr(owner, "append", forged)
        # The forged response cannot satisfy the original caller's exact tuple.
        with pytest.raises(RelayOperationError):
            await client.call(
                "file.write_text", {"path": "/forged", "text": "no", "mode": "create"}, timeout_seconds=0.1
            )
        assert not list(tmp_path.iterdir())


async def test_saturated_operations_leave_cancellation_capacity(relay):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class Blocked:
        def prepare(self, operation, payload):
            async def execute():
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            return execute

    async with relay(concurrency=1, dispatch=Blocked()) as (client, _, _, _):
        async with client.request("file.stat", {"path": "/blocked"}) as pending:
            await entered.wait()
            with pytest.raises(RelayOperationError) as error:
                await client.call("file.stat", {"path": "/other"})
            assert error.value.failure.certainty == "not_dispatched"
            assert error.value.code == "environment_overloaded"
            assert await client.call("operation.cancel", {"request_id": pending.request.request_id}) == {
                "accepted": True
            }
            await cancelled.wait()
            with pytest.raises(RelayOperationError) as error:
                await pending.result()
            assert error.value.failure.certainty == "unknown"
            assert error.value.code == "environment_cancelled"


async def test_scope_close_publishes_terminal_before_ending_consumer(relay):
    async with relay() as (client, owner, _, task):
        assert await client.call("scope.close") is None
        await task
        assert await owner.read(pending=True) == ()


async def test_lost_read_reply_recovers_owned_pending_without_reexecuting(relay, monkeypatch, tmp_path):
    (tmp_path / "append").write_text("")
    async with relay() as (client, owner, _, _):
        read = owner.read
        lost = False

        async def lose_once(**kwargs):
            nonlocal lost
            rows = await read(**kwargs)
            if rows and not lost:
                lost = True
                raise RelayStoreError("relay_unavailable")
            return rows

        monkeypatch.setattr(owner, "read", lose_once)
        await client.call("file.write_text", {"path": "/append", "text": "once", "mode": "append"})
        assert lost
        assert (tmp_path / "append").read_text() == "once"


async def test_lost_dispatch_evidence_reply_fences_scope_without_effects(relay, monkeypatch, tmp_path):
    with pytest.raises(ExceptionGroup) as error:
        async with relay() as (client, owner, _, task):
            start = owner.start

            async def lose_reply(request, entry):
                await start(request, entry)
                raise RelayStoreError("relay_unavailable")

            monkeypatch.setattr(owner, "start", lose_reply)
            async with client.request("file.write_text", {"path": "/none", "text": "no", "mode": "create"}):
                await task
    assert isinstance(error.value.exceptions[0], RelayStoreError)
    assert not list(tmp_path.iterdir())
