"""Real cloud effects with explicit local response loss and lifecycle barriers."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from a13n_environment import E2BEnvironment, EnvironmentProviderError
from e2b import AsyncSandbox

from .e2b_support import command

pytestmark = pytest.mark.anyio


async def test_e2b_lost_create_response_recovers_by_metadata(e2b_sandboxes, monkeypatch):
    pool = e2b_sandboxes
    env = pool.adapter()
    create = AsyncSandbox.create
    calls = []

    async def lose_response(**options):
        result = await create(**options)
        calls.append(result.sandbox_id)
        raise TimeoutError("Injected loss after the real E2B create succeeded")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSandbox, "create", lose_response)
        with pytest.raises(EnvironmentProviderError) as caught:
            await env.prepare()
        assert caught.value.certainty.value == "unknown"
        assert env.dump_state() is None
        recovered = pool.fresh(env, state=False)
        assert await recovered.reconcile() == "running"
        await pool.prepare(recovered)
        assert recovered.dump_state().state["sandbox_id"] == calls[0]
        assert [item.sandbox_id for item in await pool.targets(env.environment_id)] == calls
        assert len(calls) == 1
        assert (await recovered.operations.shell.exec(command("printf recovered"))).output.stdout.inline == b"recovered"


async def test_e2b_readiness_failure_keeps_known_create_state(e2b_sandboxes, monkeypatch):
    pool = e2b_sandboxes
    env = pool.adapter()

    async def unavailable(self, sandbox, mount_id):
        raise RuntimeError("Injected operation readiness failure")

    with monkeypatch.context() as patch:
        patch.setattr(E2BEnvironment, "_open_operations", unavailable)
        with pytest.raises(RuntimeError, match="readiness failure"):
            await env.prepare()
    state = env.dump_state()
    assert state is not None
    assert [item.sandbox_id for item in await pool.targets(env.environment_id)] == [state.state["sandbox_id"]]
    recovered = await pool.prepare(pool.fresh(env))
    assert recovered.dump_state() == state
    assert (await recovered.operations.shell.exec(command("printf ready"))).output.stdout.inline == b"ready"


@pytest.mark.parametrize("action,sdk_method", [("stop", "pause"), ("destroy", "kill"), ("keepalive", "set_timeout")])
async def test_e2b_lost_mutation_response_is_reconciled_without_repeating_effect(
    e2b_sandboxes, monkeypatch, action, sdk_method
):
    pool = e2b_sandboxes
    env = await pool.prepare()
    state = env.dump_state()
    target = state.state["sandbox_id"]
    deadline = datetime.now(UTC) + timedelta(seconds=90)
    if action == "keepalive":
        await AsyncSandbox.set_timeout(target, 20, **pool.options)
    original = getattr(AsyncSandbox, sdk_method)
    dispatched = []

    async def lose_response(*args, **options):
        await original(*args, **options)
        dispatched.append(args[0])
        raise TimeoutError("Injected loss after the real E2B mutation succeeded")

    async def perform(control):
        if action == "keepalive":
            return await control.keepalive(deadline=deadline, operation_id="op-repeat-same-renewal")
        return await getattr(control, action)()

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSandbox, sdk_method, lose_response)
        control = pool.fresh(env)
        with pytest.raises(EnvironmentProviderError) as caught:
            await perform(control)
        assert caught.value.code == "provider_unknown_outcome" and caught.value.certainty.value == "unknown"
        assert control.dump_state() == state
        expected = {"stop": "stopped", "destroy": "absent", "keepalive": "running"}[action]
        recovered = pool.fresh(env)
        assert await recovered.reconcile() == expected
        result = await perform(recovered)
        assert dispatched == [target]
        if action == "keepalive":
            assert result >= deadline and (await pool.info(target)).end_at == result
        elif action == "destroy":
            assert recovered.dump_state() is None
        else:
            await pool.state(target, "paused")


@pytest.mark.parametrize("operation", ["prepare", "recover"])
@pytest.mark.parametrize("cancel", [False, True], ids=["complete", "cancel"])
async def test_e2b_close_serializes_with_real_preparation(e2b_sandboxes, monkeypatch, operation, cancel):
    pool = e2b_sandboxes
    env = pool.adapter()
    await env.enter(thread_id="t", run_id="r", agent_instance_id="a", mount_id="m")
    if operation == "recover":
        await env.prepare()
    opened = E2BEnvironment._open_operations
    reached, release = asyncio.Event(), asyncio.Event()

    async def barrier(self, sandbox, mount_id):
        if self is env:
            reached.set()
            await release.wait()
        await opened(self, sandbox, mount_id)

    with monkeypatch.context() as patch:
        patch.setattr(E2BEnvironment, "_open_operations", barrier)
        preparing = asyncio.create_task(getattr(env, operation)())
        closing = None
        try:
            await asyncio.wait_for(reached.wait(), 60)
            closing = asyncio.create_task(env.close())
            await asyncio.sleep(0)
            assert not closing.done(), "close must wait for the preparation owner"
            if cancel:
                preparing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await preparing
            else:
                release.set()
                await preparing
            await closing
        finally:
            release.set()
            await asyncio.gather(preparing, *([closing] if closing else []), return_exceptions=True)
    assert env.availability.status == "unavailable" and env.operations.files is None
    state = env.dump_state()
    assert state is not None
    with pytest.raises(RuntimeError, match="closed"):
        await env.prepare()
    with pytest.raises(RuntimeError):
        await env.recover()
    recovered = await pool.prepare(pool.fresh(env))
    assert recovered.dump_state() == state
    assert [item.sandbox_id for item in await pool.targets(env.environment_id)] == [state.state["sandbox_id"]]
