from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from a13n_envd_client import (
    EIPDeviceConnection,
    EIPMethodError,
    EIPSessionStateError,
    EIPTransportClosedError,
    RequestCoordinator,
    StdioTransport,
)
from a13n_envd_client.eip.v1 import (
    ArgvCommand,
    CommandEnvironment,
    CommandRequest,
    DesiredPortStatus,
    DeviceDescribeParams,
    EIPCallContext,
    EIPClient,
    EIPClientInfo,
    EIPPath,
    EncodedBytes,
    EnvironmentDescribeParams,
    EnvironmentDescribeResult,
    ErrorType,
    ExecutableName,
    FileFindParams,
    FileKind,
    FileListParams,
    FileReadTextParams,
    FileSearchParams,
    FileStatParams,
    FileWriteMode,
    FileWriteTextParams,
    InitializeParams,
    MethodSpec,
    OperationCancelParams,
    OperationCancelStatus,
    OutputReadParams,
    OutputReleaseParams,
    PortAddress,
    PortInspectParams,
    PortStatus,
    PortTarget,
    PortWaitParams,
    ProcessCloseStdinParams,
    ProcessInspectParams,
    ProcessKillParams,
    ProcessReleaseParams,
    ProcessSignalParams,
    ProcessStartParams,
    ProcessWaitCondition,
    ProcessWaitParams,
    ProcessWriteStdinParams,
    ReceiptGetParams,
    RequestedProcessSignal,
    SearchMode,
    ShellExecParams,
)

_OWNED_RUNTIME_DIRECTORIES: dict[int, Path] = {}


def a13n_envd_binary() -> Path:
    configured = os.environ.get("A13N_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("set A13N_ENVD_TEST_BINARY to run Rust daemon E2E tests")
    binary = Path(configured)
    assert binary.is_file(), f"a13n-envd test binary does not exist: {binary}"
    return binary


async def start_daemon(
    binary: Path,
    device_id: str = "env-e2e",
    config_path: Path | None = None,
    runtime_dir: Path | None = None,
    startup_arguments: tuple[str, ...] = (),
) -> asyncio.subprocess.Process:
    arguments = [str(binary), *startup_arguments]
    if config_path is not None:
        arguments.extend(("--config", str(config_path)))
    environment = {
        "A13N_ENVD_DEVICE_ID": device_id,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HTTP_PROXY": "http://proxy.invalid:8080",
    }
    if sys.platform == "win32":
        # Windows process startup requires its trusted system directory even
        # when the rest of the test environment is intentionally isolated.
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    owned_runtime = runtime_dir is None
    if runtime_dir is None:
        runtime_dir = Path(tempfile.mkdtemp(prefix="a13n-envd-e2e-"))
    environment["A13N_ENVD_RUNTIME_DIR"] = str(runtime_dir)
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    if owned_runtime:
        _OWNED_RUNTIME_DIRECTORIES[id(process)] = runtime_dir
    return process


def cleanup_owned_runtime(process: asyncio.subprocess.Process) -> None:
    runtime_directory = _OWNED_RUNTIME_DIRECTORIES.pop(id(process), None)
    if runtime_directory is not None:
        shutil.rmtree(runtime_directory, ignore_errors=True)


async def wait_for_exit(process: asyncio.subprocess.Process, expected_code: int = 0) -> bytes:
    try:
        try:
            returncode = await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        assert process.stderr is not None
        stderr = await process.stderr.read()
        assert returncode == expected_code, stderr.decode("utf-8", errors="replace")
        return stderr
    finally:
        cleanup_owned_runtime(process)


def device_path(path: Path) -> str:
    value = path.as_posix()
    if os.name == "nt":
        return "/UNC/" + value[2:] if value.startswith("//") else "/" + value
    return value


async def initialize_direct(process: asyncio.subprocess.Process) -> tuple[RequestCoordinator, EIPClient]:
    requester = RequestCoordinator(StdioTransport.from_process(process), request_timeout=2)
    client = EIPClient(requester)
    result = await client.initialize(
        InitializeParams(
            supported_protocol_versions=("0.1",),
            client=EIPClientInfo(name="e2e", version="1"),
            expected_device_id="env-e2e",
        )
    )
    requester.configure_limits(result.descriptor.limits)
    return requester, client


def test_real_daemon_session_round_trip_and_fresh_generations() -> None:
    async def one_run() -> int:
        process = await start_daemon(a13n_envd_binary())
        transport = StdioTransport.from_process(process)
        device = await EIPDeviceConnection.initialize(transport, expected_device_id="env-e2e")
        session = await device.open_session(required_methods=("environment.describe", "session.close"))
        descriptor = await session.describe()
        assert descriptor.device_id == "env-e2e"
        assert "file.stat" in descriptor.available_methods
        assert "session.keepalive" in descriptor.available_methods

        concurrent = await asyncio.gather(*(session.describe() for _ in range(8)))
        assert all(item.generation == descriptor.generation for item in concurrent)

        listener = await asyncio.start_server(lambda _reader, writer: writer.close(), "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        target = PortTarget(protocol="tcp", address=PortAddress.LOOPBACK, port=port)
        listening = await session.client.port_inspect(
            PortInspectParams(
                context=EIPCallContext(operation_id="port-inspect-e2e"),
                target=target,
            )
        )
        assert listening.observation.status is PortStatus.LISTENING
        listener.close()
        await listener.wait_closed()
        not_listening = await session.client.port_wait(
            PortWaitParams(
                context=EIPCallContext(
                    operation_id="port-wait-e2e",
                    timeout_ms=2_000,
                ),
                target=target,
                desired_status=DesiredPortStatus.NOT_LISTENING,
            )
        )
        assert not_listening.observation.status is PortStatus.NOT_LISTENING

        waiting = asyncio.create_task(
            session.client.port_wait(
                PortWaitParams(
                    context=EIPCallContext(
                        operation_id="port-cancel-target-e2e",
                        timeout_ms=5_000,
                    ),
                    target=target,
                    desired_status=DesiredPortStatus.LISTENING,
                )
            )
        )
        await asyncio.sleep(0.1)
        cancelled = await session.client.operation_cancel(
            OperationCancelParams(
                context=EIPCallContext(operation_id="port-cancel-request-e2e"),
                target_operation_id="port-cancel-target-e2e",
            )
        )
        assert cancelled.status is OperationCancelStatus.CANCELLATION_REQUESTED
        with pytest.raises(EIPMethodError) as cancellation_error:
            await waiting
        assert cancellation_error.value.error.code == -32041
        await session.close()
        await device.close()
        await wait_for_exit(process)
        return descriptor.generation

    async def scenario() -> None:
        first = await one_run()
        second = await one_run()
        assert first != second

    asyncio.run(scenario())


def test_real_daemon_one_device_opens_sequential_independent_sessions() -> None:
    async def scenario():
        process = await start_daemon(a13n_envd_binary())
        device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(process), expected_device_id="env-e2e"
        )
        descriptors = []
        async with device:
            for _ in range(2):
                async with await device.open_session() as session:
                    descriptors.append(await session.describe())
                assert process.returncode is None
        assert descriptors[0].generation == descriptors[1].generation
        assert descriptors[0].session_id != descriptors[1].session_id
        await wait_for_exit(process)

    asyncio.run(scenario())


def test_process_handles_are_fenced_across_daemon_generations(tmp_path: Path) -> None:
    native = tmp_path / "native"
    runtime = tmp_path / "runtime"
    native.mkdir()
    runtime.mkdir()
    python = Path(sys.executable).resolve()
    config_path = tmp_path / "a13n-envd.json"
    config_path.write_text(
        json.dumps({"default_working_directory": str(native), "trusted_executable_roots": [str(python.parent)]})
    )
    request = CommandRequest(
        command=ArgvCommand(
            kind="argv",
            executable_spec=ExecutableName(kind="name", name=python.name),
            arguments=("-c", "import time; time.sleep(30)"),
        ),
        cwd=EIPPath(path=device_path(native)),
    )

    async def scenario() -> None:
        first_daemon = await start_daemon(
            a13n_envd_binary(),
            config_path=config_path,
            runtime_dir=runtime,
        )
        first_device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(first_daemon), expected_device_id="env-e2e", request_timeout=5
        )
        first_session = await first_device.open_session(required_methods=("process.start",))
        first = await first_session.client.process_start(
            ProcessStartParams(
                context=EIPCallContext(
                    operation_id="generation-one-start",
                ),
                request=request,
            )
        )
        old_handle = first.process.handle
        await first_session.close()
        await first_device.close()
        await wait_for_exit(first_daemon)

        second_daemon = await start_daemon(
            a13n_envd_binary(),
            config_path=config_path,
            runtime_dir=runtime,
        )
        second_device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(second_daemon), expected_device_id="env-e2e", request_timeout=5
        )
        second_session = await second_device.open_session(required_methods=("process.start",))
        second = await second_session.client.process_start(
            ProcessStartParams(
                context=EIPCallContext(
                    operation_id="generation-two-start",
                ),
                request=request,
            )
        )
        assert second.process.handle != old_handle
        with pytest.raises(EIPMethodError):
            await second_session.client.process_kill(
                ProcessKillParams(
                    context=EIPCallContext(
                        operation_id="stale-generation-kill",
                        timeout_ms=5_000,
                    ),
                    handle=old_handle,
                )
            )
        still_running = await second_session.client.process_inspect(
            ProcessInspectParams(
                context=EIPCallContext(operation_id="generation-two-inspect"),
                handle=second.process.handle,
            )
        )
        assert still_running.process.status.phase.value == "running"
        await second_session.client.process_kill(
            ProcessKillParams(
                context=EIPCallContext(
                    operation_id="generation-two-kill",
                    timeout_ms=5_000,
                ),
                handle=second.process.handle,
            )
        )
        await second_session.close()
        await second_device.close()
        await wait_for_exit(second_daemon)

    asyncio.run(scenario())


def test_configured_daemon_command_process_and_output_plane(tmp_path: Path) -> None:
    native = tmp_path / "native"
    runtime = tmp_path / "runtime"
    native.mkdir()
    runtime.mkdir()
    python = Path(sys.executable).resolve()
    config_path = tmp_path / "a13n-envd.json"
    config_path.write_text(
        json.dumps(
            {
                "default_working_directory": str(native),
                "trusted_executable_roots": [str(python.parent)],
                "limits": {
                    "max_output_preview_bytes": 4,
                    "max_output_bytes_per_stream": 128,
                    "max_spool_bytes": 512,
                },
            }
        )
    )

    async def scenario() -> None:
        process = await start_daemon(
            a13n_envd_binary(),
            config_path=config_path,
            runtime_dir=runtime,
        )
        device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(process), expected_device_id="env-e2e", request_timeout=5
        )
        session = await device.open_session(required_methods=("shell.exec", "process.start", "output.read"))
        request = CommandRequest(
            command=ArgvCommand(
                kind="argv",
                executable_spec=ExecutableName(kind="name", name=python.name),
                arguments=(
                    "-c",
                    "import os,sys; data=sys.stdin.buffer.read(4); "
                    "assert os.environ['HTTP_PROXY']=='http://proxy.invalid:8080'; "
                    "sys.stdout.buffer.write(b'pre:'+data+b':'"
                    "+os.environ['BLOCK3_TEST'].encode()+b':'"
                    "+os.environ.get('LANG','').encode()); sys.stdout.flush()",
                ),
            ),
            cwd=EIPPath(path=device_path(native)),
            environment=CommandEnvironment(set={"BLOCK3_TEST": "works"}),
            keep_stdin_open=True,
        )
        started = await session.client.process_start(
            ProcessStartParams(
                context=EIPCallContext(
                    operation_id="process-start-e2e",
                ),
                request=request,
            )
        )
        assert started.process.stdin_open is True
        assert started.receipt.stage.value == "exec_confirmed"
        replayed = await session.client.process_start(
            ProcessStartParams(
                context=EIPCallContext(
                    operation_id="process-start-e2e",
                ),
                request=request,
            )
        )
        assert replayed == started
        inspected = await session.client.process_inspect(
            ProcessInspectParams(
                context=EIPCallContext(operation_id="process-inspect-e2e"),
                handle=started.process.handle,
            )
        )
        assert inspected.process.handle == started.process.handle
        assert inspected.process.status.phase.value == "running"

        payload = b"ping"
        encoded = base64.b64encode(payload).decode().rstrip("=")
        written = await session.client.process_write_stdin(
            ProcessWriteStdinParams(
                context=EIPCallContext(
                    operation_id="process-stdin-e2e",
                ),
                handle=started.process.handle,
                data=EncodedBytes(encoding="base64", data=encoded),
                close_after_write=True,
            )
        )
        assert written.accepted_bytes == len(payload)
        assert written.stdin_open is False
        waited = await session.client.process_wait(
            ProcessWaitParams(
                context=EIPCallContext(
                    operation_id="process-wait-e2e",
                    timeout_ms=5_000,
                ),
                handle=started.process.handle,
                condition=ProcessWaitCondition.TREE_CLEANED,
            )
        )
        assert waited.process.status.phase.value == "exited"
        assert waited.process.status.cleanup.value == "complete"
        assert waited.process.status.exit_code == 0

        stdout_reference = waited.process.output.stdout.reference
        stderr_reference = waited.process.output.stderr.reference
        reader = session.open_output(stdout_reference, observed=waited.process.output.stdout)
        stdout = b"".join([chunk async for chunk in reader])
        assert stdout.startswith(b"pre:ping:works:")
        with pytest.raises(EIPMethodError) as invalid_offset:
            await session.client.output_read(
                OutputReadParams(
                    context=EIPCallContext(operation_id="process-output-invalid-e2e"),
                    reference=stdout_reference,
                    start_offset=waited.process.output.stdout.retained_bytes + 1,
                )
            )
        assert invalid_offset.value.error.data.error_type is ErrorType.INVALID_PARAMS

        released = await session.client.process_release(
            ProcessReleaseParams(
                context=EIPCallContext(
                    operation_id="process-release-e2e",
                ),
                handle=started.process.handle,
            )
        )
        assert released.released is True
        replayed_after_release = await session.client.process_start(
            ProcessStartParams(
                context=EIPCallContext(
                    operation_id="process-start-e2e",
                ),
                request=request,
            )
        )
        assert replayed_after_release == started

        detached_stdout = session.open_output(stdout_reference)
        detached_stderr = session.open_output(stderr_reference)
        assert b"".join([chunk async for chunk in detached_stdout]) == stdout
        assert b"".join([chunk async for chunk in detached_stderr]) == b""
        for index, detached_reference in enumerate((stdout_reference, stderr_reference)):
            detached_release = await session.client.output_release(
                OutputReleaseParams(
                    context=EIPCallContext(operation_id=f"process-output-release-e2e-{index}"),
                    reference=detached_reference,
                )
            )
            assert detached_release.released is True

        foreground = await session.client.shell_exec(
            ShellExecParams(
                context=EIPCallContext(operation_id="shell-exec-e2e"),
                request=CommandRequest(
                    command=ArgvCommand(
                        kind="argv",
                        executable_spec=ExecutableName(kind="name", name=python.name),
                        arguments=("-c", "print('foreground')"),
                    ),
                    cwd=EIPPath(path=device_path(native)),
                ),
            )
        )
        assert foreground.status.phase.value == "exited"
        assert foreground.status.cleanup.value == "complete"
        assert base64.b64decode(foreground.output.stdout.preview.data + "===") == b"fore"
        foreground_reader = session.open_output(
            foreground.output.stdout.reference,
            observed=foreground.output.stdout,
        )
        assert b"".join([chunk async for chunk in foreground_reader]) == f"foreground{os.linesep}".encode()
        for index, foreground_output in enumerate((foreground.output.stdout, foreground.output.stderr)):
            foreground_release = await session.client.output_release(
                OutputReleaseParams(
                    context=EIPCallContext(operation_id=f"shell-output-release-e2e-{index}"),
                    reference=foreground_output.reference,
                )
            )
            assert foreground_release.released is True

        retained_foreground = await session.client.shell_exec(
            ShellExecParams(
                context=EIPCallContext(operation_id="shell-retained-output-e2e"),
                request=CommandRequest(
                    command=ArgvCommand(
                        kind="argv",
                        executable_spec=ExecutableName(kind="name", name=python.name),
                        arguments=("-c", "print('retained-foreground')"),
                    ),
                    cwd=EIPPath(path=device_path(native)),
                ),
            )
        )
        retained_reference = retained_foreground.output.stdout.reference
        retained_reader = session.open_output(
            retained_reference,
            observed=retained_foreground.output.stdout,
        )
        assert b"".join([chunk async for chunk in retained_reader]) == f"retained-foreground{os.linesep}".encode()
        retained_references = (
            retained_reference,
            retained_foreground.output.stderr.reference,
        )
        for index, reference_to_release in enumerate(retained_references):
            retained_release = await session.client.output_release(
                OutputReleaseParams(
                    context=EIPCallContext(
                        operation_id=f"shell-retained-release-e2e-{index}",
                    ),
                    reference=reference_to_release,
                )
            )
            assert retained_release.released is True

        for attempt in range(1):
            with pytest.raises(EIPMethodError) as output_error:
                await session.client.shell_exec(
                    ShellExecParams(
                        context=EIPCallContext(operation_id=f"shell-output-limit-e2e-{attempt}"),
                        request=CommandRequest(
                            command=ArgvCommand(
                                kind="argv",
                                executable_spec=ExecutableName(kind="name", name=python.name),
                                arguments=(
                                    "-c",
                                    "import sys; sys.stdout.buffer.write(b'x' * 256); sys.stdout.flush()",
                                ),
                            ),
                            cwd=EIPPath(path=device_path(native)),
                        ),
                    )
                )
            assert output_error.value.error.code == -32032
            error_data = output_error.value.error.data
            assert error_data.process is None
            assert error_data.process_status is not None
            assert error_data.process_status.cleanup.value == "complete"
            evidence = error_data.output
            assert evidence is not None
            assert evidence.stdout.content_complete is False
            for index, output_info in enumerate((evidence.stdout, evidence.stderr)):
                released_output = await session.client.output_release(
                    OutputReleaseParams(
                        context=EIPCallContext(operation_id=f"shell-output-limit-release-{attempt}-{index}"),
                        reference=output_info.reference,
                    )
                )
                assert released_output.released is True

        sleeper = await session.client.process_start(
            ProcessStartParams(
                context=EIPCallContext(
                    operation_id="process-kill-start-e2e",
                ),
                request=CommandRequest(
                    command=ArgvCommand(
                        kind="argv",
                        executable_spec=ExecutableName(kind="name", name=python.name),
                        arguments=(
                            "-c",
                            "import time; time.sleep(0.2); print('ready', flush=True); time.sleep(30)",
                        ),
                    ),
                    cwd=EIPPath(path=device_path(native)),
                ),
            )
        )
        live_reader = session.open_output(
            sleeper.process.output.stdout.reference,
            observed=sleeper.process.output.stdout,
        )
        live_page = await asyncio.wait_for(live_reader.read_page(wait_ms=5_000), timeout=2)
        assert live_page.output.producer_complete is False
        assert live_page.next_offset > 0
        repeated_reader = session.open_output(
            sleeper.process.output.stdout.reference,
            observed=sleeper.process.output.stdout,
        )
        repeated_page = await asyncio.wait_for(repeated_reader.read_page(wait_ms=5_000), timeout=1)
        assert repeated_page.data == live_page.data
        with pytest.raises(EIPMethodError) as invalid_live_offset:
            await session.client.output_read(
                OutputReadParams(
                    context=EIPCallContext(operation_id="process-live-output-invalid-e2e"),
                    reference=sleeper.process.output.stdout.reference,
                    start_offset=live_page.output.retained_bytes + 1,
                    wait_ms=5_000,
                )
            )
        assert invalid_live_offset.value.error.data.error_type is ErrorType.INVALID_PARAMS
        killed = await session.client.process_kill(
            ProcessKillParams(
                context=EIPCallContext(
                    operation_id="process-kill-e2e",
                    timeout_ms=5_000,
                ),
                handle=sleeper.process.handle,
            )
        )
        assert killed.process.status.phase.value == "signaled"
        assert killed.process.status.cleanup.value == "complete"
        await session.client.process_release(
            ProcessReleaseParams(
                context=EIPCallContext(
                    operation_id="process-kill-release-e2e",
                ),
                handle=sleeper.process.handle,
            )
        )

        stdin_waiter = await session.client.process_start(
            ProcessStartParams(
                context=EIPCallContext(
                    operation_id="process-close-start-e2e",
                ),
                request=CommandRequest(
                    command=ArgvCommand(
                        kind="argv",
                        executable_spec=ExecutableName(kind="name", name=python.name),
                        arguments=(
                            "-c",
                            "import sys; sys.stdin.buffer.read(); print('closed')",
                        ),
                    ),
                    cwd=EIPPath(path=device_path(native)),
                    keep_stdin_open=True,
                ),
            )
        )
        closed = await session.client.process_close_stdin(
            ProcessCloseStdinParams(
                context=EIPCallContext(
                    operation_id="process-close-e2e",
                ),
                handle=stdin_waiter.process.handle,
            )
        )
        assert closed.stdin_open is False
        await session.client.process_wait(
            ProcessWaitParams(
                context=EIPCallContext(
                    operation_id="process-close-wait-e2e",
                    timeout_ms=5_000,
                ),
                handle=stdin_waiter.process.handle,
                condition=ProcessWaitCondition.TREE_CLEANED,
            )
        )
        await session.client.process_release(
            ProcessReleaseParams(
                context=EIPCallContext(
                    operation_id="process-close-release-e2e",
                ),
                handle=stdin_waiter.process.handle,
            )
        )

        partial_reader = await session.client.process_start(
            ProcessStartParams(
                context=EIPCallContext(
                    operation_id="process-partial-stdin-start-e2e",
                ),
                request=CommandRequest(
                    command=ArgvCommand(
                        kind="argv",
                        executable_spec=ExecutableName(kind="name", name=python.name),
                        arguments=(
                            "-c",
                            "import os,time; os.read(0,1); os.close(0); time.sleep(0.25)",
                        ),
                    ),
                    cwd=EIPPath(path=device_path(native)),
                    keep_stdin_open=True,
                ),
            )
        )
        partial_payload = b"z" * 300_000
        partial_write = await session.client.process_write_stdin(
            ProcessWriteStdinParams(
                context=EIPCallContext(
                    operation_id="process-partial-stdin-write-e2e",
                ),
                handle=partial_reader.process.handle,
                data=EncodedBytes(
                    encoding="base64",
                    data=base64.b64encode(partial_payload).decode().rstrip("="),
                ),
            )
        )
        # Accepted bytes measure envd's bounded stdin writer, not bytes read by
        # the payload. Windows' buffered writer may accept this entire chunk
        # before observing that the reader closed its pipe.
        assert 0 < partial_write.accepted_bytes <= len(partial_payload)
        if sys.platform != "win32":
            assert partial_write.accepted_bytes < len(partial_payload)
        if partial_write.accepted_bytes < len(partial_payload):
            assert partial_write.stdin_open is False
        await session.client.process_wait(
            ProcessWaitParams(
                context=EIPCallContext(
                    operation_id="process-partial-stdin-wait-e2e",
                    timeout_ms=5_000,
                ),
                handle=partial_reader.process.handle,
                condition=ProcessWaitCondition.TREE_CLEANED,
            )
        )
        await session.client.process_release(
            ProcessReleaseParams(
                context=EIPCallContext(
                    operation_id="process-partial-stdin-release-e2e",
                ),
                handle=partial_reader.process.handle,
            )
        )

        signaled_process = await session.client.process_start(
            ProcessStartParams(
                context=EIPCallContext(
                    operation_id="process-signal-start-e2e",
                ),
                request=CommandRequest(
                    command=ArgvCommand(
                        kind="argv",
                        executable_spec=ExecutableName(kind="name", name=python.name),
                        arguments=("-c", "import time; time.sleep(30)"),
                    ),
                    cwd=EIPPath(path=device_path(native)),
                ),
            )
        )
        signal_params = ProcessSignalParams(
            context=EIPCallContext(operation_id="process-signal-e2e"),
            handle=signaled_process.process.handle,
            signal=RequestedProcessSignal.TERMINATE,
        )
        if session.descriptor.execution_features.signal_terminate:
            signaled = await session.client.process_signal(signal_params)
            assert signaled.accepted is True
        else:
            assert "process.signal" not in session.descriptor.available_methods
            with pytest.raises(EIPMethodError) as unsupported_signal:
                await session.client.process_signal(signal_params)
            assert unsupported_signal.value.error.data.error_type is ErrorType.UNSUPPORTED
            await session.client.process_kill(
                ProcessKillParams(
                    context=EIPCallContext(operation_id="process-signal-fallback-kill-e2e"),
                    handle=signaled_process.process.handle,
                )
            )
        signaled_terminal = await session.client.process_wait(
            ProcessWaitParams(
                context=EIPCallContext(
                    operation_id="process-signal-wait-e2e",
                    timeout_ms=5_000,
                ),
                handle=signaled_process.process.handle,
                condition=ProcessWaitCondition.TREE_CLEANED,
            )
        )
        assert signaled_terminal.process.status.cleanup.value == "complete"
        await session.client.process_release(
            ProcessReleaseParams(
                context=EIPCallContext(
                    operation_id="process-signal-release-e2e",
                ),
                handle=signaled_process.process.handle,
            )
        )

        blocked_initial_start = asyncio.create_task(
            session.client.process_start(
                ProcessStartParams(
                    context=EIPCallContext(
                        operation_id="process-blocked-initial-stdin-e2e",
                        timeout_ms=5_000,
                    ),
                    request=CommandRequest(
                        command=ArgvCommand(
                            kind="argv",
                            executable_spec=ExecutableName(kind="name", name=python.name),
                            arguments=("-c", "import time; time.sleep(30)"),
                        ),
                        cwd=EIPPath(path=device_path(native)),
                        initial_stdin=EncodedBytes(
                            encoding="base64",
                            data=base64.b64encode(b"x" * 300_000).decode().rstrip("="),
                        ),
                    ),
                )
            )
        )
        await asyncio.sleep(0)
        blocked_cancellation = await session.client.operation_cancel(
            OperationCancelParams(
                context=EIPCallContext(operation_id="process-blocked-initial-cancel-e2e"),
                target_operation_id="process-blocked-initial-stdin-e2e",
            )
        )
        assert blocked_cancellation.status is OperationCancelStatus.CANCELLATION_REQUESTED
        with pytest.raises(EIPMethodError) as blocked_start_error:
            await blocked_initial_start
        assert blocked_start_error.value.error.data.error_type.value == "cancelled"

        foreground_wait = asyncio.create_task(
            session.client.shell_exec(
                ShellExecParams(
                    context=EIPCallContext(
                        operation_id="shell-cancel-target-e2e",
                        timeout_ms=5_000,
                    ),
                    request=CommandRequest(
                        command=ArgvCommand(
                            kind="argv",
                            executable_spec=ExecutableName(kind="name", name=python.name),
                            arguments=("-c", "import time; time.sleep(30)"),
                        ),
                        cwd=EIPPath(path=device_path(native)),
                    ),
                )
            )
        )
        await asyncio.sleep(0.05)
        cancellation = await session.client.operation_cancel(
            OperationCancelParams(
                context=EIPCallContext(operation_id="shell-cancel-request-e2e"),
                target_operation_id="shell-cancel-target-e2e",
            )
        )
        assert cancellation.status is OperationCancelStatus.CANCELLATION_REQUESTED
        cancelled_foreground = await foreground_wait
        assert cancelled_foreground.status.phase.value == "cancelled"
        assert cancelled_foreground.status.cleanup.value == "complete"
        assert cancelled_foreground.receipt.outcome.value == "cancelled"

        await session.client.process_start(
            ProcessStartParams(
                context=EIPCallContext(
                    operation_id="process-daemon-drain-e2e",
                ),
                request=CommandRequest(
                    command=ArgvCommand(
                        kind="argv",
                        executable_spec=ExecutableName(kind="name", name=python.name),
                        arguments=("-c", "import time; time.sleep(30)"),
                    ),
                    cwd=EIPPath(path=device_path(native)),
                ),
            )
        )
        await session.close()
        await device.close()
        await wait_for_exit(process)

    asyncio.run(scenario())


def test_configured_daemon_resource_and_transfer_plane(tmp_path: Path) -> None:
    native = tmp_path / "native"
    native.mkdir()
    config_path = tmp_path / "a13n-envd.json"
    config_path.write_text(json.dumps({"default_working_directory": str(native)}))

    async def scenario() -> None:
        process = await start_daemon(a13n_envd_binary(), config_path=config_path)
        device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(process), expected_device_id="env-e2e", request_timeout=5
        )
        session = await device.open_session(
            required_methods=(
                "file.open_reader",
                "file.close_reader",
                "file.open_writer",
                "file.commit_writer",
                "file.write_text",
                "file.read_text",
                "file.stat",
                "file.list",
                "file.find",
                "file.search",
                "receipt.get",
            )
        )
        assert session.descriptor.working_directory == device_path(native)
        file_path = EIPPath(path=device_path(native / "binary.dat"))
        payload = bytes(range(256)) * 8
        async with session.open_writer(file_path, mode=FileWriteMode.CREATE) as writer:
            await writer.write(payload[:777])
            await writer.write(payload[777:])
            committed = await writer.commit()
        assert committed.transferred_bytes == len(payload)
        assert (native / "binary.dat").read_bytes() == payload

        downloaded = bytearray()
        async with session.open_reader(file_path) as reader:
            async for chunk in reader:
                downloaded.extend(chunk)
        assert bytes(downloaded) == payload
        assert reader.completion.digest.algorithm == "sha256"

        text_path = EIPPath(path=device_path(native / "notes.txt"))
        write_params = FileWriteTextParams(
            context=EIPCallContext(
                operation_id="write-text-e2e",
            ),
            path=text_path,
            mode=FileWriteMode.CREATE,
            text="alpha\nbeta\n",
        )
        written = await session.client.file_write_text(write_params)
        replayed = await session.client.file_write_text(
            FileWriteTextParams(
                context=EIPCallContext(
                    operation_id="write-text-e2e",
                ),
                path=text_path,
                mode=FileWriteMode.CREATE,
                text="alpha\nbeta\n",
            )
        )
        assert replayed == written
        assert (native / "notes.txt").read_text() == "alpha\nbeta\n"
        text = await session.client.file_read_text(
            FileReadTextParams(
                context=EIPCallContext(operation_id="text-e2e"),
                path=text_path,
                line_offset=0,
                line_limit=2,
                max_line_length=2_000,
            )
        )
        assert text.text == "alpha\nbeta\n"
        assert text.line_offset == 0
        assert text.lines_read == 2
        assert text.has_more is False
        stat = await session.client.file_stat(
            FileStatParams(
                context=EIPCallContext(operation_id="stat-e2e"),
                path=text_path,
            )
        )
        assert stat.info.path == text.info.path

        missing_a = FileStatParams(
            context=EIPCallContext(operation_id="stat-failure-e2e"),
            path=EIPPath(path=device_path(native / "missing-a")),
        )
        with pytest.raises(EIPMethodError) as first_failure:
            await session.client.file_stat(missing_a)
        assert first_failure.value.error.data.error_type is ErrorType.NOT_FOUND_OR_DENIED
        with pytest.raises(EIPMethodError) as second_failure:
            await session.client.file_stat(
                FileStatParams(
                    context=EIPCallContext(operation_id="stat-failure-e2e"),
                    path=EIPPath(path=device_path(native / "missing-b")),
                )
            )
        assert second_failure.value.error.data.error_type is ErrorType.NOT_FOUND_OR_DENIED
        with pytest.raises(EIPMethodError) as repeated_failure:
            await session.client.file_stat(missing_a)
        assert repeated_failure.value.error.data.error_type is ErrorType.NOT_FOUND_OR_DENIED

        listed = await session.client.file_list(
            FileListParams(
                context=EIPCallContext(operation_id="list-e2e"),
                path=EIPPath(path=device_path(native)),
            )
        )
        assert [entry.relative_path for entry in listed.entries] == ["binary.dat", "notes.txt"]
        assert listed.offset == 0
        assert listed.has_more is False
        found = await session.client.file_find(
            FileFindParams(
                context=EIPCallContext(operation_id="find-e2e"),
                root=EIPPath(path=device_path(native)),
                pattern="*.txt",
                kinds=(FileKind.FILE,),
            )
        )
        assert [entry.relative_path for entry in found.entries] == ["notes.txt"]
        assert found.offset == 0
        assert found.has_more is False
        searched = await session.client.file_search(
            FileSearchParams(
                context=EIPCallContext(operation_id="search-e2e"),
                root=EIPPath(path=device_path(native)),
                query="beta",
                mode=SearchMode.LITERAL,
                max_line_length=2_000,
            )
        )
        assert [(match.path, match.line_number) for match in searched.matches] == [(text_path, 2)]
        assert searched.offset == 0
        assert searched.has_more is False

        (native / "src").mkdir()
        (native / "ignored").mkdir()
        (native / ".gitignore").write_text("ignored/\n")
        (native / "src" / "match.py").write_text("before\nneedle one\nafter\nneedle two\n")
        (native / "src" / "other.txt").write_text("needle\n")
        (native / "ignored" / "hidden.py").write_text("needle\n")
        filtered_find = await session.client.file_find(
            FileFindParams(
                context=EIPCallContext(operation_id="filtered-find-e2e"),
                root=EIPPath(path=device_path(native)),
                pattern="*.py",
                kinds=(FileKind.FILE,),
                respect_git_ignore=True,
            )
        )
        assert [entry.relative_path for entry in filtered_find.entries] == ["src/match.py"]
        filtered_search = await session.client.file_search(
            FileSearchParams(
                context=EIPCallContext(operation_id="filtered-search-e2e"),
                root=EIPPath(path=device_path(native)),
                query="needle",
                mode=SearchMode.LITERAL,
                include_pattern="**/*.py",
                respect_git_ignore=True,
                context_lines=1,
                max_matches_per_file=1,
                max_files=10,
                max_file_bytes=4_096,
                max_line_length=80,
            )
        )
        assert len(filtered_search.matches) == 1
        filtered_match = filtered_search.matches[0]
        assert filtered_match.path == EIPPath(path=device_path(native / "src/match.py"))
        assert filtered_match.line_number == 2
        assert filtered_match.preview == "needle one"
        assert filtered_match.context == "before\nneedle one\nafter\n"
        assert filtered_match.context_start_line == 1
        assert filtered_search.has_more is False

        receipt = await session.client.receipt_get(
            ReceiptGetParams(
                context=EIPCallContext(operation_id="receipt-e2e"),
                operation_id=written.receipt.operation_id,
            )
        )
        assert receipt.receipt.operation_id == written.receipt.operation_id
        await session.close()
        await device.close()
        await wait_for_exit(process)

    asyncio.run(scenario())


def test_initialization_identity_failure_is_typed_and_terminal() -> None:
    async def scenario():
        process = await start_daemon(a13n_envd_binary())
        with pytest.raises(EIPMethodError) as failure:
            await EIPDeviceConnection.initialize(
                StdioTransport.from_process(process), expected_device_id="wrong-device"
            )
        assert failure.value.error.code == -32003
        await wait_for_exit(process)

    asyncio.run(scenario())


def test_incompatible_protocol_version_is_typed_and_terminal() -> None:
    async def scenario() -> None:
        process = await start_daemon(a13n_envd_binary())
        requester = RequestCoordinator(StdioTransport.from_process(process), request_timeout=2)
        client = EIPClient(requester)
        with pytest.raises(EIPMethodError) as captured:
            await client.initialize(
                InitializeParams(
                    supported_protocol_versions=("2.0",),
                    client=EIPClientInfo(name="e2e", version="1"),
                    expected_device_id="env-e2e",
                )
            )
        assert captured.value.error.code == -32003
        await requester.close()
        await wait_for_exit(process)

    asyncio.run(scenario())


def test_preinitialize_and_repeated_initialize_errors() -> None:
    async def preinitialize() -> None:
        process = await start_daemon(a13n_envd_binary())
        requester = RequestCoordinator(StdioTransport.from_process(process), request_timeout=2)
        client = EIPClient(requester)
        with pytest.raises(EIPMethodError) as captured:
            await client.device_describe(DeviceDescribeParams())
        assert captured.value.error.code == -32001
        await requester.close()
        await wait_for_exit(process)

    async def repeated() -> None:
        process = await start_daemon(a13n_envd_binary())
        requester, client = await initialize_direct(process)
        with pytest.raises(EIPMethodError) as captured:
            await client.initialize(
                InitializeParams(
                    supported_protocol_versions=("0.1",),
                    client=EIPClientInfo(name="e2e", version="1"),
                    expected_device_id="env-e2e",
                )
            )
        assert captured.value.error.code == -32002
        await requester.close()
        await wait_for_exit(process)

    asyncio.run(preinitialize())
    asyncio.run(repeated())


def test_large_dual_stream_spool_lock_and_crash_restart(tmp_path: Path) -> None:
    native = tmp_path / "native"
    runtime = tmp_path / "runtime"
    native.mkdir()
    runtime.mkdir()
    python = Path(sys.executable).resolve()
    config_path = tmp_path / "a13n-envd.json"
    config_path.write_text(
        json.dumps(
            {
                "default_working_directory": str(native),
                "trusted_executable_roots": [str(python.parent)],
                "limits": {
                    "max_output_preview_bytes": 32,
                    "max_output_bytes_per_stream": 128 * 1024,
                    "max_spool_bytes": 512 * 1024,
                },
            }
        )
    )

    async def scenario() -> None:
        process = await start_daemon(
            a13n_envd_binary(),
            config_path=config_path,
            runtime_dir=runtime,
        )
        device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(process), expected_device_id="env-e2e", request_timeout=5
        )
        session = await device.open_session(required_methods=("shell.exec", "output.read"))
        generation = session.generation
        stdout_expected = bytes(range(256)) * 256
        stderr_expected = bytes(range(255, -1, -1)) * 256
        result = await session.client.shell_exec(
            ShellExecParams(
                context=EIPCallContext(operation_id="large-dual-stream-e2e"),
                request=CommandRequest(
                    command=ArgvCommand(
                        kind="argv",
                        executable_spec=ExecutableName(kind="name", name=python.name),
                        arguments=(
                            "-c",
                            "import sys; "
                            "sys.stdout.buffer.write(bytes(range(256))*256); "
                            "sys.stderr.buffer.write(bytes(range(255,-1,-1))*256)",
                        ),
                    ),
                    cwd=EIPPath(path=device_path(native)),
                ),
            )
        )
        assert result.output.stdout.retained_bytes == len(stdout_expected)
        assert result.output.stderr.retained_bytes == len(stderr_expected)
        assert len(base64.b64decode(result.output.stdout.preview.data + "===")) == 32
        stdout = b"".join(
            [
                chunk
                async for chunk in session.open_output(
                    result.output.stdout.reference,
                    observed=result.output.stdout,
                )
            ]
        )
        stderr = b"".join(
            [
                chunk
                async for chunk in session.open_output(
                    result.output.stderr.reference,
                    observed=result.output.stderr,
                )
            ]
        )
        assert hashlib.sha256(stdout).digest() == hashlib.sha256(stdout_expected).digest()
        assert hashlib.sha256(stderr).digest() == hashlib.sha256(stderr_expected).digest()

        contender = await start_daemon(
            a13n_envd_binary(),
            config_path=config_path,
            runtime_dir=runtime,
        )
        await wait_for_exit(contender, expected_code=1)

        old_reference = result.output.stdout.reference
        process.kill()
        await process.wait()
        await session.abort()
        await device.close()
        stale_generations = [path for path in runtime.iterdir() if path.name.startswith("generation-")]
        assert len(stale_generations) == 1

        replacement = await start_daemon(
            a13n_envd_binary(),
            config_path=config_path,
            runtime_dir=runtime,
        )
        replacement_device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(replacement), expected_device_id="env-e2e", request_timeout=5
        )
        replacement_session = await replacement_device.open_session(required_methods=("output.read",))
        assert replacement_session.generation != generation
        fresh_generations = [path for path in runtime.iterdir() if path.name.startswith("generation-")]
        assert len(fresh_generations) == 1
        assert fresh_generations[0] != stale_generations[0]
        with pytest.raises(EIPMethodError) as stale_output:
            await replacement_session.client.output_read(
                OutputReadParams(
                    context=EIPCallContext(operation_id="stale-output-after-restart-e2e"),
                    reference=old_reference,
                    start_offset=0,
                )
            )
        assert stale_output.value.error.data.error_type in {
            ErrorType.INVALID_HANDLE,
            ErrorType.NOT_FOUND_OR_DENIED,
        }
        await replacement_session.close()
        await replacement_device.close()
        await wait_for_exit(replacement)

    asyncio.run(scenario())


def test_unknown_method_is_rejected_as_unavailable() -> None:
    async def scenario() -> None:
        process = await start_daemon(a13n_envd_binary())
        device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(process), expected_device_id="env-e2e"
        )
        session = await device.open_session()
        unknown = MethodSpec(
            name="future.unknown",
            kind="request_response",
            replay_class="active_only",
            introduced="0.1",
            error_family="common",
            params_type=EnvironmentDescribeParams,
            result_type=EnvironmentDescribeResult,
        )
        with pytest.raises(EIPMethodError) as captured:
            await session._requester.request(
                unknown,
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="unknown-method")),
            )
        assert captured.value.error.code == -32601
        assert captured.value.error.data.error_type is ErrorType.METHOD_NOT_FOUND
        await device.close()
        await wait_for_exit(process)

    asyncio.run(scenario())


def test_daemon_exit_maps_to_transport_closed() -> None:
    async def scenario() -> None:
        process = await start_daemon(a13n_envd_binary())
        device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(process), expected_device_id="env-e2e"
        )
        session = await device.open_session(required_methods=())
        process.terminate()
        await wait_for_exit(process)
        with pytest.raises((EIPTransportClosedError, EIPSessionStateError)) as failure:
            await session.describe()
        if isinstance(failure.value, EIPSessionStateError):
            assert isinstance(failure.value.__cause__, EIPTransportClosedError)
        await session.abort()
        await device.close()

    asyncio.run(scenario())


def test_raw_invalid_id_gets_nullable_invalid_request_response() -> None:
    async def scenario() -> None:
        process = await start_daemon(a13n_envd_binary())
        assert process.stdin is not None and process.stdout is not None
        body = json.dumps(
            {"jsonrpc": "2.0", "id": True, "method": "initialize", "params": {}},
            separators=(",", ":"),
        ).encode()
        process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        await process.stdin.drain()
        response = await read_raw_frame(process.stdout)
        assert response["id"] is None
        assert response["error"]["code"] == -32600
        process.stdin.close()
        await wait_for_exit(process)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "header",
    [
        b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
        b"Content-Length: 16777217\r\n\r\n",
    ],
)
def test_malformed_or_oversized_frame_fails_transport(header: bytes) -> None:
    async def scenario() -> None:
        process = await start_daemon(a13n_envd_binary())
        assert process.stdin is not None
        process.stdin.write(header)
        await process.stdin.drain()
        process.stdin.close()
        stderr = await wait_for_exit(process, expected_code=1)
        assert b"a13n-envd failed" in stderr

    asyncio.run(scenario())


def test_sigterm_remains_bounded_when_stdout_is_backpressured() -> None:
    async def scenario() -> None:
        process = await start_daemon(a13n_envd_binary())
        stdout_transport = None
        stdout_paused = False
        try:
            assert process.stdin is not None and process.stdout is not None
            initialize = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "supported_protocol_versions": ["0.1"],
                        "client": {"name": "backpressure", "version": "1"},
                        "expected_device_id": "env-e2e",
                    },
                },
                separators=(",", ":"),
            ).encode()
            process.stdin.write(f"Content-Length: {len(initialize)}\r\n\r\n".encode() + initialize)
            await process.stdin.drain()
            assert (await read_raw_frame(process.stdout))["id"] == 1

            stdout_transport = process.stdout._transport
            assert stdout_transport is not None
            stdout_transport.pause_reading()
            stdout_paused = True

            next_id = 2
            input_backpressured = False
            for _batch in range(256):
                frames = bytearray()
                for _frame in range(256):
                    body = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": next_id,
                            "method": "device.describe",
                            "params": {},
                        },
                        separators=(",", ":"),
                    ).encode()
                    frames.extend(f"Content-Length: {len(body)}\r\n\r\n".encode())
                    frames.extend(body)
                    next_id += 1
                process.stdin.write(frames)
                try:
                    async with asyncio.timeout(1):
                        await process.stdin.drain()
                except TimeoutError:
                    input_backpressured = True
                    break
            assert input_backpressured, "stdio request input did not backpressure behind blocked stdout"

            process.terminate()
            async with asyncio.timeout(5):
                while process.returncode is None:
                    await asyncio.sleep(0.01)
            returncode = process.returncode
        finally:
            # asyncio's subprocess transport does not finish wait() while an
            # unread stdout pipe remains paused with buffered data, even after
            # child exit.
            if stdout_paused:
                assert stdout_transport is not None
                stdout_transport.resume_reading()
            try:
                if process.returncode is None:
                    process.kill()
                if process.stdout is not None:
                    await process.stdout.read()
                await process.wait()
            finally:
                cleanup_owned_runtime(process)

        assert returncode == 1
        assert process.stderr is not None
        stderr = await process.stderr.read()
        assert b"response drain timed out" in stderr

    asyncio.run(scenario())


def test_parent_eof_before_initialization_and_sigterm_after_readiness_exit_cleanly() -> None:
    async def eof() -> None:
        process = await start_daemon(a13n_envd_binary())
        assert process.stdin is not None
        process.stdin.close()
        await wait_for_exit(process)

    async def sigterm() -> None:
        process = await start_daemon(a13n_envd_binary())
        requester, _ = await initialize_direct(process)
        process.terminate()
        await wait_for_exit(process)
        await requester.close()

    asyncio.run(eof())
    asyncio.run(sigterm())


async def read_raw_frame(reader: asyncio.StreamReader) -> dict[str, object]:
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line == b"\r\n":
            break
        assert line
        name, value = line.decode("ascii").split(":", 1)
        headers[name.lower()] = value.strip()
    body = await reader.readexactly(int(headers["content-length"]))
    value = json.loads(body)
    assert isinstance(value, dict)
    return value
