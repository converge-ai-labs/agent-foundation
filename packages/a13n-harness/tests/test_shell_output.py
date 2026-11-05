"""Final Shell output is paged before release and saved only as Run temporary text."""

import asyncio
import json
import shlex
import sys
from types import SimpleNamespace

import pytest
from a13n_harness import RunBindings
from a13n_harness.environment.commands import ProcessStreamRead
from a13n_harness.environment.retention import (
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    EnvironmentOutputSegment,
)
from a13n_harness.tool_execution import ToolExecutionRuntime
from a13n_harness.toolsets import shell_output
from a13n_harness.toolsets.process_manager import _ProcessController
from a13n_harness.toolsets.shell_output import _RedactedText, offload_stream

from .test_runtime_shell import bindings, executable, source

pytestmark = pytest.mark.anyio


def test_streamed_utf8_and_bearer_redaction_at_every_boundary():
    text = "你好\nBearer abc.def-~+/=\nXBearer visible\nBearer \tsecret! end Bearer"
    data = text.encode()
    expected = "你好\nBearer [REDACTED]\nXBearer visible\nBearer \t[REDACTED]! end Bearer".encode()
    for size in range(1, len(data) + 1):
        redactor = _RedactedText()
        chunks = [redactor.feed(data[start : start + size]) for start in range(0, len(data), size)]
        chunks.append(redactor.feed(b"", final=True))
        assert b"".join(chunks) == expected, size
    redactor = _RedactedText()
    assert redactor.feed(b"Bearer " + b"x" * 65536) == b"Bearer [REDACTED]"
    assert redactor.feed(b"x" * 65536) == b""
    assert redactor.feed(b"!", final=True) == b"!"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fixture")
@pytest.mark.parametrize("padding", [0, 80000])
async def test_shell_redacts_bearer_in_inline_preview_and_saved_text(tmp_path, monkeypatch, padding):
    original = _ProcessController._retire
    expected = b"Bearer [REDACTED]!" + b"x" * padding

    async def retire(controller, entry):
        files = list(tmp_path.rglob("tool-result-*.txt"))
        if padding:
            assert len(files) == 1
            assert files[0].read_bytes() == expected
        else:
            assert files == []
        await original(controller, entry)

    monkeypatch.setattr(_ProcessController, "_retire", retire)
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        f"import sys; sys.stdout.write('Bearer abc.def-~+/=!'+ 'x'*{padding})"
    )
    results = []
    await executable([("shell_exec", {"command": command})], results, mode="inline", wait_seconds=15).run(
        "capture", environment=source(tmp_path), bindings=bindings(None)
    )
    assert results[0]["stdout"]["text"].startswith("Bearer [REDACTED]!")
    assert "abc.def" not in json.dumps(results)
    assert not list(tmp_path.rglob("tool-result-*.txt"))


def page(data, offset, total, *, complete=True):
    return ProcessStreamRead(
        chunks=(EnvironmentOutputSegment(start_offset=offset, data=data),) if data else (),
        next_cursor=None,
        capture=EnvironmentOutputCapture(
            kind="inline",
            inline=data,
            captured_bytes=total,
            available_end=total,
            producer_complete=True,
            content_complete=complete,
        ),
    )


@pytest.mark.parametrize("outcome", ["complete", "limit", "provider", "read", "empty", "write", "cancel"])
async def test_stream_offload_reports_actual_coverage(monkeypatch, outcome):
    monkeypatch.setattr(shell_output, "SHELL_OUTPUT_BYTES", 24 if outcome == "limit" else 100)
    data = b"a" * 20 + b"TAIL"
    written = bytearray()
    offsets = []

    async def write(chunks):
        async for chunk in chunks:
            written.extend(chunk)
            if outcome == "write":
                return None
        return "/tmp/log.txt"

    async def read(offset):
        offsets.append(offset)
        if outcome == "read":
            raise OSError("read unavailable")
        if outcome == "cancel":
            raise asyncio.CancelledError
        return page(
            b"" if outcome == "empty" else data[offset : offset + 10],
            offset,
            len(data) + (1 if outcome == "limit" else 0),
            complete=outcome != "provider",
        )

    initial = page(data[:10], 0, len(data) + (1 if outcome == "limit" else 0))
    context = SimpleNamespace(_spill_shell_output=write)
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await offload_stream(context, initial, read)
        return
    saved = await offload_stream(context, initial, read)
    expected = {
        "complete": None,
        "limit": "output_limit",
        "provider": "provider_incomplete",
        "read": "read_failed",
        "empty": "source_gap",
        "write": "write_unavailable",
    }
    assert saved["reason"] == expected[outcome]
    assert saved["content_complete"] is (outcome == "complete")
    if outcome == "write":
        assert saved["path"] is None and saved["bytes"] == 0
    else:
        assert saved["path"] == "/tmp/log.txt"
        assert saved["bytes"] == len(written)
        assert written == (data[:10] if outcome in {"read", "empty"} else data)
    assert offsets == sorted(set(offsets))


@pytest.mark.parametrize("failed_stream", ["stdout", "stderr"])
async def test_spool_commit_failure_closes_output_observation(tmp_path, monkeypatch, failed_stream):
    from a13n_harness.environment.models import EnvironmentError
    from a13n_harness.providers.environment._local_retention import LocalRetentionWriter

    from .providers_environment.test_direct_local_processes import OUTPUT, _processes, _request

    original = LocalRetentionWriter.commit
    commits = 0

    async def commit(writer, **kwargs):
        nonlocal commits
        commits += 1
        if commits == (1 if failed_stream == "stdout" else 2):
            raise EnvironmentError("spool commit failed", code="environment_provider_failure")
        return await original(writer, **kwargs)

    monkeypatch.setattr(LocalRetentionWriter, "commit", commit)
    async with _processes(tmp_path) as processes:
        started = await processes.start(
            _request("import sys; sys.stdout.write('o'*100000); sys.stderr.write('e'*100000)")
        )
        # The observer is already waiting when publication fails.
        result = await processes.read_output(started.process.handle, policy=OUTPUT, wait_seconds=5)
        failed = result.stdout if failed_stream == "stdout" else result.stderr
        healthy = result.stderr if failed_stream == "stdout" else result.stdout
        assert failed.capture.observation_closed
        assert not failed.capture.content_complete
        assert failed.capture.reference is None
        assert failed.capture.reason == "connection_lost"
        assert healthy.capture.observation_closed
        assert healthy.capture.reason is None
        assert healthy.capture.available_end == 65536  # The fixture's native retention cap.
        assert healthy.capture.produced_bytes == 100000
        assert healthy.capture.reference is not None
        status = (await processes.inspect(started.process.handle)).status
        assert status.phase == "exited"
        assert status.exit_code == 0
        assert status.cleanup == "complete"


@pytest.mark.parametrize("complete", [True, False])
async def test_offload_at_provider_clamped_limit(monkeypatch, complete):
    monkeypatch.setattr(shell_output, "SHELL_OUTPUT_BYTES", 24)
    written = bytearray()

    async def write(chunks):
        async for chunk in chunks:
            written.extend(chunk)
        return "/tmp/clamped.txt"

    async def read(offset):
        pytest.fail(f"Unexpected read past closed capture: {offset}")

    saved = await offload_stream(
        SimpleNamespace(_spill_shell_output=write), page(b"x" * 24, 0, 24, complete=complete), read
    )
    assert saved["content_complete"] is complete
    assert saved["reason"] == (None if complete else "output_limit")
    assert saved["bytes"] == len(written) == 24


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fixture")
@pytest.mark.parametrize("owned", [True, False])
@pytest.mark.parametrize("operation", ["write", "commit"])
@pytest.mark.parametrize("exit_code", [0, 7])
async def test_shell_waiting_on_spool_failure_preserves_exit(tmp_path, monkeypatch, owned, operation, exit_code):
    from a13n_harness.environment.models import EnvironmentError
    from a13n_harness.providers.environment._local_retention import LocalRetentionWriter

    failures = 0

    async def fail(*args, **kwargs):
        nonlocal failures
        failures += 1
        if operation == "write":
            raise OSError("spool write failed")
        raise EnvironmentError("spool commit failed", code="environment_provider_failure")

    monkeypatch.setattr(LocalRetentionWriter, operation, fail)
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        f"import sys; sys.stdout.write('x'*2097152); sys.stderr.write('healthy'); sys.exit({exit_code})"
    )
    runtime = ToolExecutionRuntime() if owned else None
    results = []
    try:
        await executable([("shell_exec", {"command": command})], results, mode="inline", wait_seconds=15).run(
            "capture", environment=source(tmp_path), bindings=bindings(runtime)
        )
        result = results[0]
        assert failures
        assert result["ok"], result
        assert result["status"]["phase"] == "exited"
        assert result["status"]["exit_code"] == exit_code
        assert result["status"]["cleanup"] == "complete"
        assert result["stdout"]["observation_closed"]
        assert result["stdout"]["produced_bytes"] == 2097152
        assert not result["stdout"]["offload"]["content_complete"]
        assert result["stdout"]["offload"]["reason"] == "connection_lost"
        assert result["stderr"]["text"] == "healthy"
        assert result["stderr"]["content_complete"]
    finally:
        if runtime is not None:
            await runtime.close()


async def test_eip_retained_output_offloads_all_pages_beyond_one_mebibyte():
    from a13n_envd_client.eip import v1 as eip
    from a13n_harness.environment.commands import ArgvCommand, CommandRequest
    from a13n_harness.providers.environment.eip._common import encode_bytes
    from a13n_harness.providers.environment.eip.output import EIPOutputRegistry
    from a13n_harness.toolsets.process_manager import _retain_shell_output

    data = b"x" * (2 * 1024 * 1024) + b"REMOTE-TAIL"
    output = eip.OutputInfo(
        reference=eip.OutputReference("out-remote"),
        producer_complete=True,
        content_complete=True,
        produced_bytes=len(data),
        retained_bytes=len(data),
        preview=encode_bytes(data[:4096]),
    )
    offsets = []

    def open_output(reference, *, start_offset, observed):
        assert reference == output.reference and observed == output
        offsets.append(start_offset)

        async def read_page(*, wait_ms):
            assert wait_ms == 0
            return SimpleNamespace(
                output=output, start_offset=start_offset, data=data[start_offset : start_offset + 8192]
            )

        return SimpleNamespace(read_page=read_page)

    registry = EIPOutputRegistry(
        session=SimpleNamespace(open_output=open_output),
        environment_id="env-one",
        execution_id="exec-one",
        generation="generation-one",
    )
    admission = _retain_shell_output(
        CommandRequest(
            command=ArgvCommand(executable="python"),
            output_policy=EnvironmentOutputPolicy(
                max_inline_bytes=4096, max_output_bytes=1024 * 1024, overflow="truncate"
            ),
        )
    )
    capture = registry.capture(output, policy=admission.output_policy)
    assert capture.reference is not None

    async def read(offset):
        result = await registry.read(capture.reference, start_offset=offset, policy=admission.output_policy)
        return ProcessStreamRead(chunks=result.chunks, next_cursor=result.next_cursor, capture=result.capture)

    written = bytearray()

    async def write(chunks):
        async for chunk in chunks:
            written.extend(chunk)
        return "/tmp/remote.txt"

    saved = await offload_stream(SimpleNamespace(_spill_shell_output=write), await read(0), read)
    assert saved["content_complete"] and saved["reason"] is None
    assert saved["bytes"] == len(data)
    assert written == data
    assert offsets == list(range(0, len(data), 8192))


@pytest.mark.parametrize("directory", ["relative", "/tmp/../logs", "/tmp/"])
def test_shell_output_directory_requires_canonical_path(directory):
    with pytest.raises(ValueError, match="shell_output_directory"):
        RunBindings.embedded(shell_output_directory=directory)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fixture")
@pytest.mark.parametrize("owned", [True, False])
@pytest.mark.parametrize("sink", ["default", "explicit", "unavailable"])
async def test_large_shell_saves_both_tails_before_release_and_run_cleanup(tmp_path, monkeypatch, owned, sink):
    original = _ProcessController._retire
    observed = []
    size = 2 * 1024 * 1024

    async def retire(controller, entry):
        files = sorted(tmp_path.rglob("tool-result-*.txt"))
        if sink == "unavailable":
            assert files == []
        else:
            assert len(files) == 2
            contents = [path.read_bytes() for path in files]
            assert contents == [b"o" * size + b"OUT-TAIL", b"e" * size + b"ERR-TAIL"]
        observed.append(True)
        await original(controller, entry)

    monkeypatch.setattr(_ProcessController, "_retire", retire)
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        f"import sys; sys.stdout.write('o'*{size}+'OUT-TAIL'); sys.stderr.write('e'*{size}+'ERR-TAIL')"
    )
    runtime = ToolExecutionRuntime() if owned else None
    results = []
    options = (
        {}
        if sink == "default"
        else {"shell_output_directory": "/environment/workspace/logs" if sink == "explicit" else "/missing/logs"}
    )
    try:
        run = await executable([("shell_exec", {"command": command})], results, mode="inline", wait_seconds=15).run(
            "capture", environment=source(tmp_path), bindings=bindings(runtime, **options)
        )
        result = results[0]
        assert result["status"]["exit_code"] == 0, result
        assert observed
        assert len(json.dumps(result)) < 20000
        for name in ("stdout", "stderr"):
            stream = result[name]
            assert not stream["content_complete"]
            saved = stream["offload"]
            if sink == "unavailable":
                assert saved["path"] is None
                assert saved["reason"] == "write_unavailable"
            else:
                assert saved["content_complete"], saved
                assert saved["bytes"] == size + 8
                assert saved["path"].startswith(
                    "/environment/workspace/" + ("logs/" if sink == "explicit" else ".a13n/tmp/tool-results/")
                )
        assert not list(tmp_path.rglob("tool-result-*.txt"))
        if runtime is not None:
            records = await runtime.summarize(run.thread_id)
            stored = await runtime.wait(run.thread_id, records[0].execution_id)
            assert stored.artifacts == ()
    finally:
        if runtime is not None:
            await runtime.close()
