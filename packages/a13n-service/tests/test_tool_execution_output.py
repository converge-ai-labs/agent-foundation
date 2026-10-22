"""Disposable Redis output never owns execution, completion, or lease renewal."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.commands import ProcessIdentity, ProcessStatus
from a13n_harness.environment.retention import EnvironmentOutputCapture
from a13n_harness.toolsets.shell_control import ShellTarget
from a13n_harness.toolsets.shell_observation import ShellObservation, ShellStream
from a13n_service.runs.claim import claim
from a13n_service.runs.tool_executions.authority import heartbeat
from a13n_service.runs.tool_executions.output import Snapshot, key
from a13n_service.runs.tool_executions.store import ORIGIN
from redis.asyncio.client import Pipeline
from redis.exceptions import RedisError

pytestmark = pytest.mark.anyio


def _observation(data=b"ready"):
    stream = ShellStream(
        start_offset=0,
        data=data,
        capture=EnvironmentOutputCapture(
            kind="inline",
            captured_bytes=len(data),
            available_end=len(data),
            producer_complete=False,
            content_complete=False,
        ),
    )
    return ShellObservation(
        target=ShellTarget(
            resource_id="process-test",
            backing_identity=None,
            identity=ProcessIdentity(
                provider_type="local", environment_id="env-test", generation="one", process_id="native"
            ),
        ),
        status=ProcessStatus(phase="running"),
        stdin_open=True,
        stdout=stream,
        stderr=stream,
    )


@pytest.mark.parametrize("publish_fails", [False, True])
async def test_cache_coalesces_and_redis_failure_does_not_delay_execution(
    service, scripted_model, runs_kit, monkeypatch, publish_fails
):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    await runs_kit.start_thread(service, agent, "work")
    (lease,) = await claim(service.runtime, worker_id="worker-cache", worker_build="test", limit=1)
    owner = service.runtime.executions
    entered, finish, publishing, publish_allowed = (asyncio.Event() for _ in range(4))

    async def operation():
        owner.observe_resource(_observation())
        entered.set()
        await finish.wait()
        return "terminal result"

    token = ORIGIN.set(lease)
    try:
        record = owner.admit(
            operation, thread_id=lease.thread_id, run_id="harness-test", tool_call_id="call-test", tool_name="test"
        )
    finally:
        ORIGIN.reset(token)
    flush = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        observation = _observation(b"last\xff")
        snapshot = Snapshot(thread_id=lease.thread_id, worker_id=lease.worker_id, observation=observation)
        for _ in range(100):
            owner.output.offer(record.execution_id, snapshot)
        assert len(owner.output._pending) == 1
        await owner.output.flush()
        assert await owner.output.get(lease.thread_id, record.execution_id, lease.worker_id) == observation
        assert 0 < await service.runtime.redis.ttl(key(record.execution_id)) <= 10
        assert await owner.output.get("other-thread", record.execution_id, lease.worker_id) is None
        assert await owner.output.get(lease.thread_id, record.execution_id, "other-worker") is None
        assert not owner.output._pending
        original = Pipeline.execute
        writes = AsyncMock(wraps=original)

        async def counted(pipe, *args, **kwargs):
            return await writes(pipe, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Pipeline, "execute", counted)
            owner.output.offer(record.execution_id, snapshot)
            await owner.output.flush()
            assert writes.await_count == 0
            fingerprint, _ = owner.output._sent[record.execution_id]
            owner.output._sent[record.execution_id] = (fingerprint, asyncio.get_running_loop().time() - 6)
            owner.output.offer(record.execution_id, snapshot)
            await owner.output.flush()
            assert writes.await_count == 1

        async def blocked(pipe, *args, **kwargs):
            publishing.set()
            await publish_allowed.wait()
            if publish_fails:
                raise RedisError("cache unavailable")
            return await original(pipe, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Pipeline, "execute", blocked)
            owner.output.offer(
                record.execution_id, snapshot.model_copy(update={"observation": _observation(b"changed")})
            )
            flush = asyncio.create_task(owner.output.flush())
            await asyncio.wait_for(publishing.wait(), 1)
            # Cache I/O holds neither a DB transaction nor the execution task.
            assert await heartbeat(service.runtime, lease.worker_id) == ()
            finish.set()
            await asyncio.wait_for(owner.join(), 1)
            assert not flush.done()
            assert (await owner.result(lease.thread_id, record.execution_id)).result == "terminal result"
            publish_allowed.set()
            await flush
            assert not owner.output._pending and not owner.output._sent
        # Loss/expiry/malformed cache entries are misses, never terminal evidence.
        await service.runtime.redis.pexpire(key(record.execution_id), 1)
        async with asyncio.timeout(1):
            while await owner.output.get(lease.thread_id, record.execution_id, lease.worker_id) is not None:
                await asyncio.sleep(0.005)
        await service.runtime.redis.set(key(record.execution_id), "broken", ex=10)
        assert await owner.output.get(lease.thread_id, record.execution_id, lease.worker_id) is None
        with monkeypatch.context() as patch:
            patch.setattr(service.runtime.redis, "get", AsyncMock(side_effect=RedisError("offline")))
            assert await owner.output.get(lease.thread_id, record.execution_id, lease.worker_id) is None
        assert (await owner.result(lease.thread_id, record.execution_id)).record.status == "succeeded"
    finally:
        finish.set()
        publish_allowed.set()
        if flush is not None:
            await flush
        await owner.close()
