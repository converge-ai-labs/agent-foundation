from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from time import monotonic

import pytest
from a13n_environment.models import EnvironmentError
from a13n_service.environments.websocket.authority import (
    ConnectionIdentity,
    DispatchAuthority,
    LeaseDeadline,
    UseIdentity,
)
from a13n_service.environments.websocket.relay_protocol import (
    RelayChunk,
    RelayFailure,
    RelayRequest,
    RelayTerminal,
    TransferPosition,
)
from a13n_service.environments.websocket.relay_storage import RelayStoreError, WorkerResponseMailbox
from a13n_service.environments.websocket.relay_waiters import RelayOperationError, RelayResponseDispatcher
from a13n_service.ids import new_object_id
from a13n_service.storage.config import RedisMemoryConfig
from a13n_service.storage.redis import open_redis

pytestmark = pytest.mark.anyio
USE = UseIdentity(
    ConnectionIdentity("org", "env", "connection", "epoch", "control"), "use", "run", "attempt", 1, "worker"
)


@pytest.fixture
async def dispatcher():
    async with open_redis(RedisMemoryConfig()) as redis:
        yield RelayResponseDispatcher(WorkerResponseMailbox(redis, "worker"), chunk_capacity=1)


def request():
    return RelayRequest(request_id=new_object_id("erq"), use=USE, operation="file.stat", deadline_ms=10**12)


def deadline(seconds=10):
    return LeaseDeadline(monotonic() + seconds)


def authority():
    return DispatchAuthority(USE, deadline())


def chunk(message, transfer_id, sequence=0, offset=0, data=b"hello"):
    return RelayChunk(
        request_id=message.request_id,
        use=message.use,
        transfer=TransferPosition(transfer_id=transfer_id, sequence=sequence, offset=offset),
        data=base64.b64encode(data).decode(),
    )


async def test_response_can_arrive_before_publication_returns(dispatcher):
    message = request()
    with dispatcher.register(message, authority(), deadline()) as pending:
        pending.begin_publication()
        result = RelayTerminal(request_id=message.request_id, use=USE, result={"value": 1})
        dispatcher.accept(result)
        assert await pending.result() == result
        dispatcher.accept(result.model_copy(update={"result": "late duplicate"}))
        assert await pending.result() == result


async def test_terminal_failure_retains_only_projected_provider_diagnostics(dispatcher):
    message = request()
    error = EnvironmentError(
        "secret native endpoint and payload",
        code="environment_request_invalid",
        retry_hint="fix_input",
        details={
            "field": "pattern",
            "reason": "invalid_regex",
            "native_endpoint": "secret",
            "dispatch_stage": "pre_dispatch",
        },
    )
    with dispatcher.register(message, authority(), deadline()) as pending:
        dispatcher.accept(
            RelayTerminal(request_id=message.request_id, use=USE, error=RelayFailure.from_environment(error))
        )
        with pytest.raises(RelayOperationError) as raised:
            await pending.result()
        assert raised.value.details["field"] == "pattern"
        assert raised.value.details["reason"] == "invalid_regex"
        assert "native_endpoint" not in raised.value.details
        assert raised.value.retry_hint == "fix_input"
        assert raised.value.failure.certainty == "not_dispatched"
        assert "secret" not in str(raised.value)


async def test_foreign_use_and_out_of_order_responses_never_complete_another_waiter(dispatcher):
    first, second = request(), request()
    with (
        dispatcher.register(first, authority(), deadline()) as one,
        dispatcher.register(second, authority(), deadline()) as two,
    ):
        result = RelayTerminal(request_id=first.request_id, use=USE, result="first")
        dispatcher.accept(result.model_copy(update={"use": replace(USE, attempt_fence=2)}))
        dispatcher.accept(RelayTerminal(request_id=second.request_id, use=USE, result="second"))
        assert (await two.result()).result == "second"
        waiting = asyncio.create_task(one.result())
        await asyncio.sleep(0)
        assert not waiting.done()
        dispatcher.accept(result)
        assert await waiting == result


async def test_local_expiry_is_unknown_only_after_possible_publication(dispatcher):
    with dispatcher.register(request(), authority(), deadline(0.01)) as pending:
        pending.begin_publication()
        with pytest.raises(RelayOperationError) as error:
            await pending.result()
        assert error.value.failure.code == "environment_timeout"
        assert error.value.failure.certainty == "unknown"
    with dispatcher.register(request(), authority(), deadline(-1)) as pending:
        with pytest.raises(RelayOperationError) as error:
            pending.begin_publication()
        assert error.value.failure.certainty == "not_dispatched"


async def test_renewal_extends_a_sleeping_wait_without_reviving_expired_authority(dispatcher):
    message = request()
    grant = DispatchAuthority(USE, deadline(0.04))
    with dispatcher.register(message, grant, deadline(1)) as pending:
        waiting = asyncio.create_task(pending.result())
        await asyncio.sleep(0.01)
        grant.renew(USE, deadline(1))
        await asyncio.sleep(0.05)
        assert not waiting.done()
        dispatcher.accept(RelayTerminal(request_id=message.request_id, use=USE))
        assert (await waiting).request_id == message.request_id


async def test_use_revocation_wakes_waiter_and_late_response_cannot_revive_it(dispatcher):
    message = request()
    with dispatcher.register(message, authority(), deadline()) as pending:
        pending.begin_publication()
        waiting = asyncio.create_task(pending.result())
        await asyncio.sleep(0)
        dispatcher.fence_use(USE)
        dispatcher.accept(RelayTerminal(request_id=message.request_id, use=USE))
        with pytest.raises(RelayOperationError) as error:
            await waiting
        assert error.value.failure.code == "environment_unavailable"


async def test_slow_transfer_fails_alone_and_does_not_block_unary_response(dispatcher):
    stream, unary = request(), request()
    transfer_id = new_object_id("etr")
    with (
        dispatcher.register(stream, authority(), deadline(), streaming="download") as slow,
        dispatcher.register(unary, authority(), deadline()) as fast,
    ):
        dispatcher.accept(chunk(stream, transfer_id))
        dispatcher.accept(chunk(stream, transfer_id, sequence=1, offset=5))
        dispatcher.accept(RelayTerminal(request_id=unary.request_id, use=USE, result="ready"))
        assert (await fast.result()).result == "ready"
        with pytest.raises(RelayOperationError) as error:
            await slow.next_chunk()
        assert error.value.failure.code == "environment_overloaded"


async def test_duplicate_chunk_is_ignored_and_missing_chunk_never_becomes_eof(dispatcher):
    message = request()
    transfer_id = new_object_id("etr")
    with dispatcher.register(message, authority(), deadline(), streaming="download") as pending:
        frame = chunk(message, transfer_id)
        dispatcher.accept(frame)
        dispatcher.accept(frame)
        assert (await pending.next_chunk()).data == b"hello"
        dispatcher.accept(
            RelayTerminal(
                request_id=message.request_id,
                use=USE,
                transfer=TransferPosition(transfer_id=transfer_id, sequence=2, offset=10),
            )
        )
        with pytest.raises(RelayOperationError) as error:
            await pending.next_chunk()
        assert error.value.failure.code == "environment_transfer_incomplete"


async def test_empty_transfer_requires_explicit_successful_terminal(dispatcher):
    message = request()
    with dispatcher.register(message, authority(), deadline(), streaming="download") as pending:
        dispatcher.accept(
            RelayTerminal(
                request_id=message.request_id,
                use=USE,
                transfer=TransferPosition(transfer_id=new_object_id("etr"), sequence=0, offset=0),
            )
        )
        assert await pending.next_chunk() is None


async def test_caller_cancellation_unregisters_before_late_completion(dispatcher):
    message = request()
    entered = asyncio.Event()

    async def call():
        with dispatcher.register(message, authority(), deadline()) as pending:
            pending.begin_publication()
            entered.set()
            await pending.result()

    task = asyncio.create_task(call())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    dispatcher.accept(RelayTerminal(request_id=message.request_id, use=USE))
    assert not dispatcher._pending


async def test_reader_failure_settles_waiters_and_closes_registration(dispatcher, monkeypatch):
    async def unavailable(**kwargs):
        raise RelayStoreError("relay_unavailable")

    monkeypatch.setattr(dispatcher._mailbox, "read", unavailable)
    with dispatcher.register(request(), authority(), deadline()) as pending:
        pending.begin_publication()
        reader = asyncio.create_task(dispatcher.run())
        with pytest.raises(RelayOperationError) as error:
            await pending.result()
        assert error.value.failure.certainty == "unknown"
        with pytest.raises(RelayStoreError):
            await reader
    with pytest.raises(RelayOperationError):
        with dispatcher.register(request(), authority(), deadline()):
            pytest.fail("Closed reader must not accept new requests")


async def test_waiter_capacity_reserves_cancellation(dispatcher):
    limited = RelayResponseDispatcher(dispatcher._mailbox, max_pending=2, control_reserve=1)
    with limited.register(request(), authority(), deadline()):
        with pytest.raises(RelayOperationError) as error:
            with limited.register(request(), authority(), deadline()):
                pytest.fail("Ordinary operation exceeded capacity")
        assert error.value.failure.code == "environment_overloaded"
        cancellation = request().model_copy(update={"operation": "operation.cancel"})
        with limited.register(cancellation, authority(), deadline()):
            pass


async def test_upload_wait_for_credit_is_woken_by_use_loss(dispatcher):
    message = request().model_copy(update={"payload": {"transfer_id": new_object_id("etr")}})
    with dispatcher.register(message, authority(), deadline(), streaming="upload") as pending:
        pending.begin_publication()
        window = await pending.upload_slot()
        window.sent(5)
        waiting = asyncio.create_task(pending.upload_slot())
        await asyncio.sleep(0)
        assert not waiting.done()
        dispatcher.fence_use(USE)
        with pytest.raises(RelayOperationError) as error:
            await waiting
        assert error.value.code == "environment_unavailable"


@pytest.mark.parametrize("fault", ["foreign_transfer", "wrong_offset", "out_of_order", "before_finish"])
async def test_upload_rejects_invalid_credit_and_premature_success(dispatcher, fault):
    from a13n_service.environments.websocket.relay_protocol import RelayCredit

    message = request().model_copy(update={"payload": {"transfer_id": new_object_id("etr")}})
    with dispatcher.register(message, authority(), deadline(), streaming="upload") as pending:
        window = await pending.upload_slot()
        window.sent(5)
        position = window.position
        if fault == "before_finish":
            dispatcher.accept(RelayTerminal(request_id=message.request_id, use=USE, transfer=position))
        else:
            changed = {
                "foreign_transfer": {"transfer_id": new_object_id("etr")},
                "wrong_offset": {"offset": 6},
                "out_of_order": {"sequence": 2},
            }[fault]
            dispatcher.accept(
                RelayCredit(request_id=message.request_id, use=USE, transfer=position.model_copy(update=changed))
            )
        with pytest.raises(RelayOperationError) as error:
            await pending.result()
        assert error.value.code == "environment_transfer_incomplete"


async def test_duplicate_credit_cannot_reopen_a_consumed_slot(dispatcher):
    from a13n_service.environments.websocket.relay_protocol import RelayCredit

    message = request().model_copy(update={"payload": {"transfer_id": new_object_id("etr")}})
    with dispatcher.register(message, authority(), deadline(), streaming="upload") as pending:
        window = await pending.upload_slot()
        window.sent(5)
        frame = RelayCredit(request_id=message.request_id, use=USE, transfer=window.position)
        dispatcher.accept(frame)
        assert window.has_capacity
        window.sent(5)
        dispatcher.accept(frame)
        assert not window.has_capacity
