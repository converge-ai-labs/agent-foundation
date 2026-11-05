"""Real Redis binary frames: bounded queues, fencing, deadlines and deterministic cleanup."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from a13n_service.runs.environments.clients import streams
from a13n_service.runs.environments.clients.routing import Routing
from a13n_service.runs.environments.clients.streams import MAX_FRAMES, TTL_SECONDS, StreamPair, transfer_keys
from a13n_service.settings import ClientEnvironments, RedisSettings
from pydantic import SecretStr
from redis.exceptions import ConnectionError

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def pair(redis_url, *, seconds=5, limit=4096):
    routing = Routing(RedisSettings(url=SecretStr(redis_url)), ClientEnvironments())
    identity, epoch, transfer = "env_" + uuid4().hex, uuid4().hex, uuid4().hex
    worker = StreamPair(routing.redis, identity, epoch, transfer, side="worker", limit=limit, seconds=seconds)
    control = StreamPair(routing.redis, identity, epoch, transfer, side="control", limit=limit, seconds=seconds)
    await routing.redis.set(worker.owner, epoch, ex=30)
    await control.create()
    try:
        yield worker, control
    finally:
        await worker.close()
        await control.close()
        await routing.redis.delete(worker.owner)
        await routing.close()


async def test_raw_binary_round_trip_and_ttl_need_no_text_encoding(redis_url):
    async with pair(redis_url) as (worker, control):
        payload = bytes(range(256)) * 8
        await worker.send(payload)
        assert await control.receive() == payload
        await control.send(payload[::-1])
        assert await worker.receive() == payload[::-1]
        for key in worker.keys:
            assert await worker.redis.type(key) == "stream"
            assert await worker.redis.xlen(key) == 0
            assert 0 < await worker.redis.ttl(key) <= TTL_SECONDS
        assert worker.keys[0].endswith(":w2c") and worker.keys[1].endswith(":c2w")
        await worker.close()
        assert await worker.redis.exists(*worker.keys) == 0
        with pytest.raises(OSError):
            await control.send(b"late")
        assert await worker.redis.exists(*worker.keys) == 0


async def test_slow_consumer_is_bounded_without_trimming_frames(redis_url):
    async with pair(redis_url) as (worker, control):
        for i in range(MAX_FRAMES):
            await worker.send(bytes([i]))
        pending = asyncio.create_task(worker.send(b"next"))
        try:
            await asyncio.sleep(0.05)
            assert not pending.done()
            assert await worker.redis.xlen(worker.write_key) == MAX_FRAMES
            assert await control.receive() == b"\x00"
            await asyncio.wait_for(pending, 1)
            assert [await control.receive() for _ in range(MAX_FRAMES)] == [
                *(bytes([i]) for i in range(1, MAX_FRAMES)),
                b"next",
            ]
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("cause", ["expired", "new_epoch", "closed", "deadline"])
async def test_interruption_fails_both_directions_without_recreating_keys(redis_url, cause):
    async with pair(redis_url, seconds=0.05 if cause == "deadline" else 5) as (worker, control):
        if cause == "expired":
            for key in worker.keys:
                await worker.redis.pexpire(key, 1)
            await asyncio.sleep(0.02)
        elif cause == "new_epoch":
            await worker.redis.set(worker.owner, uuid4().hex, ex=30)
        elif cause == "closed":
            await control.close()
        else:
            await asyncio.sleep(0.06)
        with pytest.raises((OSError, TimeoutError)):
            await worker.send(b"late")
        with pytest.raises((OSError, TimeoutError)):
            await worker.receive()
        if cause == "deadline":
            assert await worker.redis.xlen(worker.write_key) == 0
        await worker.close()
        assert await worker.redis.exists(*worker.keys) == 0


async def test_stalled_read_and_full_send_have_transfer_deadlines(redis_url):
    async with pair(redis_url, seconds=0.1) as (worker, _control):
        with pytest.raises(TimeoutError):
            await worker.receive()
    async with pair(redis_url) as (worker, _control):
        for _ in range(MAX_FRAMES):
            await worker.send(b"x")
        worker.deadline = asyncio.get_running_loop().time() + 0.1
        with pytest.raises(TimeoutError):
            await worker.send(b"overflow")
        assert await worker.redis.xlen(worker.write_key) == MAX_FRAMES


async def test_one_transfer_cleanup_does_not_touch_another_on_the_same_environment(redis_url):
    async with pair(redis_url) as (worker, control):
        other_id = uuid4().hex
        environment = worker.owner.removeprefix("a13n:client-environment:").removesuffix(":owner")
        other = StreamPair(worker.redis, environment, worker.epoch, other_id, side="worker", limit=4096, seconds=5)
        other_peer = StreamPair(
            worker.redis, environment, worker.epoch, other_id, side="control", limit=4096, seconds=5
        )
        await other_peer.create()
        try:
            await worker.send(b"first")
            await other.send(b"second")
            await control.close()
            assert await other_peer.receive() == b"second"
            assert other.keys == transfer_keys(environment, worker.epoch, other_id)
        finally:
            await other.close()


async def test_lost_xadd_reply_is_not_replayed(redis_url, monkeypatch):
    async with pair(redis_url) as (worker, control):
        evaluate, sent = worker.redis.eval, []

        async def lose_reply(script, *args):
            result = await evaluate(script, *args)
            if script == streams._SEND:
                sent.append(1)
                raise ConnectionError("Lost response after XADD")
            return result

        monkeypatch.setattr(worker.redis, "eval", lose_reply)
        with pytest.raises(ConnectionError):
            await worker.send(b"only-once")
        assert len(sent) == 1 and await worker.redis.xlen(worker.write_key) == 1
        assert await control.receive() == b"only-once"
        assert await worker.redis.xlen(worker.write_key) == 0


async def test_oversized_frame_never_enters_redis(redis_url):
    async with pair(redis_url, limit=16) as (worker, _control):
        with pytest.raises(OSError):
            await worker.send(b"x" * 17)
        assert await worker.redis.xlen(worker.write_key) == 0
