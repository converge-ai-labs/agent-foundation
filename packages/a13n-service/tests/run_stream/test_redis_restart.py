"""A real primary restart must not revive a rolled-back publication fence."""

from datetime import UTC, datetime

import anyio
import pytest
from a13n_service.run_stream import RedisRunStream, RunStreamEvent, deterministic_run_stream_event_id
from a13n_service.run_stream.domain import PublicationUnavailable, RunStreamReplayGap
from a13n_service.run_stream.redis import _keys
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, TimeoutError
from testcontainers.redis import RedisContainer
from tests.run_stream.support import ATTEMPT_ID, activate_stream, opening_event

pytestmark = pytest.mark.anyio


async def test_primary_restart_rejects_an_old_fence_restored_from_disk():
    organization_id = "org_1234567890abcdef"
    run_id = "run_1234567890abcdef"
    thread_id = "thread-1234567890abcdef"
    with RedisContainer("redis:8-alpine") as container:

        def client():
            return Redis.from_url(
                f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0",
                socket_connect_timeout=3,
                socket_timeout=3,
            )

        async with client() as redis:
            stream = RedisRunStream(redis)
            await activate_stream(stream, organization_id, run_id, thread_id)
            old_primary = await stream.server_incarnation()
            await redis.save()
            await activate_stream(
                stream,
                organization_id,
                run_id,
                thread_id,
                attempt_id="rat_2222222222222222",
                number=2,
                reason="lease_expired",
            )
        wrapped = container.get_wrapped_container()
        # SIGKILL prevents saving the successor fence: restart restores generation 1.
        await anyio.to_thread.run_sync(wrapped.kill)
        await anyio.to_thread.run_sync(wrapped.start)
        async with client() as redis:
            with anyio.fail_after(20):
                while True:
                    try:
                        await redis.ping()
                        break
                    except (ConnectionError, TimeoutError):
                        await anyio.sleep(0.05)
            stream = RedisRunStream(redis)
            assert await stream.server_incarnation() != old_primary
            _, metadata = _keys(organization_id, run_id)
            assert await redis.hget(metadata, "fence") == b"1"
            old_observation = RunStreamEvent(
                event_id=deterministic_run_stream_event_id("restart-test", "old"),
                event_type="agui.custom",
                run_id=run_id,
                thread_id=thread_id,
                run_attempt_id=ATTEMPT_ID,
                occurred_at=datetime.now(UTC),
                payload={"old": True},
            )
            with pytest.raises(PublicationUnavailable):
                await stream.append(organization_id, old_observation, attempt_number=1)
            with pytest.raises(PublicationUnavailable):
                await stream.activate(
                    organization_id,
                    opening_event(run_id, thread_id, attempt_id="rat_2222222222222222", number=2),
                    attempt_number=2,
                    reason="lease_expired",
                    allow_create=True,
                )
            with pytest.raises(RunStreamReplayGap):
                await stream.read(organization_id, run_id, after_stream_id=None, limit=10)
