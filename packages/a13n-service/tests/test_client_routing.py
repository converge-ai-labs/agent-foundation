"""Single-send RPC behavior over real Redis; no PostgreSQL or device is needed."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from a13n_harness.environment.models import EnvironmentError
from a13n_service.runs.environments.clients.routing import Failure, Request, Result, Routing, request_channel, subscribe
from a13n_service.runs.tool_executions.routing import Caller
from a13n_service.settings import ClientEnvironments, RedisSettings
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


def request(method="process.start", *, seconds=3):
    return Request(
        environment_id="env_client",
        epoch=uuid4().hex,
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
    node = uuid4().hex
    serving = asyncio.create_task(control.serve(node, dispatch))
    try:
        await asyncio.wait_for(control.ready.wait(), 5)
        yield worker, control, node
    finally:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
        await asyncio.gather(worker.close(), control.close())


async def test_immediate_results_are_not_lost_and_calls_are_not_repeated(redis_url):
    seen = []

    async def dispatch(call):
        seen.append(call.id)
        return {"operation_id": call.id}

    async with routers(redis_url, dispatch) as (worker, _, node):
        calls = [request() for _ in range(4)]
        responses = await asyncio.gather(*(worker.call(node, call) for call in calls))
        assert [response.outcome.value for response in responses] == [{"operation_id": call.id} for call in calls]
        assert sorted(seen) == sorted(call.id for call in calls)
        assert all(isinstance(response.outcome, Result) for response in responses)


async def test_lost_reply_has_unknown_outcome_without_replaying(redis_url):
    worker = Routing(RedisSettings(url=SecretStr(redis_url)), ClientEnvironments())
    node = uuid4().hex
    call = request(seconds=0.3)
    try:
        async with worker.redis.pubsub() as sink:
            await asyncio.wait_for(subscribe(sink, request_channel(node)), 3)
            with pytest.raises(EnvironmentError) as failed:
                await worker.call(node, call)
            assert failed.value.details.get("dispatch_stage") != "pre_dispatch"
            seen = await sink.get_message(ignore_subscribe_messages=True, timeout=1)
            assert Request.model_validate_json(seen["data"]).id == call.id
            assert await sink.get_message(ignore_subscribe_messages=True, timeout=0.1) is None
    finally:
        await worker.close()


async def test_missing_owner_and_expired_request_are_rejected_before_dispatch(redis_url):
    worker = Routing(RedisSettings(url=SecretStr(redis_url)), ClientEnvironments())
    try:
        for call in (request(seconds=-1), request()):
            with pytest.raises(EnvironmentError) as failed:
                await worker.call(uuid4().hex, call)
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

    async with routers(redis_url, dispatch, max_requests=1) as (worker, _, node):
        waiting = asyncio.create_task(worker.call(node, request("process.wait")))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            with pytest.raises(EnvironmentError) as full:
                await worker.call(node, request())
            assert full.value.details["dispatch_stage"] == "pre_dispatch"
            killed = await worker.call(node, request("process.kill"))
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

    async with routers(redis_url, dispatch) as (worker, _, node):
        response = await worker.call(node, request("process.wait", seconds=0.5))
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

    async with routers(redis_url, dispatch) as (worker, _, node):
        subscriptions = []
        create = worker.redis.pubsub

        def capture():
            subscription = create()
            subscriptions.append(subscription)
            return subscription

        monkeypatch.setattr(worker.redis, "pubsub", capture)
        first = request(seconds=1)
        pending = asyncio.create_task(worker.call(node, first))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await subscriptions[0].connection.disconnect()
            with pytest.raises(EnvironmentError) as failed:
                await asyncio.wait_for(pending, 2)
            assert "unknown" in str(failed.value)
        finally:
            release.set()
        second = request()
        response = await worker.call(node, second)
        assert isinstance(response.outcome, Result) and response.outcome.value["id"] == second.id
        assert seen == [first.id, second.id]
