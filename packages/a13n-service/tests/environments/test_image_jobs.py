"""Short-lived Docker image work never survives its caller or blocks Engine probes."""

import asyncio
from unittest.mock import Mock

import pytest
from a13n_harness.providers.environment.docker.configuration import DockerEnvironmentConfiguration
from a13n_service.environments import image_jobs
from fakeredis.aioredis import FakeRedis

pytestmark = pytest.mark.anyio


def identity(request_id: str, provider_id: str = "envp_test") -> image_jobs.ImageTestIdentity:
    return image_jobs.ImageTestIdentity(
        request_id=request_id,
        provider_id=provider_id,
        organization_id="org_test",
        workspace_id="ws_test",
        principal_type="user",
        principal_id="usr_test",
    )


async def _until(predicate, *, timeout: float = 2) -> None:
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.01)


async def test_expired_queued_request_is_discarded_after_caller_leaves(monkeypatch):
    async with FakeRedis() as redis:
        request = image_jobs.ImageTestRequest(
            identity=identity("envtest_expired"), configuration=DockerEnvironmentConfiguration()
        )
        caller = asyncio.create_task(image_jobs.request_image_test(redis, request))
        await _until(lambda: redis.zcard(image_jobs._QUEUE))
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        assert not await redis.exists(image_jobs._valid_key(request.identity))

        monkeypatch.setattr(image_jobs, "_now", lambda: request.expires_at + 181)
        # Even a stale validity marker cannot revive the expired request.
        await redis.set(image_jobs._valid_key(request.identity), "1", ex=130)
        connect = Mock(side_effect=AssertionError("expired work reached Docker"))
        monkeypatch.setattr(image_jobs.DockerSDKEngine, "connect", connect)
        worker = image_jobs.DockerImageTestWorker(Mock(), redis)
        running = asyncio.create_task(worker.run())
        await _until(lambda: _queue_empty(redis))
        await worker.shutdown()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        connect.assert_not_called()


async def test_new_requests_do_not_evict_live_queued_work():
    async with FakeRedis() as redis:
        first = image_jobs.ImageTestRequest(
            identity=identity("envtest_first"), configuration=DockerEnvironmentConfiguration()
        )
        first_caller = asyncio.create_task(image_jobs.request_image_test(redis, first))
        await _until(lambda: redis.exists(image_jobs._valid_key(first.identity)))
        await redis.zadd(image_jobs._QUEUE, {f"queued-{index}": first.expires_at + 1 for index in range(300)})
        second = image_jobs.ImageTestRequest(
            identity=identity("envtest_second"), configuration=DockerEnvironmentConfiguration()
        )
        second_caller = asyncio.create_task(image_jobs.request_image_test(redis, second))
        await _until(lambda: redis.exists(image_jobs._valid_key(second.identity)))
        assert await redis.zscore(image_jobs._QUEUE, first.model_dump_json()) is not None
        assert await redis.zcard(image_jobs._QUEUE) == 302
        assert await redis.ttl(image_jobs._QUEUE) > 0
        first_caller.cancel()
        second_caller.cancel()
        await asyncio.gather(first_caller, second_caller, return_exceptions=True)


async def test_reused_request_id_cannot_replace_active_work():
    async with FakeRedis() as redis:
        request = image_jobs.ImageTestRequest(
            identity=identity("envtest_reused", "envp_original"), configuration=DockerEnvironmentConfiguration()
        )
        caller = asyncio.create_task(image_jobs.request_image_test(redis, request))
        await _until(lambda: redis.exists(image_jobs._valid_key(request.identity)))
        replacement = request.model_copy()
        with pytest.raises(ValueError, match="already in use"):
            await image_jobs.request_image_test(redis, replacement)
        assert await redis.get(image_jobs._valid_key(request.identity)) == b"1"
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)


async def test_cancel_arriving_before_enqueue_prevents_engine_work(monkeypatch):
    async with FakeRedis() as redis:
        request = image_jobs.ImageTestRequest(
            identity=identity("envtest_early"), configuration=DockerEnvironmentConfiguration()
        )
        await image_jobs.cancel_image_test(redis, request.identity)
        result = await image_jobs.request_image_test(redis, request)
        assert result.error == "Image test canceled"
        connect = Mock(side_effect=AssertionError("canceled work reached Docker"))
        monkeypatch.setattr(image_jobs.DockerSDKEngine, "connect", connect)
        worker = image_jobs.DockerImageTestWorker(Mock(), redis)
        running = asyncio.create_task(worker.run())
        await _until(lambda: _queue_empty(redis))
        await worker.shutdown()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        connect.assert_not_called()


async def _queue_empty(redis: FakeRedis) -> bool:
    return await redis.zcard(image_jobs._QUEUE) == 0


async def test_connectivity_refreshes_while_image_execution_is_blocked(monkeypatch):
    async with FakeRedis() as redis:
        monkeypatch.setattr(image_jobs, "_PROBE_INTERVAL", 0.02)
        monkeypatch.setattr(image_jobs, "_CONNECTIVITY_TTL", 1)
        probe = image_jobs.DockerConnectivityProbe(Mock(), redis)
        count = 0

        async def observe():
            nonlocal count
            count += 1
            await redis.set("probe", str(count), ex=image_jobs._CONNECTIVITY_TTL)

        monkeypatch.setattr(probe, "probe_connectivity", observe)
        worker = image_jobs.DockerImageTestWorker(Mock(), redis)
        started = asyncio.Event()

        async def blocked(_request):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(worker, "_execute", blocked)
        request = image_jobs.ImageTestRequest(
            identity=identity("envtest_blocked"), configuration=DockerEnvironmentConfiguration()
        )
        await redis.set(image_jobs._valid_key(request.identity), "1", ex=130)
        await redis.zadd(image_jobs._QUEUE, {request.model_dump_json(): request.expires_at})
        probing = asyncio.create_task(probe.run())
        running = asyncio.create_task(worker.run())
        try:
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.sleep(1.2)
            assert count >= 3
            assert await redis.ttl("probe") > 0
        finally:
            await probe.shutdown()
            await worker.shutdown()
            probing.cancel()
            running.cancel()
            await asyncio.gather(probing, running, return_exceptions=True)
