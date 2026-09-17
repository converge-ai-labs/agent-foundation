from __future__ import annotations

import asyncio
from dataclasses import replace
from time import monotonic

import pytest
from a13n_service.environments.websocket.authority import ConnectionIdentity, DispatchDenied, UseIdentity
from a13n_service.environments.websocket.coordination import ConfirmedObservation, ConnectionObservation, UseGrant
from a13n_service.environments.websocket.relay_client import RelayUseClient
from a13n_service.environments.websocket.relay_protocol import RelayTerminal
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
async def relay(relay_redis):
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
    try:
        yield client, owner, responses
    finally:
        await client.invalidate()
        responses.close()
        await reader


async def reply_once(owner):
    while not (rows := await owner.read()):
        pass
    entry, request = rows[0]
    assert (await owner.start(request, entry)).phase == "started"
    await owner.complete(
        request, entry, RelayTerminal(request_id=request.request_id, use=request.use, result=request.payload)
    )
    return request


async def test_fast_terminal_response_before_append_return_is_correlated(relay, monkeypatch):
    client, owner, responses = relay
    delivered = asyncio.Event()
    accept = responses.accept

    def accept_and_signal(frame):
        accept(frame)
        delivered.set()

    append = owner.append

    async def delayed_return(message):
        evidence = await append(message)
        await delivered.wait()
        return evidence

    monkeypatch.setattr(responses, "accept", accept_and_signal)
    monkeypatch.setattr(owner, "append", delayed_return)
    server = asyncio.create_task(reply_once(owner))
    assert await client.call("file.stat", {"value": "first"}, timeout_seconds=1) == {"value": "first"}
    assert (await server).payload == {"value": "first"}


async def test_lost_append_reply_reuses_exact_request_without_executing_twice(relay, monkeypatch):
    client, owner, _ = relay
    append = owner.append
    published = []

    async def lose_first_reply(message):
        published.append(message)
        evidence = await append(message)
        if len(published) == 1:
            raise RelayStoreError("relay_unavailable")
        return evidence

    monkeypatch.setattr(owner, "append", lose_first_reply)
    server = asyncio.create_task(reply_once(owner))
    assert await client.call("file.stat", {"value": "same"}, timeout_seconds=1) == {"value": "same"}
    request = await server
    assert len(published) == 2 and published[0] == published[1] == request
    assert await owner.read(pending=True) == ()


async def test_known_append_rejection_does_not_consume_cancellation_capacity(relay, monkeypatch):
    client, owner, _ = relay
    operations = []

    async def reject(message):
        operations.append(message.operation)
        raise RelayStoreError("relay_overloaded")

    monkeypatch.setattr(owner, "append", reject)
    with pytest.raises(RelayOperationError) as error:
        await client.call("file.stat", timeout_seconds=1)
    assert error.value.failure.certainty == "not_dispatched"
    assert operations == ["file.stat"]


@pytest.mark.parametrize("exit_early", [False, True])
async def test_cancellation_and_early_stream_exit_send_bounded_control_request(relay, exit_early):
    client, owner, responses = relay
    started = asyncio.Event()
    original = None

    async def server():
        nonlocal original
        while True:
            for entry, message in await owner.read():
                await owner.start(message, entry)
                if message.operation == "operation.cancel":
                    assert original is not None
                    assert message.payload == {"request_id": original.request_id}
                    await owner.complete(message, entry, RelayTerminal(request_id=message.request_id, use=USE))
                    return
                original = message
                started.set()

    serving = asyncio.create_task(server())
    if exit_early:
        async with client.request("file.read_bytes_stream", {}, timeout_seconds=2, streaming="download"):
            await started.wait()
    else:
        task = asyncio.create_task(client.call("file.stat", timeout_seconds=2))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.wait_for(serving, 1)
    assert not responses._pending


async def test_scope_close_is_idempotent_and_fences_later_publication(relay):
    client, owner, _ = relay
    serving = asyncio.create_task(reply_once(owner))
    await client.close()
    assert (await serving).operation == "scope.close"
    await client.close()
    with pytest.raises(DispatchDenied):
        await client.call("file.stat")


async def test_takeover_observation_cannot_reuse_retained_old_use(relay):
    client, owner, responses = relay
    now = client._server_ms
    takeover = ConfirmedObservation(
        ConnectionObservation(
            code="ok",
            now_ms=now,
            status="connecting",
            connection=replace(CONNECTION, connection_id="candidate"),
            expires_at_ms=now + 5000,
            barrier_ms=now + 5000,
            retiring=None,
            use=UseGrant(identity=USE, expires_at_ms=now + 5000),
            error=None,
        ),
        monotonic(),
        0.005,
    )
    with pytest.raises(ValueError, match="exact confirmed use"):
        RelayUseClient(USE, takeover, owner, responses, check_authority=lambda: None)
    with pytest.raises(DispatchDenied):
        await client.renew(takeover)
    with pytest.raises(DispatchDenied):
        await client.call("file.stat")
