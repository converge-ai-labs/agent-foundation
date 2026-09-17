from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from time import monotonic

import pytest
from a13n_service.environments.websocket.authority import (
    ConnectionIdentity,
    DispatchAuthority,
    LeaseDeadline,
    UseIdentity,
)
from a13n_service.environments.websocket.relay_capability import validate_relay_backend
from a13n_service.environments.websocket.relay_protocol import (
    RelayChunk,
    RelayFailure,
    RelayLimits,
    RelayRequest,
    RelayTerminal,
    TransferPosition,
    canonical_message,
)
from a13n_service.environments.websocket.relay_storage import (
    ConnectionRelayStore,
    RelayStoreError,
    WorkerResponseMailbox,
)
from a13n_service.environments.websocket.relay_waiters import RelayResponseDispatcher
from a13n_service.ids import new_object_id
from a13n_service.storage.config import RedisMemoryConfig, RedisServerConfig
from a13n_service.storage.redis import open_redis
from redis.crc import key_slot
from redis.exceptions import TimeoutError as RedisTimeoutError

pytestmark = pytest.mark.anyio
CONNECTION = ConnectionIdentity("org_test", "env_test", "connection", "epoch", "control")
USE = UseIdentity(CONNECTION, "use", "run", "attempt", 2**60 + 1, "worker")


async def test_startup_probe_verifies_real_backend_and_cleans_its_keys(relay_redis):
    await validate_relay_backend(relay_redis)
    assert await relay_redis.keys("a13n:{environment-relay}:*") == []


async def test_unsupported_stream_scripting_is_rejected_before_serving():
    async with open_redis(RedisMemoryConfig()) as client:
        with pytest.raises(ValueError, match="atomic Stream scripting support"):
            await validate_relay_backend(client)
        assert await client.keys("a13n:{environment-relay}:*") == []


async def test_blocking_reader_cannot_exhaust_request_publication_pool(relay_redis, redis_url):
    config = RedisServerConfig(url=redis_url, max_connections=1)
    async with open_redis(config) as commands, open_redis(config) as reader:
        await commands.client_setname("relay-publication")
        await reader.client_setname("relay-reader")
        owner = ConnectionRelayStore(commands, CONNECTION, reader=reader)
        await WorkerResponseMailbox(commands, USE.worker_instance_id).prepare()
        await owner.prepare()
        seconds, micros = await commands.time()
        request = RelayRequest(
            request_id=new_object_id("erq"),
            use=USE,
            operation="file.stat",
            deadline_ms=seconds * 1000 + micros // 1000 + 10_000,
        )
        reading = asyncio.create_task(owner.read())
        try:
            async with asyncio.timeout(1):
                while not any(
                    client["name"].startswith("relay-") and client["cmd"] == "xreadgroup"
                    for client in await relay_redis.client_list()
                ):
                    assert not reading.done()
                    await asyncio.sleep(0)
            publication = await owner.append(request)
            assert await reading == ((publication.entry_id, request),)
        finally:
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)


def test_terminal_has_one_outcome_and_envelopes_reject_unknown_versions():
    with pytest.raises(ValueError, match="both a result and an error"):
        RelayTerminal(
            request_id=new_object_id("erq"),
            use=USE,
            result="success",
            error=RelayFailure(code="environment_unknown_outcome", certainty="unknown"),
        )
    with pytest.raises(ValueError):
        RelayRequest(version=2, request_id=new_object_id("erq"), use=USE, operation="file.stat", deadline_ms=1)


@pytest.fixture
async def stores(relay_redis):
    owner = ConnectionRelayStore(relay_redis, CONNECTION)
    worker = WorkerResponseMailbox(relay_redis, USE.worker_instance_id)
    await worker.prepare()
    await owner.prepare()
    return owner, worker


@pytest.fixture
async def request_message(relay_redis):
    seconds, micros = await relay_redis.time()
    return RelayRequest(
        request_id=new_object_id("erq"),
        use=USE,
        operation="file.read_text",
        deadline_ms=seconds * 1000 + micros // 1000 + 50_000,
        payload={"path": "/workspace/file"},
    )


async def pending(owner, request):
    receipt = await owner.append(request)
    rows = await owner.read()
    assert rows == ((receipt.entry_id, request),)
    return receipt.entry_id


def terminal(request, **values):
    return RelayTerminal(request_id=request.request_id, use=request.use, result={"text": "hello"}, **values)


async def test_terminal_response_evidence_and_ack_are_atomic(stores, request_message, relay_redis):
    owner, worker = stores
    entry = await pending(owner, request_message)
    assert (await owner.start(request_message, entry)).phase == "started"
    assert (await owner.start(request_message, entry)).phase == "inflight"
    result = terminal(request_message)
    evidence = await owner.complete(request_message, entry, result)
    assert evidence.terminal() == result
    assert (await relay_redis.xpending(owner.requests_key, "owner"))["pending"] == 0
    assert await relay_redis.xlen(owner.requests_key) == 0
    frames = await worker.read()
    assert len(frames) == 1 and frames[0][1] == result
    await worker.acknowledge(frames[0][0])
    assert await relay_redis.xlen(worker.key) == 0
    assert (await owner.complete(request_message, entry, result)).terminal() == result
    assert await relay_redis.xlen(worker.key) == 0


async def test_identical_append_retries_preserve_entry_and_changed_payload_conflicts(stores, request_message):
    owner, _ = stores
    first = await owner.append(request_message)
    assert await owner.append(request_message) == first
    changed = request_message.model_copy(update={"payload": {"path": "/other"}})
    with pytest.raises(RelayStoreError) as error:
        await owner.append(changed)
    assert error.value.code == "request_conflict"


async def test_lost_completion_reply_retries_without_another_terminal(
    stores, request_message, relay_redis, monkeypatch
):
    owner, worker = stores
    entry = await pending(owner, request_message)
    await owner.start(request_message, entry)
    original = owner._storage._script

    async def lose_reply(**kwargs):
        await original(**kwargs)
        raise RedisTimeoutError("lost reply")

    result = terminal(request_message)
    monkeypatch.setattr(owner._storage, "_script", lose_reply)
    with pytest.raises(RelayStoreError):
        await owner.complete(request_message, entry, result)
    monkeypatch.setattr(owner._storage, "_script", original)
    assert (await owner.complete(request_message, entry, result)).phase == "completed"
    assert await relay_redis.xlen(worker.key) == 1


async def test_redelivered_completed_payload_is_acknowledged_without_redispatch(stores, request_message, relay_redis):
    owner, worker = stores
    entry = await pending(owner, request_message)
    await owner.start(request_message, entry)
    result = terminal(request_message)
    await owner.complete(request_message, entry, result)
    duplicate = await relay_redis.xadd(
        owner.requests_key,
        {"request_id": request_message.request_id, "request": canonical_message(request_message, max_bytes=65_536)},
    )
    rows = await owner.read()
    assert rows[0][0] == duplicate.decode()
    observed = await owner.start(request_message, rows[0][0])
    assert observed.phase == "completed" and observed.terminal() == result
    await owner.complete(request_message, rows[0][0], observed.terminal())
    assert await relay_redis.xlen(worker.key) == 1
    assert (await relay_redis.xpending(owner.requests_key, "owner"))["pending"] == 0


async def test_partial_response_before_evidence_is_completed_without_early_ack(stores, request_message, relay_redis):
    owner, worker = stores
    entry = await pending(owner, request_message)
    await owner.start(request_message, entry)
    result = terminal(request_message)
    await relay_redis.xadd(worker.key, {"frame": canonical_message(result, max_bytes=262_144)})
    assert (await relay_redis.xpending(owner.requests_key, "owner"))["pending"] == 1
    await owner.complete(request_message, entry, result)
    frames = await worker.read()
    assert len(frames) == 2 and all(frame == result for _, frame in frames)


@pytest.mark.parametrize("loss", ["ledger", "record", "request_stream", "expiry_index", "server_epoch"])
async def test_lost_evidence_cannot_bootstrap_or_replay_an_existing_scope(stores, request_message, relay_redis, loss):
    owner, _ = stores
    entry = await pending(owner, request_message)
    await owner.start(request_message, entry)
    if loss == "record":
        await relay_redis.hdel(owner.ledger_key, request_message.request_id)
    elif loss == "server_epoch":
        await relay_redis.hset(owner.ledger_key, "_server", "previous-server")
    else:
        await relay_redis.delete(
            {"ledger": owner.ledger_key, "request_stream": owner.requests_key, "expiry_index": owner.expiries_key}[loss]
        )
    with pytest.raises(RelayStoreError) as error:
        await owner.append(request_message)
    assert error.value.code == "scope_lost"


async def test_wrong_response_key_type_rejects_before_completion_or_ack(stores, request_message, relay_redis):
    owner, worker = stores
    entry = await pending(owner, request_message)
    await owner.start(request_message, entry)
    await relay_redis.delete(worker.key)
    await relay_redis.set(worker.key, "wrong-type")
    with pytest.raises(RelayStoreError) as error:
        await owner.complete(request_message, entry, terminal(request_message))
    assert error.value.code == "response_scope_lost"
    assert (await relay_redis.xpending(owner.requests_key, "owner"))["pending"] == 1


async def test_altered_result_conflicts_without_publishing_or_acknowledging_more(stores, request_message, relay_redis):
    owner, worker = stores
    entry = await pending(owner, request_message)
    await owner.start(request_message, entry)
    await owner.complete(request_message, entry, terminal(request_message))
    with pytest.raises(RelayStoreError) as error:
        await owner.complete(request_message, entry, terminal(request_message).model_copy(update={"result": "changed"}))
    assert error.value.code == "result_conflict"
    assert await relay_redis.xlen(worker.key) == 1


async def test_queued_request_requires_known_pre_dispatch_rejection(stores, request_message):
    owner, _ = stores
    entry = await pending(owner, request_message)
    with pytest.raises(RelayStoreError) as error:
        await owner.complete(request_message, entry, terminal(request_message))
    assert error.value.code == "request_not_started"
    rejected = RelayTerminal(
        request_id=request_message.request_id,
        use=USE,
        error=RelayFailure(code="environment_forbidden", certainty="not_dispatched"),
    )
    assert (await owner.complete(request_message, entry, rejected)).terminal() == rejected


async def test_chunk_order_and_terminal_offset_are_enforced(stores, request_message):
    owner, worker = stores
    entry = await pending(owner, request_message)
    await owner.start(request_message, entry)
    transfer_id = new_object_id("etr")
    frame = RelayChunk(
        request_id=request_message.request_id,
        use=USE,
        transfer=TransferPosition(transfer_id=transfer_id, sequence=0, offset=0),
        data=base64.b64encode(b"hello").decode(),
    )
    await owner.chunk(request_message, entry, frame)
    with pytest.raises(RelayStoreError) as error:
        await owner.chunk(request_message, entry, frame)
    assert error.value.code == "transfer_conflict"
    with pytest.raises(RelayStoreError) as error:
        await owner.complete(request_message, entry, terminal(request_message))
    assert error.value.code == "transfer_conflict"
    end = terminal(request_message, transfer=TransferPosition(transfer_id=transfer_id, sequence=1, offset=5))
    await owner.complete(request_message, entry, end)
    assert [item for _, item in await worker.read()] == [frame, end]


async def test_scopes_share_one_cluster_slot_and_expire_after_owner_loss(stores, relay_redis):
    owner, worker = stores
    keys = (owner.requests_key, owner.ledger_key, owner.expiries_key, worker.key)
    assert len({key_slot(key.encode()) for key in keys}) == 1
    assert all([0 < await relay_redis.pttl(key) <= 130_000 for key in keys])


async def test_full_bigint_attempt_fence_survives_stream_roundtrip(stores, request_message):
    owner, _ = stores
    await owner.append(request_message)
    decoded = (await owner.read())[0][1]
    assert decoded.use.attempt_fence == 2**60 + 1
    other = request_message.model_copy(update={"use": replace(USE, attempt_fence=2**60 + 2)})
    with pytest.raises(RelayStoreError) as error:
        await owner.append(other)
    assert error.value.code == "request_conflict"


async def test_response_capacity_reserves_room_for_terminal_frames(relay_redis, request_message):
    limits = RelayLimits(response_frames=2, terminal_reserve=1)
    owner = ConnectionRelayStore(relay_redis, CONNECTION, limits=limits)
    worker = WorkerResponseMailbox(relay_redis, USE.worker_instance_id, limits=limits)
    await worker.prepare()
    await owner.prepare()
    entry = await pending(owner, request_message)
    await owner.start(request_message, entry)
    transfer = TransferPosition(transfer_id=new_object_id("etr"), sequence=0, offset=0)
    frame = RelayChunk(request_id=request_message.request_id, use=USE, transfer=transfer, data="aA==")
    await owner.chunk(request_message, entry, frame)
    with pytest.raises(RelayStoreError) as error:
        await owner.chunk(
            request_message,
            entry,
            frame.model_copy(update={"transfer": transfer.model_copy(update={"sequence": 1, "offset": 1})}),
        )
    assert error.value.code == "relay_overloaded"
    await owner.complete(
        request_message,
        entry,
        terminal(request_message, transfer=transfer.model_copy(update={"sequence": 1, "offset": 1})),
    )
    assert await relay_redis.xlen(worker.key) == 2


async def test_full_operation_ledger_keeps_independent_cancellation_capacity(relay_redis, request_message):
    limits = RelayLimits(retained_requests=17)
    owner = ConnectionRelayStore(relay_redis, CONNECTION, limits=limits)
    worker = WorkerResponseMailbox(relay_redis, USE.worker_instance_id, limits=limits)
    await worker.prepare()
    await owner.prepare()
    await owner.append(request_message)
    second = request_message.model_copy(update={"request_id": new_object_id("erq")})
    with pytest.raises(RelayStoreError) as error:
        await owner.append(second)
    assert error.value.code == "relay_overloaded"
    cancel = request_message.model_copy(
        update={
            "request_id": new_object_id("erq"),
            "operation": "operation.cancel",
            "payload": {"request_id": request_message.request_id},
        }
    )
    assert (await owner.append(cancel)).phase == "queued"


async def test_expired_evidence_is_pruned_and_expired_append_is_never_replayed(relay_redis, request_message):
    limits = RelayLimits(evidence_ms=1)
    owner = ConnectionRelayStore(relay_redis, CONNECTION, limits=limits)
    worker = WorkerResponseMailbox(relay_redis, USE.worker_instance_id, limits=limits)
    await worker.prepare()
    await owner.prepare()
    seconds, micros = await relay_redis.time()
    request = request_message.model_copy(update={"deadline_ms": seconds * 1000 + micros // 1000 + 250})
    entry = await pending(owner, request)
    await owner.start(request, entry)
    await owner.complete(request, entry, terminal(request))
    await asyncio.sleep(0.27)
    await owner.prune()
    assert await relay_redis.hget(owner.ledger_key, request.request_id) is None
    assert await relay_redis.hmget(owner.ledger_key, "_count", "_bytes") == [b"0", b"0"]
    with pytest.raises(RelayStoreError) as error:
        await owner.append(request)
    assert error.value.code == "request_expired"


async def test_foreign_connection_and_wrong_consumer_cannot_start_dispatch(stores, request_message, relay_redis):
    owner, _ = stores
    entry = (await owner.append(request_message)).entry_id
    await relay_redis.xreadgroup("owner", "other-control", {owner.requests_key: ">"}, count=1)
    with pytest.raises(RelayStoreError) as error:
        await owner.start(request_message, entry)
    assert error.value.code == "request_not_owned"
    foreign = request_message.model_copy(
        update={"use": replace(USE, connection=replace(CONNECTION, connection_epoch="new"))}
    )
    with pytest.raises(RelayStoreError) as error:
        await owner.append(foreign)
    assert error.value.code == "scope_lost"


async def test_response_dispatch_recovers_a_lost_read_reply_without_losing_waiter(stores, request_message, monkeypatch):
    owner, worker = stores
    dispatcher = RelayResponseDispatcher(worker)
    original = worker.read
    lost = False

    async def lose_first_batch(**kwargs):
        nonlocal lost
        frames = await original(**kwargs)
        if frames and not lost:
            lost = True
            raise RelayStoreError("relay_unavailable")
        return frames

    monkeypatch.setattr(worker, "read", lose_first_batch)
    reader = asyncio.create_task(dispatcher.run())
    grant = DispatchAuthority(USE, LeaseDeadline(monotonic() + 5))
    try:
        with dispatcher.register(request_message, grant, LeaseDeadline(monotonic() + 2)) as waiting:
            waiting.begin_publication()
            entry = await pending(owner, request_message)
            await owner.start(request_message, entry)
            result = terminal(request_message)
            await owner.complete(request_message, entry, result)
            assert await waiting.result() == result
            assert lost
    finally:
        dispatcher.close()
        await reader


async def test_upload_input_window_ack_and_finish_preserve_one_terminal(stores, request_message, relay_redis):
    from a13n_service.environments.websocket.relay_protocol import RelayCredit, RelayFinish
    from a13n_service.environments.websocket.relay_storage import RelayInputDelivery

    owner, worker = stores
    transfer_id = new_object_id("etr")
    request = request_message.model_copy(
        update={"operation": "file.write_bytes", "payload": {"transfer_id": transfer_id}}
    )
    entry = await pending(owner, request)
    await owner.start(request, entry)
    baseline = int(await relay_redis.hget(owner.ledger_key, "_bytes"))
    frames = [
        RelayChunk(
            request_id=request.request_id,
            use=request.use,
            data="eA==",
            transfer=TransferPosition(transfer_id=transfer_id, sequence=i, offset=i),
        )
        for i in range(9)
    ]
    for frame in frames[:8]:
        await owner.send_input(request, frame)
    with pytest.raises(RelayStoreError) as error:
        await owner.send_input(request, frames[8])
    assert error.value.code == "relay_overloaded"
    assert int(await relay_redis.hget(owner.ledger_key, "_count")) == 1
    rows = await owner.read()
    assert all(isinstance(delivery, RelayInputDelivery) for _, delivery in rows)
    first_entry, first = rows[0]
    await owner.acknowledge_input(first_entry, first)
    released = int(await relay_redis.hget(owner.ledger_key, "_bytes"))
    await owner.acknowledge_input(first_entry, first)
    assert int(await relay_redis.hget(owner.ledger_key, "_bytes")) == released
    await owner.send_input(request, frames[8])
    for i in range(9):
        position = TransferPosition(transfer_id=transfer_id, sequence=i + 1, offset=i + 1)
        credit = RelayCredit(request_id=request.request_id, use=request.use, transfer=position)
        await owner.credit(request, entry, credit)
        await owner.credit(request, entry, credit)
    result = RelayTerminal(request_id=request.request_id, use=request.use, transfer=position)
    with pytest.raises(RelayStoreError) as error:
        await owner.complete(request, entry, result)
    assert error.value.code == "transfer_conflict"
    finish = RelayFinish(request_id=request.request_id, use=request.use, transfer=position)
    await owner.send_input(request, finish)
    for input_entry, delivery in rows[1:] + await owner.read():
        await owner.acknowledge_input(input_entry, delivery)
    assert int(await relay_redis.hget(owner.ledger_key, "_bytes")) == baseline
    assert len(await worker.read()) == 9
    await owner.complete(request, entry, result)
    assert [frame for _, frame in await worker.read()] == [result]
    assert (await relay_redis.xpending(owner.requests_key, "owner"))["pending"] == 0


async def test_pruning_parent_also_removes_unconsumed_inputs(stores, request_message, relay_redis):
    owner, _ = stores
    transfer_id = new_object_id("etr")
    request = request_message.model_copy(
        update={"operation": "file.write_bytes", "payload": {"transfer_id": transfer_id}}
    )
    await owner.append(request)
    await owner.send_input(
        request,
        RelayChunk(
            request_id=request.request_id,
            use=request.use,
            data="eA==",
            transfer=TransferPosition(transfer_id=transfer_id, sequence=0, offset=0),
        ),
    )
    await relay_redis.zadd(owner.expiries_key, {request.request_id: 0})
    await owner.prune()
    assert await relay_redis.xlen(owner.requests_key) == 0
    assert await relay_redis.hmget(owner.ledger_key, "_count", "_bytes") == [b"0", b"0"]
