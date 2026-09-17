"""Opt-in integration against a real Engine, including the Compose DinD socket."""

import asyncio
import os
from uuid import uuid4

import docker
import pytest
from a13n_environment import DockerEnvironment, DockerProviderConfiguration, DockerProviderRuntime, DockerSDKEngine
from a13n_environment.commands import CommandRequest, ShellCommand
from a13n_environment.retention import EnvironmentOutputPolicy

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def live_opt_in(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for real native Docker integration")


OUTPUT = EnvironmentOutputPolicy(max_inline_bytes=1024, max_output_bytes=65536, overflow="retain")


def command(script, **kwargs):
    return CommandRequest(command=ShellCommand(profile_id="default", script=script), output_policy=OUTPUT, **kwargs)


@pytest.fixture
async def native_environment():
    engine = DockerSDKEngine(docker.from_env())
    config = DockerProviderConfiguration(
        image=os.environ.get("A13N_TEST_DOCKER_IMAGE", "a13n-docker-environment:local"),
        pull_policy="never",
        init_script="echo initialized >> /workspace/init-count",
        disable_network=True,
        memory_mib=256,
        pids_limit=128,
    )
    env = DockerEnvironment(config, "env_" + uuid4().hex, None, DockerProviderRuntime(engine))
    try:
        await env.prepare()
        yield env
    finally:
        cleanup = DockerEnvironment(env.config, env.environment_id, env.dump_state(), env.runtime)
        await cleanup.destroy()
        await env.close()
        await engine.close()


async def test_native_files_commands_streams_and_retained_output(native_environment):
    env = native_environment
    files = env.operations.files
    await files.write_text("/workspace/hello", "hello\n", mode="create")
    assert (await files.read_text("/workspace/hello")).text == "hello\n"
    result = await env.operations.shell.exec(command("pwd; cat hello; echo diagnostic >&2"))
    assert result.status.exit_code == 0
    assert result.output.stdout.inline == b"/workspace\nhello\n"
    assert result.output.stderr.inline == b"diagnostic\n"
    result = await env.operations.shell.exec(command("python3 -c 'print(\"x\"*8192)'"))
    assert result.output.stdout.kind == "retained"
    capture = await env.operations.outputs.read(result.output.stdout.reference, policy=OUTPUT)
    assert len(capture.chunks[0].data) == 1024
    started = await env.operations.processes.start(command("cat", keep_stdin_open=True))
    await env.operations.processes.write_stdin(started.process.handle, b"input\n", close_after_write=True)
    info = await env.operations.processes.wait(started.process.handle, condition="initial_terminal", timeout_seconds=5)
    assert info.status.exit_code == 0
    output = await env.operations.processes.read_output(started.process.handle, policy=OUTPUT, wait_seconds=0.1)
    assert output.stdout.capture.inline == b"input\n"
    await env.operations.processes.release(started.process.handle)


async def test_targeted_cancel_background_survival_and_stop_resume(native_environment):
    env = native_environment
    first = await env.operations.processes.start(command("sleep 120"))
    second = await env.operations.processes.start(command("sleep 120"))
    await env.operations.processes.kill(first.process.handle)
    info = await env.operations.processes.wait(first.process.handle, condition="initial_terminal", timeout_seconds=5)
    assert info.status.phase == "exited"
    assert (await env.operations.processes.inspect(second.process.handle)).status.phase == "running"
    state = env.dump_state()
    await env.close()
    fresh = DockerEnvironment(env.config, env.environment_id, state, env.runtime)
    try:
        await fresh.prepare()
        info = await fresh.operations.processes.rebind(second.process.handle.identity, output_policy=OUTPUT)
        assert info.status.phase == "running"
        assert (await fresh.operations.shell.exec(command("cat init-count"))).output.stdout.inline == b"initialized\n"
        await fresh.stop()
        assert await fresh.reconcile() == "stopped"
    finally:
        await fresh.close()
    resumed = DockerEnvironment(env.config, env.environment_id, state, env.runtime)
    try:
        await resumed.prepare()
        assert (await resumed.operations.shell.exec(command("cat init-count"))).output.stdout.inline == b"initialized\n"
        container = env.runtime.engine.client.containers.get(state.state["container_id"])
        assert container.attrs["HostConfig"]["NetworkMode"] == "none"
        assert container.attrs["HostConfig"]["Memory"] == 256 * 1024 * 1024
    finally:
        await resumed.close()


async def test_confirmed_container_loss_rebuilds_without_private_files(native_environment):
    env = native_environment
    await env.operations.shell.exec(command("echo old > old-file"))
    old = env.dump_state()
    container = env.runtime.engine.client.containers.get(old.state["container_id"])
    await asyncio.to_thread(container.remove, force=True)
    await env.close()
    fresh = DockerEnvironment(env.config, env.environment_id, old, env.runtime)
    try:
        await fresh.prepare()
        assert fresh.descriptor.generation != old.state["container_id"]
        assert (
            await fresh.operations.shell.exec(command("test ! -e old-file; cat init-count"))
        ).output.stdout.inline == b"initialized\n"
    finally:
        await fresh.destroy()
        await fresh.close()


async def test_native_helper_deadline_and_closed_facets(native_environment):
    from a13n_environment import EnvironmentError, EnvironmentProviderError

    env = native_environment
    files = env.operations.files
    env.commands.config = env.config.model_copy(update={"request_timeout_seconds": 1})
    with pytest.raises(EnvironmentProviderError) as failure:
        await env.commands.execute(["/bin/sh", "-c", "sleep 30"])
    assert failure.value.certainty == "unknown"
    await env.close()
    with pytest.raises(EnvironmentError) as failure:
        await files.read_bytes("/workspace/init-count")
    assert failure.value.code == "environment_unavailable"


async def test_initialization_failure_is_not_implicitly_replayed(native_environment):
    from a13n_environment import EnvironmentError

    parent = native_environment
    config = parent.config.model_copy(update={"init_script": "echo attempted >> /workspace/attempts; exit 1"})
    env = DockerEnvironment(config, "env_" + uuid4().hex, None, parent.runtime)
    try:
        with pytest.raises(EnvironmentError):
            await env.prepare()
        state = env.dump_state()
        await env.close()
        retry = DockerEnvironment(config, env.environment_id, state, parent.runtime)
        try:
            with pytest.raises(EnvironmentError):
                await retry.prepare()
            native = parent.runtime.engine.client.containers.get(state.state["container_id"])
            assert (await asyncio.to_thread(native.exec_run, ["cat", "/workspace/attempts"])).output == b"attempted\n"
        finally:
            await retry.close()
    finally:
        cleanup = DockerEnvironment(config, env.environment_id, env.dump_state(), parent.runtime)
        await cleanup.destroy()


async def test_host_mounts_survive_destroy_and_enforce_read_only(native_environment, tmp_path):
    from a13n_environment import EnvironmentError
    from a13n_environment.docker import DockerMountConfiguration

    parent = native_environment
    source = tmp_path / "source"
    source.mkdir(mode=0o777)
    source.chmod(0o777)
    (source / "reference").write_text("original")
    config = parent.config.model_copy(
        update={
            "mounts": (
                DockerMountConfiguration(source=str(source), target="/reference", read_only=True),
                DockerMountConfiguration(source=str(source), target="/shared", read_only=False),
            )
        }
    )
    env = DockerEnvironment(config, "env_" + uuid4().hex, None, parent.runtime)
    try:
        await env.prepare()
        assert await env.operations.files.read_bytes("/reference/reference") == b"original"
        with pytest.raises(EnvironmentError):
            await env.operations.files.write_text("/reference/reference", "denied", mode="replace")
        await env.operations.files.write_text("/shared/created", "persisted", mode="create")
    finally:
        await env.destroy()
        await env.close()
    assert (source / "created").read_text() == "persisted"
    assert (source / "reference").read_text() == "original"


async def test_shell_cancellation_preserves_concurrent_command(native_environment):
    env = native_environment
    survivor = await env.operations.processes.start(command("sleep 60"))
    task = asyncio.create_task(env.operations.shell.exec(command("echo started > cancel-started; sleep 60")))
    for _ in range(100):
        result = await env.operations.shell.exec(command("test -e cancel-started"))
        if result.status.exit_code == 0:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("Command did not start")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await env.operations.processes.inspect(survivor.process.handle)).status.phase == "running"
    await env.operations.processes.kill(survivor.process.handle)


async def test_docker_default_working_directory_through_harness(native_environment):
    from a13n_harness import AgentSpec, HarnessBuilder
    from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration, EnvironmentMount
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    env = native_environment
    assert EnvironmentMount(env).working_directory == "/workspace"
    assert EnvironmentMount(env, working_directory="/").working_directory == "/"
    outcomes = []

    async def stream(messages, info):
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            outcomes.extend(returns)
            yield "done"
        else:
            yield {
                0: DeltaToolCall(
                    name="shell_exec", json_args='{"command":"pwd","yield_time_seconds":5}', tool_call_id="cwd"
                )
            }

    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),),
    )
    await agent.run("Print the working directory", environment=env)
    assert outcomes[-1]["ok"] is True, outcomes
    assert "/workspace" in str(outcomes[-1]), outcomes


async def test_full_filesystem_preserves_destination_and_removes_staging(monkeypatch):
    from a13n_environment import EnvironmentError

    engine = DockerSDKEngine(docker.from_env())
    original_create = engine.client.containers.create

    def limited_container(_collection, *args, **kwargs):
        return original_create(*args, **kwargs, tmpfs={"/limited": "size=1m,mode=1777"})

    monkeypatch.setattr(type(engine.client.containers), "create", limited_container)
    env = DockerEnvironment(
        DockerProviderConfiguration(
            image=os.environ.get("A13N_TEST_DOCKER_IMAGE", "a13n-docker-environment:local"), pull_policy="never"
        ),
        "env_" + uuid4().hex,
        None,
        DockerProviderRuntime(engine),
    )
    try:
        await env.prepare()
        files = env.operations.files
        await files.write_text("/limited/destination", "ORIGINAL", mode="create")
        with pytest.raises(EnvironmentError):
            await files.write_text("/limited/destination", "x" * (2 * 1024 * 1024), mode="replace")
        assert await files.read_bytes("/limited/destination") == b"ORIGINAL"
        listing = await env.operations.shell.exec(command("ls -A /limited"))
        assert listing.output.stdout.inline == b"destination\n"
        await files.write_text("/limited/destination", "RECOVERED", mode="replace")
        assert await files.read_bytes("/limited/destination") == b"RECOVERED"
    finally:
        await env.destroy()
        await env.close()
        await engine.close()
