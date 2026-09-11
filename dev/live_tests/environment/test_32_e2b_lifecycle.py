"""Real Sandbox SDK effects, observed independently of Environment state."""

import asyncio
import shlex
from datetime import UTC, datetime, timedelta

import pytest
from a13n_environment import EnvironmentError, EnvironmentProviderError
from e2b import AsyncSandbox

from .e2b_support import OUTPUT, command, eventually, past

pytestmark = pytest.mark.anyio


def native_id(environment):
    return environment.dump_state().state["sandbox_id"]


def stdout(page):
    return b"".join(chunk.data for chunk in page.stdout.chunks)


async def test_e2b_inert_entry_concurrent_prepare_and_exact_destroy(e2b_sandboxes):
    pool = e2b_sandboxes
    unused = pool.adapter()
    await unused.enter(thread_id="t", run_id="r", agent_instance_id="a", mount_id="m")
    assert unused.dump_state() is None
    await unused.close()
    await unused.close()
    assert await pool.targets(unused.environment_id) == []
    with pytest.raises(RuntimeError, match="closed"):
        await unused.prepare()

    env = pool.adapter()
    await asyncio.gather(env.prepare(), env.prepare(), env.prepare())
    target = native_id(env)
    generation = env.descriptor.generation
    assert [item.sandbox_id for item in await pool.targets(env.environment_id)] == [target]
    await env.prepare()
    assert native_id(env) == target and env.descriptor.generation == generation
    assert await pool.fresh(env).reconcile() == "running"
    bystander = await pool.prepare()
    await pool.fresh(env).destroy()
    await pool.state(target, "absent")
    await pool.fresh(env).destroy()
    assert await pool.fresh(env).reconcile() == "absent"
    assert await pool.fresh(bystander).reconcile() == "running"


async def test_e2b_keepalive_extends_real_expiry_without_shortening(e2b_sandboxes):
    pool = e2b_sandboxes
    env = await pool.prepare()
    target = native_id(env)
    await AsyncSandbox.set_timeout(target, 20, **pool.options)
    before = await pool.info(target)
    deadline = datetime.now(UTC) + timedelta(seconds=90)
    control = pool.fresh(env)
    renewed = await control.keepalive(deadline=deadline, operation_id="op-renew-real")
    after = await pool.info(target)
    assert after.end_at > before.end_at and after.end_at >= deadline and renewed == after.end_at
    assert (
        await control.keepalive(deadline=deadline - timedelta(seconds=30), operation_id="op-no-shortening") == renewed
    )
    assert (await pool.info(target)).end_at == renewed
    await past(before.end_at + timedelta(seconds=1))
    await env.check_ready(frozenset({"shell"}))
    result = await env.operations.shell.exec(command("printf alive-after-original-expiry"))
    assert result.output.stdout.inline == b"alive-after-original-expiry"


async def test_e2b_pause_resumes_the_same_process_memory(e2b_sandboxes):
    pool = e2b_sandboxes
    env = await pool.prepare()
    script = "import secrets; value=secrets.token_hex(16); print(value, flush=True); input(); print(value, flush=True)"
    started = await env.operations.processes.start(
        command("python3 -u -c " + shlex.quote(script), keep_stdin_open=True)
    )
    handle = started.process.handle
    first = await eventually(
        lambda: env.operations.processes.read_output(handle, policy=OUTPUT),
        lambda page: bool(stdout(page).strip()),
        "Process published its in-memory value",
    )
    memory = stdout(first).strip()
    assert len(memory) == 32
    target, generation = native_id(env), env.descriptor.generation
    await env.close()
    await pool.fresh(env).stop()
    await pool.state(target, "paused")
    resumed = await pool.prepare(pool.fresh(env))
    assert native_id(resumed) == target and resumed.descriptor.generation == generation
    rebound = await resumed.operations.processes.rebind(handle.identity, output_policy=OUTPUT)
    assert rebound.status.phase == "running"
    # Attach before supplying input: a fresh observer is not guaranteed historical output.
    await resumed.operations.processes.read_output(rebound.handle, policy=OUTPUT)
    await resumed.operations.processes.write_stdin(rebound.handle, b"continue\n")
    completed = await resumed.operations.processes.wait(
        rebound.handle, condition="initial_terminal", timeout_seconds=20
    )
    assert completed.status.exit_code == 0
    result = await resumed.operations.processes.read_output(rebound.handle, policy=OUTPUT)
    assert memory in stdout(result)


async def test_e2b_paused_observation_and_destruction_never_resume(e2b_sandboxes):
    pool = e2b_sandboxes
    env = await pool.prepare()
    target = native_id(env)
    await pool.fresh(env).stop()
    await pool.state(target, "paused")
    for control in (pool.fresh(env), pool.fresh(env, state=False)):
        assert await control.reconcile() == "stopped"
        assert native_id(control) == target
        await control.stop()
        with pytest.raises(EnvironmentProviderError) as caught:
            await control.keepalive(deadline=datetime.now(UTC) + timedelta(seconds=30), operation_id="op-paused")
        assert caught.value.code == "provider_target_stopped"
        await pool.state(target, "paused")
    await pool.fresh(env).destroy()
    await pool.state(target, "absent")


async def test_e2b_close_fences_old_facets_without_killing_native_process(e2b_sandboxes):
    pool = e2b_sandboxes
    env = await pool.prepare()
    files = env.operations.files
    await files.write_text("/proof", "retained", mode="create")
    started = await env.operations.processes.start(command("cat", keep_stdin_open=True))
    target = native_id(env)
    await env.close()
    await env.close()
    with pytest.raises(EnvironmentError) as caught:
        await files.stat("/proof")
    assert caught.value.code == "environment_closed"
    with pytest.raises(RuntimeError, match="entered"):
        await env.check_ready(frozenset({"files"}))
    await pool.state(target, "running")
    other = await pool.prepare(pool.fresh(env))
    assert (await other.operations.files.read_text("/proof")).text == "retained"
    rebound = await other.operations.processes.rebind(started.process.handle.identity, output_policy=OUTPUT)
    assert rebound.status.phase == "running"
    await other.operations.processes.kill(rebound.handle)


async def test_e2b_same_adapter_recovery_preserves_observation_offsets(e2b_sandboxes, monkeypatch):
    pool = e2b_sandboxes
    env = await pool.prepare()
    started = await env.operations.processes.start(
        command("printf before; read line; printf after", keep_stdin_open=True)
    )
    handle = started.process.handle
    page = await eventually(
        lambda: env.operations.processes.read_output(handle, policy=OUTPUT),
        lambda page: stdout(page) == b"before",
        "Initial output before reconnect",
    )
    offset = len(stdout(page))
    target, generation = native_id(env), env.descriptor.generation

    async def unavailable(*args, **kwargs):
        return False

    # Only the readiness response is injected; reconnection and command I/O are real.
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSandbox, "is_running", unavailable)
        with pytest.raises(EnvironmentError) as caught:
            await env.check_ready(frozenset({"processes"}))
        assert caught.value.code == "environment_unavailable"
        assert native_id(env) == target
    await env.recover()
    await env.check_ready(frozenset({"processes"}))
    assert env.descriptor.generation == generation and native_id(env) == target
    retained = await env.operations.processes.read_output(handle, policy=OUTPUT)
    assert stdout(retained).startswith(b"before")
    await env.operations.processes.write_stdin(handle, b"continue\n")
    completed = await env.operations.processes.wait(handle, condition="initial_terminal", timeout_seconds=20)
    assert completed.status.exit_code == 0
    tail = await env.operations.processes.read_output(handle, stdout_start_offset=offset, policy=OUTPUT)
    assert b"after" in stdout(tail)
    assert [item.sandbox_id for item in await pool.targets(env.environment_id)] == [target]


@pytest.mark.parametrize("managed", [True, False], ids=["managed", "external"])
async def test_e2b_external_deletion_rebuilds_only_managed_targets(e2b_sandboxes, managed):
    pool = e2b_sandboxes
    original = await pool.prepare()
    await original.operations.files.write_text("/proof", "old", mode="create")
    started = await original.operations.processes.start(command("sleep 120"))
    old, generation = native_id(original), original.descriptor.generation
    await AsyncSandbox.kill(old, **pool.options)
    await pool.state(old, "absent")
    replacement = pool.fresh(original, managed=managed)
    if not managed:
        with pytest.raises(EnvironmentProviderError) as caught:
            await replacement.prepare()
        assert caught.value.code == "provider_target_missing"
        assert await pool.targets(original.environment_id) == []
        return
    await pool.prepare(replacement)
    assert native_id(replacement) != old and replacement.descriptor.generation != generation
    with pytest.raises(EnvironmentError) as caught:
        await replacement.operations.files.stat("/proof")
    assert caught.value.code == "environment_not_found"
    with pytest.raises(EnvironmentError) as caught:
        await replacement.operations.processes.rebind(started.process.handle.identity, output_policy=OUTPUT)
    assert caught.value.code == "environment_stale_mount"


@pytest.mark.parametrize("paused", [False, True], ids=["running", "paused"])
async def test_e2b_missing_state_recovers_unique_metadata_without_creating(e2b_sandboxes, paused):
    pool = e2b_sandboxes
    env = await pool.prepare()
    target = native_id(env)
    if paused:
        await pool.fresh(env).stop()
    recovered = pool.fresh(env, state=False)
    assert recovered.dump_state() is None
    assert await recovered.reconcile() == ("stopped" if paused else "running")
    assert recovered.dump_state() == env.dump_state()
    await pool.state(target, "paused" if paused else "running")
    await recovered.prepare()
    assert native_id(recovered) == target
    assert [item.sandbox_id for item in await pool.targets(env.environment_id)] == [target]


@pytest.mark.parametrize("ambiguous", [False, True], ids=["wrong-owner", "multiple-matches"])
async def test_e2b_metadata_conflicts_have_no_target_mutations(e2b_sandboxes, ambiguous):
    pool = e2b_sandboxes
    env = pool.adapter()
    first = await pool.duplicate(env, compatible=ambiguous)
    if ambiguous:
        await pool.duplicate(env)
    before = {item.sandbox_id for item in await pool.targets(env.environment_id)}
    for action in ("prepare", "reconcile", "stop", "destroy", "keepalive"):
        control = pool.fresh(env, state=False)
        with pytest.raises(EnvironmentProviderError) as caught:
            if action == "keepalive":
                await control.keepalive(deadline=datetime.now(UTC) + timedelta(seconds=30), operation_id="op-conflict")
            else:
                await getattr(control, action)()
        assert caught.value.code == "provider_target_conflict"
        assert {item.sandbox_id for item in await pool.targets(env.environment_id)} == before
        assert (await pool.info(first.sandbox_id)).state.value == "running"


async def test_e2b_native_timeout_kills_without_automatic_resume(e2b_sandboxes):
    pool = e2b_sandboxes
    env = await pool.prepare()
    target = native_id(env)
    await AsyncSandbox.set_timeout(target, 5, **pool.options)
    await pool.state(target, "absent")
    assert await pool.fresh(env).reconcile() == "absent"
    with pytest.raises((EnvironmentError, EnvironmentProviderError)):
        await env.check_ready(frozenset({"files"}))
    assert await pool.targets(env.environment_id) == []
    replacement = await pool.prepare(pool.fresh(env))
    assert native_id(replacement) != target
