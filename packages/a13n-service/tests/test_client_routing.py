"""Single-send RPC behavior over real Redis; no PostgreSQL or device is needed."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from a13n_harness.environment.models import EnvironmentError
from a13n_service.runs.environments.clients.routing import (
    Failure,
    Request,
    Response,
    Result,
    Routing,
    reply_channel,
    request_channel,
    subscribe,
)
from a13n_service.runs.tool_executions.routing import Caller
from a13n_service.settings import ClientEnvironments, RedisSettings
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


def request(method="process.start", *, seconds=3, environment_id="env_client", epoch="1" * 32):
    return Request(
        environment_id=environment_id,
        mount_name="pc",
        epoch=epoch,
        scope_id=uuid4().hex,
        thread_id="thr_client",
        caller=Caller(run_id="run_client", attempt_id="att_client", worker_id="worker-test"),
        method=method,
        params={},
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
    )


@asynccontextmanager
async def routers(redis_url, dispatch, *, max_requests=4):
    config = ClientEnvironments(max_requests=max_requests, response_margin_seconds=0.1)
    worker, control = [Routing(RedisSettings(url=SecretStr(redis_url)), config) for _ in range(2)]
    ready = asyncio.Event()
    serving = asyncio.create_task(
        control.serve("env_client", "1" * 32, dispatch, ready=ready, on_subscription_lost=AsyncMock())
    )
    try:
        await asyncio.wait_for(ready.wait(), 5)
        yield worker, control, ready
    finally:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
        await asyncio.gather(worker.close(), control.close())


async def test_immediate_results_are_not_lost_and_calls_are_not_repeated(redis_url):
    seen = []

    async def dispatch(call):
        seen.append(call.id)
        return {"operation_id": call.id}

    async with routers(redis_url, dispatch) as (worker, _, _):
        calls = [request() for _ in range(4)]
        responses = await asyncio.gather(*(worker.call(call) for call in calls))
        assert [response.outcome.value for response in responses] == [{"operation_id": call.id} for call in calls]
        assert sorted(seen) == sorted(call.id for call in calls)
        assert all(isinstance(response.outcome, Result) for response in responses)


async def test_lost_reply_has_unknown_outcome_without_replaying(redis_url):
    worker = Routing(RedisSettings(url=SecretStr(redis_url)), ClientEnvironments())
    try:
        async with worker.redis.pubsub() as sink:
            await asyncio.wait_for(subscribe(sink, request_channel("env_client")), 3)
            # Start the request budget after the observer is ready, using the normal fixture deadline.
            call = request()
            with pytest.raises(EnvironmentError) as failed:
                await worker.call(call)
            assert failed.value.details.get("dispatch_stage") != "pre_dispatch"
            seen = await sink.get_message(ignore_subscribe_messages=True, timeout=1)
            assert Request.model_validate_json(seen["data"]).id == call.id
            # The caller has settled. A same-channel sentinel follows every publication it made.
            sentinel = "publication-barrier"
            await worker.redis.publish(request_channel(call.environment_id), sentinel)
            following = await sink.get_message(ignore_subscribe_messages=True, timeout=3)
            assert following["data"] == sentinel
    finally:
        await worker.close()


async def test_missing_owner_and_expired_request_are_rejected_before_dispatch(redis_url):
    worker = Routing(RedisSettings(url=SecretStr(redis_url)), ClientEnvironments())
    try:
        for call in (request(seconds=-1), request()):
            with pytest.raises(EnvironmentError) as failed:
                await worker.call(call)
            assert failed.value.details["dispatch_stage"] == "pre_dispatch"
    finally:
        await worker.close()


async def test_cleanup_reaches_control_while_ordinary_capacity_is_full(redis_url):
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def dispatch(call):
        seen.append(call.method)
        if call.method == "process.wait":
            entered.set()
            await release.wait()
        return {}

    async with routers(redis_url, dispatch, max_requests=1) as (worker, _, _):
        waiting = asyncio.create_task(worker.call(request("process.wait")))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            with pytest.raises(EnvironmentError) as full:
                await worker.call(request())
            assert full.value.details["dispatch_stage"] == "pre_dispatch"
            killed = await worker.call(request("process.kill"))
            assert isinstance(killed.outcome, Result)
            assert seen == ["process.wait", "process.kill"]
        finally:
            release.set()
            await waiting


async def test_control_timeout_does_not_dispatch_a_kill_or_repeat_start(redis_url):
    seen, cancelled = [], asyncio.Event()

    async def dispatch(call):
        seen.append(call.method)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with routers(redis_url, dispatch) as (worker, _, _):
        response = await worker.call(request("process.wait", seconds=0.5))
        assert isinstance(response.outcome, Failure)
        assert not response.outcome.pre_dispatch
        assert cancelled.is_set() and seen == ["process.wait"]


async def test_interrupted_reply_subscription_ends_without_replaying_the_command(redis_url, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def dispatch(call):
        seen.append(call.id)
        entered.set()
        await release.wait()
        return {"id": call.id}

    async with routers(redis_url, dispatch) as (worker, _, _):
        subscriptions = []
        create = worker.redis.pubsub

        def capture():
            subscription = create()
            subscriptions.append(subscription)
            return subscription

        monkeypatch.setattr(worker.redis, "pubsub", capture)
        async with worker.replies("env_client"):
            first = request(seconds=1)
            pending = asyncio.create_task(worker.call(first))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await subscriptions[0].connection.disconnect()
                with pytest.raises(EnvironmentError) as failed:
                    await asyncio.wait_for(pending, 2)
                assert "unknown" in str(failed.value)
            finally:
                release.set()
            second = request()
            response = await worker.call(second)
            assert isinstance(response.outcome, Result) and response.outcome.value["id"] == second.id
            assert seen == [first.id, second.id]
            assert len(subscriptions) == 2
            assert await worker.redis.pubsub_numsub(reply_channel("env_client")) == [(reply_channel("env_client"), 1)]
            third = request()
            await worker.call(third)
            assert len(subscriptions) == 2
            assert seen == [first.id, second.id, third.id]


async def test_workers_share_environment_reply_channel_without_mixing_calls(redis_url):
    entered = asyncio.Queue()
    releases = {}

    async def dispatch(call):
        gate = releases[call.id] = asyncio.Event()
        await entered.put(call.id)
        await gate.wait()
        return {"id": call.id}

    async with routers(redis_url, dispatch) as (first, control, _):
        second = Routing(RedisSettings(url=SecretStr(redis_url)), first.config)
        calls = [request() for _ in range(4)]
        pending = []
        try:
            async with first.replies("env_client"), second.replies("env_client"):
                pending = [
                    asyncio.create_task(worker.call(call))
                    for worker, call in zip((first, second, first, second), calls, strict=True)
                ]
                for _ in calls:
                    await asyncio.wait_for(entered.get(), 3)
                assert await control.redis.pubsub_numsub(reply_channel("env_client")) == [
                    (reply_channel("env_client"), 2)
                ]
                # Same request ID alone is insufficient, even on the right channel.
                for changed in ({"epoch": "2" * 32}, {"scope_id": "2" * 32}, {"environment_id": "env_other"}):
                    forged = Response(
                        id=calls[0].id,
                        environment_id=calls[0].environment_id,
                        epoch=calls[0].epoch,
                        scope_id=calls[0].scope_id,
                        outcome=Result(value={"id": "wrong"}),
                    ).model_copy(update=changed)
                    await control.redis.publish(reply_channel("env_client"), forged.model_dump_json())
                for index in reversed(range(len(calls))):
                    releases[calls[index].id].set()
                    response = await asyncio.wait_for(pending[index], 3)
                    assert isinstance(response.outcome, Result)
                    assert response.outcome.value == {"id": calls[index].id}
                # A subsequent call reuses the same subscription while the Session holds it.
                subsequent = request()
                pending.append(asyncio.create_task(first.call(subsequent)))
                assert await asyncio.wait_for(entered.get(), 3) == subsequent.id
                assert await control.redis.pubsub_numsub(reply_channel("env_client")) == [
                    (reply_channel("env_client"), 2)
                ]
                releases[subsequent.id].set()
                await pending[-1]
            assert not first._replies and not second._replies
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await second.close()


async def test_replacement_control_uses_same_channel_and_old_subscriber_does_not_reply(redis_url):
    old_dispatch, new_dispatch = AsyncMock(return_value={"owner": "old"}), AsyncMock(return_value={"owner": "new"})
    async with routers(redis_url, old_dispatch) as (worker, old, _):
        new = Routing(RedisSettings(url=SecretStr(redis_url)), old.config)
        ready = asyncio.Event()
        serving = asyncio.create_task(
            new.serve("env_client", "2" * 32, new_dispatch, ready=ready, on_subscription_lost=AsyncMock())
        )
        try:
            await asyncio.wait_for(ready.wait(), 3)
            async with worker.redis.pubsub() as observer:
                await subscribe(observer, reply_channel("env_client"))
                call = request(epoch="2" * 32)
                result = await worker.call(call)
                assert result.outcome.value == {"owner": "new"}
                old_dispatch.assert_not_awaited()
                new_dispatch.assert_awaited_once()
                response = await observer.get_message(ignore_subscribe_messages=True, timeout=1)
                assert Response.model_validate_json(response["data"]).id == call.id
                assert await observer.get_message(ignore_subscribe_messages=True, timeout=0.1) is None
        finally:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
            await new.close()


async def test_cancelled_call_does_not_close_shared_reply_subscription(redis_url):
    entered, release = asyncio.Queue(), asyncio.Event()

    async def dispatch(call):
        await entered.put(call.id)
        await release.wait()
        return {"id": call.id}

    async with routers(redis_url, dispatch) as (worker, _, _):
        cancelled, successful = request(), request()
        first, second = [asyncio.create_task(worker.call(call)) for call in (cancelled, successful)]
        try:
            for _ in range(2):
                await asyncio.wait_for(entered.get(), 3)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            result = await second
            assert result.outcome.value == {"id": successful.id}
            assert not worker._replies
        finally:
            release.set()
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)


async def test_request_subscription_failure_does_not_stop_other_environments(redis_url, monkeypatch):
    from redis.asyncio.client import PubSub
    from redis.exceptions import ConnectionError

    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def dispatch(call):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with routers(redis_url, dispatch) as (worker, control, first_ready):
        other_ready, other_lost = asyncio.Event(), AsyncMock()
        serving = asyncio.create_task(
            control.serve(
                "env_other",
                "2" * 32,
                AsyncMock(return_value={"ok": True}),
                ready=other_ready,
                on_subscription_lost=other_lost,
            )
        )
        pending = asyncio.create_task(worker.call(request(seconds=0.5)))
        try:
            await asyncio.wait_for(other_ready.wait(), 3)
            await asyncio.wait_for(entered.wait(), 3)
            get_message = PubSub.get_message

            async def interrupted(subscription, *args, **kwargs):
                if request_channel("env_client") in subscription.channels:
                    raise ConnectionError("Environment request subscription lost")
                return await get_message(subscription, *args, **kwargs)

            monkeypatch.setattr(PubSub, "get_message", interrupted)
            # Wake a reader already blocked before the injection, without dispatching work.
            await control.redis.publish(request_channel("env_client"), "not a request")
            async with asyncio.timeout(3):
                while first_ready.is_set():
                    await asyncio.sleep(0.01)
            assert cancelled.is_set()
            with pytest.raises(EnvironmentError) as failed:
                await pending
            assert "unknown" in str(failed.value)
            result = await worker.call(request(environment_id="env_other", epoch="2" * 32))
            assert result.outcome.value == {"ok": True}
            assert other_ready.is_set()
            other_lost.assert_not_awaited()
        finally:
            pending.cancel()
            serving.cancel()
            await asyncio.gather(pending, serving, return_exceptions=True)
