"""Opt-in real E2B integration; every fixture owns and destroys its sandbox."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from a13n_environment_provider import (
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
    config = E2BProviderConfiguration(
        timeout_seconds=300, max_output_bytes=65536, max_wall_time_seconds=15, max_processes=4
    )
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
            if env.dump_state() is not None:
                cleanup = E2BEnvironment(
                    config, environment_id=env.environment_id, state=env.dump_state(), runtime=runtime
                )
                await cleanup.destroy()


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


async def test_process_bytes_paging_limits_and_release(sandbox):
    env = sandbox.environment
    processes, outputs = env.operations.processes, env.operations.outputs
    assert processes is not None and outputs is not None
    started = await processes.start(
        request("python3 -c 'import os; os.write(1,bytes(range(256))*4); os.write(2,b\"stderr\")'")
    )
    completed = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=15)
    assert completed.status.exit_code == 0
    assert completed.status.cleanup == "residual_confined"
    reference = completed.output.stdout.reference
    assert reference is not None
    page = await outputs.read(
        reference,
        start_offset=250,
        policy=EnvironmentOutputPolicy(max_inline_bytes=100, max_output_bytes=65536, overflow="retain"),
    )
    assert b"".join(part.data for part in page.chunks) == (bytes(range(256)) * 4)[250:350]
    assert page.capture.content_complete
    await processes.release(started.process.handle)
    with pytest.raises(EnvironmentError):
        await processes.rebind(started.process.handle.identity, output_policy=request("true").output_policy)
    await outputs.release(reference=reference)
    assert completed.output.stderr.reference is not None
    await outputs.release(reference=completed.output.stderr.reference)
    with pytest.raises(EnvironmentError):
        await outputs.read(reference, policy=request("true").output_policy)
    result = await processes.exec(request("python3 -c 'import os; os.write(1,b\"x\"*200000)'", maximum=128))
    assert result.status.termination_reason == "output_limit"
    assert result.output.stdout.captured_bytes == 128
    assert not result.output.stdout.content_complete
    result = await processes.exec(request("sleep 10", limits=CommandLimits(wall_time_seconds=0.2)))
    assert result.status.phase == "timed_out"


async def test_stdin_signal_and_reentry(sandbox):
    processes = sandbox.environment.operations.processes
    assert processes is not None
    started = await processes.start(request("cat", keep_stdin_open=True))
    await processes.write_stdin(started.process.handle, b"hello\x00\xff\n", close_after_write=True)
    result = await processes.wait(started.process.handle, condition="initial_terminal", timeout_seconds=10)
    assert b"".join(segment.data for segment in result.output.stdout.preview) == b"hello\x00\xff\n"
    long = await processes.start(request("sleep 10"))
    other = sandbox.fresh()
    try:
        await other.enter(thread_id="t", run_id="r", agent_instance_id="a", mount_id="other-mount")
        await other.prepare()
        rebound = await other.operations.processes.rebind(
            long.process.handle.identity, output_policy=request("true").output_policy
        )
        assert rebound.handle.mount_id == "other-mount"
        await other.operations.processes.signal(rebound.handle, "terminate")
        terminal = await processes.wait(long.process.handle, condition="tree_cleaned", timeout_seconds=10)
        assert terminal.status.phase == "signaled"
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


async def test_unsupported_limits_fail_before_start_and_argv_is_literal(sandbox):
    processes = sandbox.environment.operations.processes
    assert processes is not None
    for limits in (CommandLimits(memory_bytes=1024), CommandLimits(process_count=2), CommandLimits(cpu_time_seconds=1)):
        with pytest.raises(EnvironmentError) as error:
            await processes.start(request("touch /tmp/should-not-run", limits=limits))
        assert error.value.code == "environment_unsupported"
    with pytest.raises(EnvironmentError):
        await processes.start(request("true", network="deny"))
    result = await processes.exec(
        CommandRequest(
            command=ArgvCommand(executable="/usr/bin/printf", arguments=("%s", "$(touch /tmp/should-not-run);literal")),
            output_policy=request("true").output_policy,
            environment=CommandEnvironment(unset=("LANG",)),
        )
    )
    assert b"".join(part.data for part in result.output.stdout.preview).startswith(b"$(touch /tmp/should-not-run)")
    port = await sandbox.environment.operations.ports.inspect(PortTarget(port=54321))
    assert port.status == "not_listening"


async def test_stdin_quota_survives_rebind_and_initial_input(sandbox):
    processes = sandbox.environment.operations.processes
    assert processes is not None
    started = await processes.start(
        request("cat", keep_stdin_open=True, initial_stdin=b"first", limits=CommandLimits(stdin_bytes=8))
    )
    other = sandbox.fresh()
    try:
        await other.prepare()
        rebound = await other.operations.processes.rebind(
            started.process.handle.identity, output_policy=request("true").output_policy
        )
        with pytest.raises(EnvironmentError) as error:
            await other.operations.processes.write_stdin(rebound.handle, b"four")
        assert error.value.code == "environment_too_large"
        await other.operations.processes.write_stdin(rebound.handle, b"end", close_after_write=True)
        result = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=10)
        assert b"".join(part.data for part in result.output.stdout.preview) == b"firstend"
    finally:
        await other.close()


async def test_capacity_includes_retained_records_across_adapters(sandbox):
    environment = sandbox.environment
    processes = environment.operations.processes
    assert processes is not None
    outputs = environment.operations.outputs
    assert outputs is not None
    completed = [await processes.exec(request("true")) for _ in range(sandbox.configuration.max_processes)]
    other = sandbox.fresh()
    try:
        await other.prepare()
        with pytest.raises(EnvironmentError) as error:
            await other.operations.processes.start(request("true"))
        assert error.value.code == "environment_busy"
        first = completed[0]
        assert first.output.stdout.reference is not None and first.output.stderr.reference is not None
        await outputs.release(reference=first.output.stdout.reference)
        await outputs.release(reference=first.output.stderr.reference)
        result = await other.operations.processes.exec(request("printf freed"))
        assert result.status.exit_code == 0
    finally:
        await other.close()
