"""Opt-in real E2B integration; every fixture owns and destroys its sandbox."""

from __future__ import annotations

import asyncio
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from a13n_environment import (
    ArgvCommand,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    E2BEnvironment,
    E2BProviderConfiguration,
    E2BProviderRuntime,
    EnvironmentError,
    EnvironmentOutputPolicy,
    EnvironmentProviderError,
    FileQueryRequest,
    FileTextSearchRequest,
    PortTarget,
    ShellCommand,
)
from pydantic import SecretStr

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not os.environ.get("A13N_TEST_E2B_API_KEY"), reason="Set A13N_TEST_E2B_API_KEY to run live E2B tests"
    ),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass
class SandboxFixture:
    environment: E2BEnvironment
    configuration: E2BProviderConfiguration
    runtime: E2BProviderRuntime

    def fresh(self):
        return E2BEnvironment(
            self.configuration,
            environment_id=self.environment.environment_id,
            state=self.environment.dump_state(),
            runtime=self.runtime,
        )


@pytest.fixture
async def sandbox():
    config = E2BProviderConfiguration(timeout_seconds=300, max_observation_bytes=65536, max_active_observations=4)
    runtime = E2BProviderRuntime(SecretStr(os.environ["A13N_TEST_E2B_API_KEY"]))
    env = E2BEnvironment(config, environment_id="env-test-" + secrets.token_hex(8), state=None, runtime=runtime)
    try:
        await env.enter(
            thread_id="thread-test", run_id="run-test", agent_instance_id="agent-test", mount_id="mount-test"
        )
        await env.ensure_ready(frozenset({"files", "shell", "processes"}))
        yield SandboxFixture(env, config, runtime)
    finally:
        try:
            await env.close()
        finally:
            state = env.dump_state()
            if state is not None:
                cleanup = E2BEnvironment(config, environment_id=env.environment_id, state=state, runtime=runtime)
                await cleanup.destroy()
                probe = E2BEnvironment(config, environment_id=env.environment_id, state=state, runtime=runtime)
                assert await probe.reconcile() == "absent"


def request(script: str, *, maximum=65536, overflow="retain", **kwargs):
    return CommandRequest(
        command=ShellCommand(profile_id="default", script=script),
        output_policy=EnvironmentOutputPolicy(max_inline_bytes=32, max_output_bytes=maximum, overflow=overflow),
        **kwargs,
    )


async def test_files_binary_transfer_patch_search_and_conflicts(sandbox):
    files = sandbox.environment.operations.files
    assert files is not None
    await files.mkdir("/work")
    payload = bytes(range(256)) * 1000

    async def chunks():
        for offset in range(0, len(payload), 17001):
            yield payload[offset : offset + 17001]

    await files.write_bytes_stream("/work/binary", chunks(), mode="create")
    assert await files.read_bytes("/work/binary", offset=250, length=200) == payload[250:450]
    assert b"".join([chunk async for chunk in files.read_bytes_stream("/work/binary", chunk_size=1024)]) == payload
    with pytest.raises(EnvironmentError):
        await files.read_text("/work/binary")
    await files.write_text("/work/text", "first\nsecond\n", mode="create")
    await files.patch_text("/work/text", "@@ -1,2 +1,2 @@\n first\n-second\n+changed\n")
    await files.write_text("/work/text", "third\n", mode="append")
    assert (await files.read_text("/work/text", line_offset=1)).text == "changed\nthird\n"
    with pytest.raises(EnvironmentError) as error:
        await files.write_text("/work/text", "oops", mode="create")
    assert error.value.code == "environment_conflict"
    await files.copy("/work/text", "/work/copy")
    await files.move("/work/copy", "/work/moved")
    result = await files.query(FileQueryRequest(root="/work", pattern="*", max_results=10))
    assert {entry.path for entry in result.entries} == {"/work/binary", "/work/text", "/work/moved"}
    found = await files.search_text(FileTextSearchRequest(root="/work", pattern="changed", max_matches=10))
    assert {match.path for match in found.matches} == {"/work/text", "/work/moved"}
    await files.remove("/work/moved")


async def test_sdk_text_output_and_observation_limit_without_termination(sandbox):
    processes = sandbox.environment.operations.processes
    assert processes is not None
    assert sandbox.environment.operations.outputs is None
    started = await processes.start(
        request("python3 -c 'import os; os.write(1, bytes([104, 101, 108, 108, 111, 255]))'")
    )
    completed = await processes.wait(started.process.handle, condition="initial_terminal", timeout_seconds=15)
    assert completed.status.exit_code == 0
    assert completed.status.cleanup is None
    page = await processes.read_output(completed.handle, policy=request("true").output_policy)
    assert b"".join(part.data for part in page.stdout.chunks) == "hello\ufffd".encode()
    assert page.stdout.capture.origin == "sdk_text"
    assert page.stdout.capture.produced_bytes is None
    await processes.release(completed.handle)
    noisy = await processes.start(request("head -c 200000 /dev/zero; sleep 60"))
    async with asyncio.timeout(15):
        while True:
            page = await processes.read_output(noisy.process.handle, policy=request("true").output_policy)
            if page.stdout.capture.reason == "observation_limit":
                break
            await asyncio.sleep(0.1)
    assert page.stdout.capture.captured_bytes <= sandbox.configuration.max_observation_bytes
    assert (await processes.inspect(noisy.process.handle)).status.phase == "running"
    await processes.kill(noisy.process.handle)


async def test_stdin_and_discovery_after_adapter_close(sandbox):
    processes = sandbox.environment.operations.processes
    assert processes is not None
    started = await processes.start(request("cat", keep_stdin_open=True))
    await processes.write_stdin(started.process.handle, b"hello\n", close_after_write=True)
    result = await processes.wait(started.process.handle, condition="initial_terminal", timeout_seconds=10)
    page = await processes.read_output(result.handle, policy=request("true").output_policy)
    assert b"".join(segment.data for segment in page.stdout.chunks) == b"hello\n"
    long = await processes.start(request("sleep 60"))
    identity = long.process.handle.identity
    await sandbox.environment.close()
    other = sandbox.fresh()
    try:
        await other.enter(thread_id="t", run_id="r", agent_instance_id="a", mount_id="other-mount")
        await other.prepare()
        listed = await other.operations.processes.list(limit=100)
        rebound = next(info for info in listed.processes if info.handle.identity == identity)
        assert rebound.handle.mount_id == "other-mount"
        assert rebound.status.phase == "running"
        await other.operations.processes.kill(rebound.handle)
    finally:
        await other.close()


async def test_lifecycle_pause_resume_and_keepalive(sandbox):
    env = sandbox.environment
    state = env.dump_state()
    await env.close()
    control = sandbox.fresh()
    await control.stop()
    assert await control.reconcile() == "stopped"
    with pytest.raises(EnvironmentProviderError):
        await control.keepalive(deadline=datetime.now(UTC) + timedelta(seconds=30), operation_id="renew-paused")
    assert await control.reconcile() == "stopped"
    resumed = sandbox.fresh()
    try:
        await resumed.prepare()
        assert resumed.dump_state() == state
        assert await resumed.reconcile() == "running"
        assert await resumed.keepalive(
            deadline=datetime.now(UTC) + timedelta(seconds=30), operation_id="renew-running"
        ) >= datetime.now(UTC)
    finally:
        await resumed.close()


async def test_unsupported_guarantees_fail_before_start(sandbox):
    processes = sandbox.environment.operations.processes
    assert processes is not None
    limits = (
        CommandLimits(memory_bytes=1024),
        CommandLimits(process_count=2),
        CommandLimits(cpu_time_seconds=1),
        CommandLimits(wall_time_seconds=1),
        CommandLimits(stdin_bytes=8),
    )
    for limit in limits:
        with pytest.raises(EnvironmentError) as error:
            await processes.start(request("touch /tmp/should-not-run", limits=limit))
        assert error.value.code == "environment_unsupported"
    for command in (
        request("true", network="deny"),
        request("true", environment=CommandEnvironment(unset=("LANG",))),
        CommandRequest(command=ArgvCommand(executable="/usr/bin/true"), output_policy=request("true").output_policy),
    ):
        with pytest.raises(EnvironmentError) as error:
            await processes.start(command)
        assert error.value.code == "environment_unsupported"
    port = await sandbox.environment.operations.ports.inspect(PortTarget(port=54321))
    assert port.status == "not_listening"


async def test_observation_capacity_is_adapter_local(sandbox):
    processes = sandbox.environment.operations.processes
    assert processes is not None
    for _ in range(sandbox.configuration.max_active_observations + 2):
        started = await processes.start(request("true"))
        info = await processes.wait(started.process.handle, condition="initial_terminal", timeout_seconds=10)
        assert info.status.exit_code == 0
    running = []
    other = sandbox.fresh()
    try:
        for _ in range(sandbox.configuration.max_active_observations):
            started = await processes.start(request("sleep 120"))
            running.append(started.process.handle)
        with pytest.raises(EnvironmentError) as error:
            await processes.start(request("true"))
        assert error.value.code == "environment_limit_exceeded"
        await other.prepare()
        result = await other.operations.shell.exec(request("printf independent"))
        assert result.status.exit_code == 0
        assert result.output.stdout.inline == b"independent"
        assert result.output.stdout.reference is None
    finally:
        for handle in running:
            await processes.kill(handle)
        await other.close()
