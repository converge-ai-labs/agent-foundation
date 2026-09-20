import pytest
from redis.asyncio import Redis

pytestmark = pytest.mark.anyio


async def test_binary_strings_and_expiration(any_redis_client: Redis) -> None:
    assert await any_redis_client.set(b"cache:key", b"\x00value", ex=30)
    assert await any_redis_client.get(b"cache:key") == b"\x00value"
    assert 0 < await any_redis_client.ttl(b"cache:key") <= 30


async def test_hash_list_set_and_sorted_set(any_redis_client: Redis) -> None:
    await any_redis_client.hset(b"hash", mapping={b"a": b"1", b"b": b"2"})
    await any_redis_client.rpush(b"list", b"a", b"b")
    await any_redis_client.sadd(b"set", b"a", b"b")
    await any_redis_client.zadd(b"sorted", {b"later": 2, b"first": 1})

    assert await any_redis_client.hgetall(b"hash") == {b"a": b"1", b"b": b"2"}
    assert await any_redis_client.lrange(b"list", 0, -1) == [b"a", b"b"]
    assert await any_redis_client.smembers(b"set") == {b"a", b"b"}
    assert await any_redis_client.zrange(b"sorted", 0, -1) == [b"first", b"later"]


async def test_pipeline_and_transaction(any_redis_client: Redis) -> None:
    async with any_redis_client.pipeline(transaction=True) as pipeline:
        pipeline.set(b"counter", b"1")
        pipeline.incr(b"counter")
        results = await pipeline.execute()

    assert results == [True, 2]
    assert await any_redis_client.get(b"counter") == b"2"


async def test_stream_consumer_group(any_redis_client: Redis) -> None:
    first_id = await any_redis_client.xadd(b"stream", {b"payload": b"first"})
    await any_redis_client.xgroup_create(b"stream", b"workers", id=b"0")

    entries = await any_redis_client.xreadgroup(b"workers", b"worker-1", {b"stream": b">"}, count=1)

    assert entries == [[b"stream", [(first_id, {b"payload": b"first"})]]]
    assert await any_redis_client.xack(b"stream", b"workers", first_id) == 1


async def test_pubsub(any_redis_client: Redis) -> None:
    async with any_redis_client.pubsub() as subscriber:
        await subscriber.subscribe(b"notifications")
        await subscriber.get_message(timeout=1)
        assert await any_redis_client.publish(b"notifications", b"ready") == 1

        message = await subscriber.get_message(ignore_subscribe_messages=True, timeout=1)

    assert message is not None
    assert message["data"] == b"ready"
