from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from a13n_envd_client import (
    AcceptedWebSocketTransport,
    EIPDeviceConnection,
    EIPMethodError,
    EIPRequestTimeoutError,
    EIPSession,
    EIPSessionStateError,
    EIPTransportClosedError,
    HttpTransport,
    StdioTransport,
)
from a13n_envd_client.eip.v1 import (
    ArgvCommand,
    CommandRequest,
    DeviceMetricsParams,
    DirectoryListParams,
    EIPCallContext,
    EIPPath,
    ErrorType,
    ExecutableName,
    FileReadTextParams,
    FileWriteMode,
    ProcessInspectParams,
    ProcessKillParams,
    ProcessStartParams,
    SessionConfiguration,
    SessionKeepaliveParams,
    SessionLifecyclePolicy,
    ShellExecParams,
)
from websockets.asyncio.server import serve


@pytest.mark.parametrize("state", ["not_ready", "closed", "detached", "carrier_closed"])
@pytest.mark.parametrize("accessor", ["client", "requester"])
def test_session_access_rejects_unusable_local_lifecycle_without_io(state, accessor):
    device = SimpleNamespace(is_closed=state == "carrier_closed", _sessions={})
    requester = Mock(_error=None)
    session = EIPSession(device, requester, SimpleNamespace(session_id="session"))
    session._ready = state != "not_ready"
    if state == "closed":
        session._closed = True
    elif state == "detached":
        session._host_binding = True
        session.detach_host_binding()
    requester.reset_mock()
    with pytest.raises(EIPSessionStateError, match="closed or not ready"):
        if accessor == "client":
            _ = session.client
        else:
            _ = session.requester
    assert requester.mock_calls == []


def test_ready_session_access_uses_local_state_without_io():
    requester = Mock()
    session = EIPSession(SimpleNamespace(is_closed=False), requester, SimpleNamespace())
    session._ready = True
    for _ in range(10):
        assert session.client is session._client
        assert session.requester is requester
    assert requester.mock_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("accessor", ["client", "requester", "describe"])
async def test_session_guard_preserves_carrier_failure_before_keepalive_observes_it(accessor):
    failure = EIPTransportClosedError("Carrier reached EOF")
    requester = Mock(_error=failure)
    session = EIPSession(SimpleNamespace(is_closed=True), requester, SimpleNamespace())
    session._ready = True
    assert session._error is None
    with pytest.raises(EIPSessionStateError) as captured:
        if accessor == "client":
            _ = session.client
        elif accessor == "requester":
            _ = session.requester
        else:
            await session.describe()
    assert captured.value.__cause__ is failure
    assert requester.mock_calls == []


def binary() -> Path:
    configured = os.environ.get("A13N_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("set A13N_ENVD_TEST_BINARY for native Device tests")
    return Path(configured).resolve()


def device_path(path: Path) -> str:
    value = path.as_posix()
    return f"/{value}" if os.name == "nt" else value


@asynccontextmanager
async def running_device(
    tmp_path: Path,
    carrier: str,
    *,
    limits: dict[str, int] | None = None,
    monitoring: dict[str, object] | None = None,
    peer: bool = False,
):
    executable = binary()
    root = tmp_path / "alpha"
    root.mkdir()
    (tmp_path / "beta").mkdir()
    credential = tmp_path / "credential"
    credential.write_text("fixture-device-token")
    config = tmp_path / "envd.json"
    config.write_text(
        json.dumps(
            {
                "device_id": "device-test",
                "default_working_directory": str(root),
                "limits": {"max_sessions": 2, **(limits or {})},
                "monitoring": monitoring,
                "trusted_executable_roots": ["/usr/bin", "/bin"] if os.name != "nt" else [],
            }
        )
    )
    environment = {
        "A13N_ENVD_RUNTIME_DIR": str(tmp_path / "runtime"),
        "LANG": "C.UTF-8",
    }
    if os.name == "nt":
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    server = None
    accepted = asyncio.Queue()
    if carrier == "http":
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        environment.update(
            {
                "A13N_ENVD_TRANSPORT": "http",
                "A13N_ENVD_HTTP_BIND": f"127.0.0.1:{port}",
                "A13N_ENVD_HTTP_CREDENTIAL_FILE": str(credential),
                "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "loopback",
            }
        )
    elif carrier == "websocket":

        async def handler(connection):
            await accepted.put(connection)
            await connection.wait_closed()

        async def authorize(connection, request):
            if request.headers.get("Authorization") != "Bearer fixture-device-token":
                return connection.respond(401, "unauthorized")
            return None

        server = await serve(
            handler, "127.0.0.1", 0, subprotocols=["eip.v1"], compression=None, process_request=authorize
        )
        port = server.sockets[0].getsockname()[1]
        environment.update(
            {
                "A13N_ENVD_TRANSPORT": "reverse_websocket",
                "A13N_ENVD_REVERSE_WS_URL": f"ws://127.0.0.1:{port}/eip",
                "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE": str(credential),
            }
        )
    process = await asyncio.create_subprocess_exec(
        str(executable),
        "--config",
        str(config),
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    device = None
    second = None
    try:
        if carrier == "http":
            for _ in range(100):
                if process.returncode is not None:
                    assert process.stderr is not None
                    pytest.fail((await process.stderr.read()).decode())
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                except OSError:
                    await asyncio.sleep(0.03)
                else:
                    writer.close()
                    await writer.wait_closed()
                    break
            transport = HttpTransport(f"http://127.0.0.1:{port}", "fixture-device-token")
        elif carrier == "websocket":
            transport = AcceptedWebSocketTransport(await asyncio.wait_for(accepted.get(), 5))
        else:
            transport = StdioTransport.from_process(process)
        device = await EIPDeviceConnection.initialize(transport, expected_device_id="device-test", request_timeout=2)
        if peer:
            assert carrier == "http"
            second = await EIPDeviceConnection.initialize(
                HttpTransport(f"http://127.0.0.1:{port}", "fixture-device-token"),
                expected_device_id="device-test",
                request_timeout=2,
            )
            yield device, second
        else:
            yield device
    finally:
        if second is not None:
            await second.close()
        if device is not None:
            await device.close()
        terminated_by_test = carrier != "stdio" and process.returncode is None
        if terminated_by_test:
            process.terminate()
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        finally:
            if server is not None:
                server.close()
                await server.wait_closed()
        assert process.stderr is not None
        stderr = await process.stderr.read()
        expected_code = 1 if os.name == "nt" and terminated_by_test else 0
        assert process.returncode == expected_code, stderr.decode(errors="replace")
        assert b"fixture-device-token" not in stderr


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_real_text_byte_budget_and_continuation(tmp_path: Path, carrier: str):
    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            path = tmp_path / "alpha" / "guidance.txt"
            content = ("x" * 79 + "\n") * 1000
            path.write_text(content)
            async with await device.open_session() as session:
                params = FileReadTextParams(
                    context=EIPCallContext(operation_id="op-bounded-context"),
                    path=EIPPath(path=device_path(path)),
                    line_limit=1000,
                    max_line_length=4000,
                    text_byte_limit=128 * 1024,
                )
                page = await session.client.file_read_text(params)
                assert page.text == content and page.lines_read == 1000
                assert not page.has_more and page.truncated_lines == ()
                path.write_text("中文\r\n中文\r\ntail")
                params = params.model_copy(update={"text_byte_limit": 8})
                pages = []
                while True:
                    page = await session.client.file_read_text(params)
                    assert len(page.text.encode()) <= 8 and page.lines_read > 0
                    pages.append(page.text)
                    if not page.has_more:
                        break
                    params = params.model_copy(update={"line_offset": page.line_offset + page.lines_read})
                assert "".join(pages) == path.read_bytes().decode()
                params = params.model_copy(update={"line_offset": 0, "text_byte_limit": 1})
                with pytest.raises(EIPMethodError) as caught:
                    await session.client.file_read_text(params)
                assert caught.value.error.data.error_type == ErrorType.OUTPUT_LIMIT_EXCEEDED

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["transport", "timeout", "closed"])
def test_final_keepalive_preserves_body_exception(tmp_path, monkeypatch, failure):
    from a13n_envd_client.errors import EIPTransportError

    async def scenario():
        async with running_device(tmp_path, "http") as device:
            session = await device.open_session()
            calls = []

            async def fail(params):
                calls.append(params)
                if failure == "timeout":
                    raise TimeoutError("final keepalive timed out")
                raise EIPTransportError("final keepalive failed")

            monkeypatch.setattr(session._client, "session_keepalive", fail)
            with pytest.raises(ValueError, match="body failed"):
                async with session.use():
                    if failure == "closed":
                        await device.close()
                    raise ValueError("body failed")
            assert len(calls) == (0 if failure == "closed" else 1)
            assert session._users == 0
            assert session._keepalive_task is not None
            await asyncio.gather(session._keepalive_task, return_exceptions=True)
            assert session._keepalive_task.done()

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_real_shared_device_independent_sessions_and_transfers(tmp_path: Path, carrier: str):
    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            assert (await device.describe()).device_id == "device-test"
            discovery = await device.list_directories(
                DirectoryListParams(
                    expected_device_id="device-test",
                    expected_generation=device.descriptor.generation,
                    path=device_path(tmp_path),
                    limit=1,
                )
            )
            assert len(discovery.entries) == 1 and discovery.next_offset is not None
            alpha, beta = await asyncio.gather(
                device.open_session(
                    configuration=SessionConfiguration(working_directory=device_path(tmp_path / "alpha"))
                ),
                device.open_session(
                    configuration=SessionConfiguration(working_directory=device_path(tmp_path / "beta"))
                ),
            )
            assert alpha.device_descriptor == beta.device_descriptor == device.descriptor
            assert alpha.session_id != beta.session_id
            assert alpha.generation == beta.generation == device.descriptor.generation
            payload = bytes(range(256)) * 128
            path = EIPPath(path=device_path(tmp_path / "beta" / "binary.dat"))
            async with beta.open_writer(path, mode=FileWriteMode.CREATE) as writer:
                await writer.write(payload[:777])
                # A's close must not close B's live upload or the shared carrier.
                await alpha.close()
                await writer.write(payload[777:])
                committed = await writer.commit()
                assert committed.transferred_bytes == len(payload)
            async with beta.open_reader(path) as reader:
                received = b"".join([chunk async for chunk in reader])
            assert received == payload == (tmp_path / "beta" / "binary.dat").read_bytes()
            gamma = await device.open_session(
                configuration=SessionConfiguration(working_directory=device_path(tmp_path / "alpha"))
            )
            # cwd is not a file-access boundary; Device absolute paths are shared.
            text = tmp_path / "beta" / "outside.txt"
            text.write_text("outside selected cwd")
            result = await gamma.client.file_read_text(
                FileReadTextParams(
                    context=EIPCallContext(operation_id="op-outside"),
                    path=EIPPath(path=device_path(text)),
                    line_limit=10,
                    max_line_length=200,
                )
            )
            assert result.text == "outside selected cwd"
            if os.name != "nt":
                result = await gamma.client.shell_exec(
                    ShellExecParams(
                        context=EIPCallContext(operation_id="op-cwd"),
                        request=CommandRequest(
                            command=ArgvCommand(kind="argv", executable_spec=ExecutableName(kind="name", name="pwd"))
                        ),
                    )
                )
                chunks = [chunk async for chunk in gamma.open_output(result.output.stdout.reference)]
                assert b"".join(chunks).decode().strip() == str(tmp_path / "alpha")
            await beta.close()
            await gamma.close()

    asyncio.run(scenario())


def test_bad_session_open_does_not_break_existing_session(tmp_path: Path):
    async def scenario():
        async with running_device(tmp_path, "stdio") as device:
            session = await device.open_session()
            with pytest.raises(EIPMethodError):
                await device.open_session(
                    configuration=SessionConfiguration(working_directory=device_path(tmp_path / "absent"))
                )
            assert (await session.readiness()).ready
            await session.close()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="directory symlink creation requires Windows privileges")
@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_session_open_captures_resolved_directory(tmp_path: Path, carrier: str):
    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            alias = tmp_path / "alias"
            alias.symlink_to(tmp_path / "alpha", target_is_directory=True)
            async with await device.open_session(
                configuration=SessionConfiguration(working_directory=device_path(alias))
            ) as session:
                resolved = device_path((tmp_path / "alpha").resolve())
                assert session.descriptor.working_directory == resolved
                assert (await session.describe()).working_directory == resolved

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
@pytest.mark.parametrize("cancel", [True, False])
def test_late_open_releases_native_capacity_without_closing_sibling(tmp_path: Path, carrier: str, cancel: bool):
    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            sibling = await device.open_session()
            coordinator = device._requester
            receive = coordinator._handle_control
            delayed = asyncio.Queue()

            def hold_open(frame):
                response = json.loads(frame.payload)
                descriptor = response.get("result", {}).get("descriptor", {})
                if descriptor.get("session_id"):
                    delayed.put_nowait(frame)
                else:
                    receive(frame)

            coordinator._handle_control = hold_open
            coordinator._request_timeout = 0.1
            task = asyncio.create_task(device.open_session())
            frame = await asyncio.wait_for(delayed.get(), 2)
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else EIPRequestTimeoutError):
                await task
            coordinator._handle_control = receive
            coordinator._request_timeout = 2
            receive(frame)
            async with asyncio.timeout(2):
                while coordinator._device_admission.active:
                    coordinator._pending_changed.clear()
                    await coordinator._pending_changed.wait()
            # max_sessions=2: a leaked open would leave no slot beside sibling.
            async with await device.open_session() as replacement:
                assert replacement.session_id != sibling.session_id
                assert (await sibling.readiness()).ready
            await sibling.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_slow_transfer_backpressures_without_stalling_sibling(tmp_path: Path, carrier: str):
    async def scenario():
        limits = {
            "max_concurrent_file_transfers": 1,
            "max_device_file_transfers": 2,
            "max_transfer_frame_bytes": 65_536,
        }
        async with running_device(tmp_path, carrier, limits=limits) as device:
            async with asyncio.timeout(15):
                alpha, beta = await asyncio.gather(device.open_session(), device.open_session())
                payload = bytes(range(256)) * 8192
                path = EIPPath(path=device_path(tmp_path / "beta" / "large.bin"))
                async with beta.open_writer(path, mode=FileWriteMode.CREATE) as writer:
                    await writer.write(payload)
                    assert (await writer.commit()).transferred_bytes == len(payload)
                # Fill the permitted window, then abandon it. Neither overflow
                # nor duplicate RESET may poison this Session or its sibling.
                for _ in range(3):
                    async with beta.open_reader(path) as reader:
                        assert await anext(reader)
                        await asyncio.sleep(0.1)
                        assert (await alpha.readiness()).ready
                        assert (await device.describe()).device_id == "device-test"
                async with beta.open_reader(path) as reader:
                    chunks = []
                    async for chunk in reader:
                        await asyncio.sleep(0.003)
                        chunks.append(chunk)
                assert b"".join(chunks) == payload
                await beta.close()
                assert (await alpha.readiness()).ready
                await alpha.close()

    asyncio.run(scenario())


def test_native_and_restricted_sessions_share_device_not_filesystem_policy(tmp_path: Path):
    import shutil
    import sys

    from a13n_envd_client.eip.v1 import EgressMode, GrantAccess, RestrictedSandbox, SandboxGrant

    if sys.platform != "linux" or shutil.which("bwrap") is None:
        pytest.skip("requires Linux bubblewrap")

    async def scenario():
        async with running_device(tmp_path, "stdio") as device:
            visible = tmp_path / "alpha" / "visible.txt"
            hidden = tmp_path / "beta" / "hidden.txt"
            visible.write_text("visible")
            hidden.write_text("hidden")
            ordinary = await device.open_session()
            restricted = await device.open_session(
                configuration=SessionConfiguration(
                    working_directory=device_path(tmp_path / "alpha"),
                    sandbox=RestrictedSandbox(
                        mode="restricted",
                        grants=(
                            SandboxGrant(
                                path=device_path(tmp_path / "alpha"),
                                access=GrantAccess.READ_ONLY,
                            ),
                        ),
                    ),
                    egress_mode=EgressMode.DENY,
                    commands=False,
                )
            )
            assert ordinary.descriptor.boundary.sandbox.mode == "disabled"
            assert restricted.descriptor.boundary.sandbox.mode == "restricted"
            assert restricted.descriptor.boundary.egress == EgressMode.DENY
            assert device.descriptor.boundary.sandbox.mode == "disabled"
            assert (
                await restricted.client.file_read_text(
                    FileReadTextParams(
                        context=EIPCallContext(operation_id="read-visible"),
                        line_limit=10,
                        max_line_length=100,
                        path=EIPPath(path=device_path(visible)),
                    )
                )
            ).text == "visible"
            with pytest.raises(EIPMethodError):
                await restricted.client.file_read_text(
                    FileReadTextParams(
                        context=EIPCallContext(operation_id="read-hidden"),
                        line_limit=10,
                        max_line_length=100,
                        path=EIPPath(path=device_path(hidden)),
                    )
                )
            saved = restricted.descriptor
            await restricted.detach()
            attached = await device.attach_session(saved)
            assert attached.descriptor.boundary == saved.boundary
            await attached.close()
            assert (
                await ordinary.client.file_read_text(
                    FileReadTextParams(
                        context=EIPCallContext(operation_id="native-read-hidden"),
                        line_limit=10,
                        max_line_length=100,
                        path=EIPPath(path=device_path(hidden)),
                    )
                )
            ).text == "hidden"
            await ordinary.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_sessions_select_independent_configuration_on_one_device(tmp_path: Path, carrier: str):
    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            first = await device.open_session(
                configuration=SessionConfiguration(
                    working_directory=device_path(tmp_path / "alpha"),
                    commands=False,
                    lifecycle=SessionLifecyclePolicy(idle_timeout_ms=60_000),
                )
            )
            second = await device.open_session(
                configuration=SessionConfiguration(
                    working_directory=device_path(tmp_path / "beta"),
                    commands=True,
                    lifecycle=SessionLifecyclePolicy(idle_timeout_ms=120_000),
                ),
                required_methods=("shell.exec",),
            )
            assert first.session_id != second.session_id
            assert first.descriptor.generation == second.descriptor.generation
            assert first.descriptor.path_style == second.descriptor.path_style
            assert first.descriptor.system_info.os == second.descriptor.system_info.os
            assert "shell.exec" not in first.descriptor.available_methods
            assert not first.descriptor.shell_profiles
            assert "shell.exec" in second.descriptor.available_methods
            await first.close()
            before = second.descriptor
            assert await second.describe() == before
            await second.detach()
            attached = await device.attach_session(before)
            assert attached.descriptor.lifecycle.idle_timeout_ms == 120_000
            assert attached.descriptor.working_directory == device_path(tmp_path / "beta")
            await attached.close()

    asyncio.run(scenario())


def test_session_configuration_mismatch_closes_unpublished_session(tmp_path, monkeypatch):
    from a13n_envd_client import EIPProtocolError
    from a13n_envd_client.eip.v1 import EgressMode

    async def scenario():
        async with running_device(tmp_path, "stdio") as device:
            original = device._bind

            def tampered(descriptor):
                boundary = descriptor.boundary.model_copy(update={"egress": EgressMode.DENY})
                return original(descriptor.model_copy(update={"boundary": boundary}))

            monkeypatch.setattr(device, "_bind", tampered)
            for _ in range(3):
                with pytest.raises(EIPProtocolError, match="network mode"):
                    await device.open_session()
                assert not device._sessions
            monkeypatch.setattr(device, "_bind", original)
            async with await device.open_session() as session:
                assert session.descriptor.boundary == device.descriptor.boundary

    asyncio.run(scenario())


def test_session_policy_presence_must_match_selected_mode_before_dispatch(tmp_path, monkeypatch):
    from a13n_envd_client.eip.v1 import EgressMode, EgressPolicy, PublicDestinations

    async def scenario():
        async with running_device(tmp_path, "stdio") as device:
            policy = EgressPolicy(destinations=PublicDestinations(mode="public"))
            with pytest.raises(ValueError, match="exactly for controlled"):
                await device.open_session(configuration=SessionConfiguration(egress=policy))
            boundary = device.descriptor.boundary.model_copy(update={"egress": EgressMode.CONTROLLED})
            monkeypatch.setattr(device, "_descriptor", device.descriptor.model_copy(update={"boundary": boundary}))
            with pytest.raises(ValueError, match="exactly for controlled"):
                await device.open_session()
            assert not device._sessions

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_monitoring_native_sample_without_session_and_after_session_close(tmp_path: Path, carrier: str):
    import sys

    async def scenario():
        options = {
            "sample_interval_ms": 1000,
            "filesystems": {"workspace": str(tmp_path), "missing": str(tmp_path / "missing")},
        }
        async with running_device(tmp_path, carrier, monitoring=options) as device:
            descriptor = await device.describe()
            assert descriptor.system_info is not None
            expected_os = {"linux": "linux", "darwin": "macos", "win32": "windows"}[sys.platform]
            assert descriptor.system_info.os.value == expected_os
            assert descriptor.system_info.architecture
            assert descriptor.system_info.os_version
            assert descriptor.system_info.kernel_version
            assert "device.metrics" in descriptor.available_methods
            assert not device._sessions
            for identity, generation in [
                ("other-device", descriptor.generation),
                (descriptor.device_id, descriptor.generation + 1),
            ]:
                with pytest.raises(EIPMethodError) as mismatch:
                    await device._client.device_metrics(
                        DeviceMetricsParams(expected_device_id=identity, expected_generation=generation)
                    )
                assert mismatch.value.error.data.error_type == ErrorType.PROTOCOL_INCOMPATIBLE
            async with asyncio.timeout(10):
                while True:
                    report = await device.metrics()
                    if report.sample is not None and report.sample.system.cpu_busy_percent is not None:
                        break
                    await asyncio.sleep(0.03)
            assert report.device_id == descriptor.device_id
            assert report.generation == descriptor.generation
            assert report.sample_interval_ms == 1000
            sample = report.sample
            assert sample.sequence >= 2
            assert sample.sample_age_ms >= sample.collection_duration_ms
            assert sample.system.logical_cpu_count is not None and sample.system.logical_cpu_count > 0
            assert sample.system.memory_total_bytes is not None and sample.system.memory_total_bytes > 0
            assert sample.system.memory_available_bytes is not None
            assert 0 <= sample.system.memory_available_bytes <= sample.system.memory_total_bytes
            assert 0 <= sample.system.cpu_busy_percent <= 100
            filesystem = sample.filesystems["workspace"].metrics
            assert filesystem is not None
            assert filesystem.total_bytes > 0
            assert filesystem.free_bytes >= 0 and filesystem.available_bytes >= 0
            assert sample.filesystems["missing"].metrics is None
            assert sample.cgroup_v2 is None
            if sys.platform == "win32":
                assert filesystem.total_inodes is None
                assert sample.system.pressure is None
            elif sys.platform == "darwin":
                assert sample.system.cpu_time_seconds is None
                assert sample.system.pressure is None
            else:
                assert sample.system.cpu_time_seconds is not None
                assert filesystem.total_inodes is not None
            assert not device._sessions
            # Polling can cross a scheduled tick, but does not manufacture samples.
            sequences = []
            for _ in range(10):
                cached = (await device.metrics()).sample
                assert cached is not None
                sequences.append(cached.sequence)
            assert len(set(sequences)) < len(sequences)
            session = await device.open_session(
                configuration=SessionConfiguration(working_directory=device_path(tmp_path / "alpha"))
            )
            await session.close()
            async with asyncio.timeout(5):
                while True:
                    later = (await device.metrics()).sample
                    assert later is not None
                    if later.sequence > sample.sequence:
                        break
                    await asyncio.sleep(0.03)

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_monitoring_disabled_keeps_system_information(tmp_path: Path, carrier: str):
    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            assert (await device.describe()).system_info is not None
            assert "device.metrics" not in device.descriptor.available_methods
            with pytest.raises(EIPSessionStateError, match="does not advertise"):
                await device.metrics()
            with pytest.raises(EIPMethodError) as disabled:
                await device._client.device_metrics(
                    DeviceMetricsParams(
                        expected_device_id=device.descriptor.device_id, expected_generation=device.descriptor.generation
                    )
                )
            assert disabled.value.error.data.error_type == ErrorType.UNSUPPORTED
            assert not device._sessions

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_session_usage_detach_and_idle_expiry(tmp_path: Path, carrier: str):
    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            policy = SessionLifecyclePolicy(idle_timeout_ms=300)
            session = await device.open_session(configuration=SessionConfiguration(lifecycle=policy))
            assert session.descriptor.lifecycle == policy
            assert device.descriptor.lifecycle.idle_timeout_ms == 3_600_000
            assert device.descriptor.lifecycle.max_lifetime_ms is None
            async with session.use():
                await asyncio.sleep(0.8)
                assert (await session.readiness()).ready
            saved = session.descriptor
            await session.detach()
            attached = await device.attach_session(saved)
            assert attached.session_id == saved.session_id
            assert attached.descriptor.owner_epoch == saved.owner_epoch + 1
            # Keeping the Python object and Device connection does not own use.
            await asyncio.sleep(0.5)
            with pytest.raises(EIPMethodError) as expired:
                await attached.readiness()
            assert expired.value.error.data.error_type is ErrorType.NOT_INITIALIZED
            await attached.abort()
            replacement = await device.open_session(configuration=SessionConfiguration(lifecycle=policy))
            assert replacement.session_id != saved.session_id
            await replacement.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_max_lifetime_is_not_renewed_by_use_or_attachment(tmp_path: Path, carrier: str):
    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            session = await device.open_session(
                configuration=SessionConfiguration(
                    lifecycle=SessionLifecyclePolicy(idle_timeout_ms=2000, max_lifetime_ms=500)
                )
            )
            saved = session.descriptor
            async with session.use():
                await asyncio.sleep(0.3)
            await session.detach()
            attached = await device.attach_session(saved)
            await asyncio.sleep(0.3)
            with pytest.raises(EIPMethodError) as expired:
                await attached.readiness()
            assert expired.value.error.data.error_type is ErrorType.NOT_INITIALIZED
            await attached.abort()

    asyncio.run(scenario())


def test_http_takeover_fences_old_control_and_preserves_native_job(tmp_path: Path):
    if os.name == "nt":
        pytest.skip("uses the POSIX shell fixture")

    async def scenario():
        async with running_device(tmp_path, "http", peer=True) as (first, second):
            original = await first.open_session()
            started = await original.client.process_start(
                ProcessStartParams(
                    context=EIPCallContext(operation_id="start-once"),
                    request=CommandRequest(
                        command=ArgvCommand(
                            kind="argv",
                            executable_spec=ExecutableName(kind="name", name="sh"),
                            arguments=("-c", "echo admitted >> admissions; sleep 2; echo done"),
                        )
                    ),
                )
            )
            saved = original.descriptor
            successor = await second.attach_session(saved)
            assert successor.descriptor.owner_epoch == 2
            for method in (
                lambda: original.client.session_keepalive(SessionKeepaliveParams()),
                lambda: original.client.process_kill(
                    ProcessKillParams(
                        context=EIPCallContext(operation_id="stale-kill"),
                        handle=started.process.handle,
                    )
                ),
                original.close,
            ):
                with pytest.raises(EIPMethodError) as stale:
                    await method()
                assert stale.value.error.data.error_type is ErrorType.CONFLICT
            # A stale owner cannot claim the epoch already installed by its successor.
            with pytest.raises(EIPMethodError) as stale_attach:
                await first.attach_session(saved)
            assert stale_attach.value.error.data.error_type is ErrorType.CONFLICT
            async with successor.use():
                await asyncio.sleep(2.2)
                observed = await successor.client.process_inspect(
                    ProcessInspectParams(
                        context=EIPCallContext(operation_id="inspect-original"),
                        handle=started.process.handle,
                    )
                )
                assert observed.process.handle == started.process.handle
                assert observed.process.status.exit_code == 0
            assert (tmp_path / "alpha" / "admissions").read_text() == "admitted\n"
            reader = successor.open_output(observed.process.output.stdout.reference)
            assert (await reader.read_page()).data == b"done\n"
            await successor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("noisy", [False, True])
def test_owned_native_job_lives_and_unowned_job_expires(tmp_path: Path, noisy: bool):
    if os.name == "nt":
        pytest.skip("uses the POSIX shell fixture")

    async def scenario():
        async with running_device(tmp_path, "http") as device:
            session = await device.open_session(
                configuration=SessionConfiguration(lifecycle=SessionLifecyclePolicy(idle_timeout_ms=300))
            )
            script = "while :; do echo noisy; sleep 0.02; done" if noisy else "sleep 30"
            async with session.use():
                process = await session.client.process_start(
                    ProcessStartParams(
                        context=EIPCallContext(operation_id="start-owned"),
                        request=CommandRequest(
                            command=ArgvCommand(
                                kind="argv",
                                executable_spec=ExecutableName(kind="name", name="sh"),
                                arguments=("-c", script),
                            )
                        ),
                    )
                )
                await asyncio.sleep(0.9)
                observed = await session.client.process_inspect(
                    ProcessInspectParams(
                        context=EIPCallContext(operation_id="still-owned"),
                        handle=process.process.handle,
                    )
                )
                assert observed.process.status.phase.value == "running"
            saved = session.descriptor
            await session.detach()
            await asyncio.sleep(0.6)
            with pytest.raises(EIPMethodError) as expired:
                await device.attach_session(saved)
            assert expired.value.error.data.error_type is ErrorType.NOT_INITIALIZED

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_real_document_discovery_order_limits_and_read_failures(tmp_path: Path, carrier: str):
    from a13n_envd_client.eip.v1 import FileDiscoverDocumentsParams, FileDocumentFailure, FileDocumentText

    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            root = tmp_path / "alpha"
            for name in ("z-last", "a-first", "invalid", "missing", ".hidden"):
                (root / name).mkdir()
            (root / "SKILL.md").write_text("root\n")
            (root / "z-last" / "SKILL.md").write_text("last\n")
            (root / "a-first" / "SKILL.md").write_bytes("\ufeff---\r\nname: 中\r\n---\r\n".encode())
            (root / "invalid" / "SKILL.md").write_bytes(b"not\x00text")
            (root / ".hidden" / "SKILL.md").write_text("hidden")
            (root / "other-file").write_text("counts toward entry bound")
            session = await device.open_session()
            assert session.max_response_bytes == 1024 * 1024
            assert session.descriptor.limits.max_response_bytes > session.max_response_bytes
            params = FileDiscoverDocumentsParams(
                context=EIPCallContext(operation_id="discover-root"),
                root=EIPPath(path=device_path(root)),
                basename="SKILL.md",
                max_entries=6,
                line_limit=256,
                max_line_length=16384,
                result_byte_limit=session.max_response_bytes - 4096,
            )
            result = await session.client.file_discover_documents(params)
            assert not result.entries_exceeded and result.next_position is None
            assert result.omitted_unrepresentable_entries == 0
            assert len(result.documents) == 4
            first, second, failure, last = result.documents
            assert isinstance(first, FileDocumentText) and first.prefix.text == "root\n"
            assert isinstance(second, FileDocumentText) and second.prefix.text.startswith("\ufeff---\r\n")
            assert isinstance(failure, FileDocumentFailure)
            assert failure.phase.value == "read" and failure.error.data.error_type is ErrorType.UNSUPPORTED
            assert isinstance(last, FileDocumentText) and last.prefix.text == "last\n"
            overflow = await session.client.file_discover_documents(
                params.model_copy(
                    update={
                        "context": EIPCallContext(operation_id="discover-overflow"),
                        "max_entries": 5,
                    }
                )
            )
            assert overflow.entries_exceeded and not overflow.documents and overflow.next_position is None
            for index, basename in enumerate(("../SKILL.md", "a/b", "a\\b", ".", "..")):
                with pytest.raises(EIPMethodError) as invalid:
                    await session.client.file_discover_documents(
                        params.model_copy(
                            update={
                                "context": EIPCallContext(operation_id=f"invalid-basename-{index}"),
                                "basename": basename,
                            }
                        )
                    )
                assert invalid.value.error.data.error_type is ErrorType.INVALID_PARAMS

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_real_document_discovery_fragmented_encoded_budget(tmp_path: Path, carrier: str):
    import hashlib

    from a13n_envd_client.eip.v1 import FileDiscoverDocumentsParams, FileDocumentText, encode_model

    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            root = tmp_path / "alpha"
            # Large valid frontmatter cannot be replaced by a small per-document cap.
            text = "---\nname: large\ndescription: valid\n" + ('# 中\\"\t' * 1500 + "\n") * 100 + "---\n"
            assert len(text.encode()) > 1024 * 1024
            (root / "SKILL.md").write_text(text, encoding="utf-8", newline="")
            session = await device.open_session()
            position = None
            fragments = []
            observed_digest = None
            for index in range(100):
                budget = session.max_response_bytes - 4096
                result = await session.client.file_discover_documents(
                    FileDiscoverDocumentsParams(
                        context=EIPCallContext(operation_id=f"fragment-{index}"),
                        root=EIPPath(path=device_path(root)),
                        basename="SKILL.md",
                        max_entries=10,
                        line_limit=256,
                        max_line_length=16384,
                        result_byte_limit=budget,
                        position=position,
                    )
                )
                assert len(encode_model(result)) <= budget
                assert not result.entries_exceeded and len(result.documents) == 1
                document = result.documents[0]
                assert isinstance(document, FileDocumentText)
                assert document.text_byte_offset == sum(len(part.encode()) for part in fragments)
                assert document.prefix_byte_length == len(text.encode())
                assert not document.prefix.truncated_lines and not document.prefix.has_more
                digest = document.prefix_digest.value
                assert observed_digest is None or observed_digest == digest
                observed_digest = digest
                fragments.append(document.prefix.text)
                if result.next_position is None:
                    break
                assert result.next_position.text_byte_offset > document.text_byte_offset
                position = result.next_position
            else:
                pytest.fail("discovery did not make finite progress")
            assert index > 0 and "".join(fragments) == text
            assert observed_digest == hashlib.sha256(text.encode()).hexdigest()
            with pytest.raises(EIPMethodError) as small:
                await session.client.file_discover_documents(
                    FileDiscoverDocumentsParams(
                        context=EIPCallContext(operation_id="too-small"),
                        root=EIPPath(path=device_path(root)),
                        basename="SKILL.md",
                        max_entries=10,
                        line_limit=256,
                        max_line_length=16384,
                        result_byte_limit=16,
                    )
                )
            assert small.value.error.data.error_type is ErrorType.OUTPUT_LIMIT_EXCEEDED

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="fixture uses POSIX symlinks")
def test_real_document_discovery_follows_document_not_directory_links(tmp_path: Path):
    from a13n_envd_client.eip.v1 import FileDiscoverDocumentsParams, FileDocumentText

    async def scenario():
        async with running_device(tmp_path, "stdio") as device:
            root = tmp_path / "alpha"
            external = tmp_path / "beta"
            (external / "SKILL.md").write_text("external\n")
            (root / "directory-link").symlink_to(external, target_is_directory=True)
            (root / "SKILL.md").symlink_to(external / "SKILL.md")
            (root / "nonregular").mkdir()
            (root / "nonregular" / "SKILL.md").mkdir()
            session = await device.open_session()
            result = await session.client.file_discover_documents(
                FileDiscoverDocumentsParams(
                    context=EIPCallContext(operation_id="symlinks"),
                    root=EIPPath(path=device_path(root)),
                    basename="SKILL.md",
                    max_entries=3,
                    line_limit=256,
                    max_line_length=16384,
                    result_byte_limit=session.max_response_bytes - 4096,
                )
            )
            assert result.next_position is None and len(result.documents) == 1
            document = result.documents[0]
            assert isinstance(document, FileDocumentText)
            assert document.prefix.text == "external\n"
            assert document.prefix.info.path.path == device_path(root / "SKILL.md")

    asyncio.run(scenario())


def test_real_document_discovery_batches_wide_root_and_pages_whole_outcomes(tmp_path: Path):
    from a13n_envd_client.eip.v1 import FileDiscoverDocumentsParams, FileDocumentText, encode_model

    async def scenario():
        async with running_device(tmp_path, "stdio") as device:
            root = tmp_path / "alpha"
            for index in range(250):
                directory = root / f"skill-{index:03}"
                directory.mkdir()
                (directory / "SKILL.md").write_text(f"---\nname: skill-{index:03}\ndescription: fixture\n---\n")
            session = await device.open_session()
            params = FileDiscoverDocumentsParams(
                context=EIPCallContext(operation_id="wide-root"),
                root=EIPPath(path=device_path(root)),
                basename="SKILL.md",
                max_entries=250,
                line_limit=256,
                max_line_length=16384,
                result_byte_limit=session.max_response_bytes - 4096,
            )
            result = await session.client.file_discover_documents(params)
            assert len(result.documents) == 250 and result.next_position is None
            assert all(isinstance(item, FileDocumentText) for item in result.documents)
            expected = [item.prefix.info.path.path for item in result.documents]
            position = None
            observed = []
            for index in range(250):
                page = await session.client.file_discover_documents(
                    params.model_copy(
                        update={
                            "context": EIPCallContext(operation_id=f"whole-page-{index}"),
                            "result_byte_limit": 4096,
                            "position": position,
                        }
                    )
                )
                assert len(encode_model(page)) <= 4096
                for item in page.documents:
                    assert isinstance(item, FileDocumentText) and item.text_byte_offset == 0
                    assert len(item.prefix.text.encode()) == item.prefix_byte_length
                    observed.append(item.prefix.info.path.path)
                if page.next_position is None:
                    break
                assert page.next_position.text_byte_offset == 0
                assert position is None or page.next_position.candidate_offset > position.candidate_offset
                position = page.next_position
            else:
                pytest.fail("whole-outcome pagination did not finish")
            assert index > 0 and observed == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["stdio", "http", "websocket"])
def test_real_tree_observation_bfs_and_independent_budgets(tmp_path: Path, carrier: str):
    from a13n_envd_client.eip.v1 import FileKind, FileTreeParams, encode_model

    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            root = tmp_path / "alpha"
            for name in ("a", "z", ".hidden"):
                (root / name).mkdir()
                (root / name / "child").write_text("content is not loaded")
            (root / "a.txt").write_text("content is not loaded")
            async with await device.open_session() as session:
                params = FileTreeParams(
                    context=EIPCallContext(operation_id="tree"),
                    root=EIPPath(path=device_path(root)),
                    max_depth=4,
                    max_entries=256,
                    max_directory_calls=64,
                    include_hidden=False,
                    metadata_byte_limit=16384,
                    path_prefix_bytes=123,
                    result_byte_limit=1024 * 1024 - 4096,
                )
                result = await session.client.file_tree(params)
                assert [entry.relative_path for entry in result.entries] == ["a", "a.txt", "z", "a/child", "z/child"]
                assert not result.truncated and result.directory_calls == 3
                first = result.entries[0]
                value = {"kind": first.info.kind.value, "path": "/a"}
                if first.info.size_bytes is not None:
                    value["size"] = first.info.size_bytes
                cost = len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()) + 124
                for label, update, count, calls in (
                    ("calls", {"max_directory_calls": 1}, 3, 1),
                    ("entries", {"max_entries": 1}, 1, 1),
                    ("bytes", {"metadata_byte_limit": cost}, 1, 1),
                    ("short", {"metadata_byte_limit": cost - 1}, 0, 1),
                    ("depth", {"max_depth": 1}, 3, 1),
                ):
                    bounded = await session.client.file_tree(
                        params.model_copy(
                            update={
                                **update,
                                "context": EIPCallContext(operation_id=f"tree-{label}"),
                            }
                        )
                    )
                    assert bounded.truncated and len(bounded.entries) == count and bounded.directory_calls == calls
                    assert all(entry.info.kind in (FileKind.FILE, FileKind.DIRECTORY) for entry in bounded.entries)
                clipped = await session.client.file_tree(
                    params.model_copy(
                        update={
                            "context": EIPCallContext(operation_id="tree-wire"),
                            "result_byte_limit": 512,
                        }
                    )
                )
                assert clipped.truncated and len(encode_model(clipped)) <= 512

    asyncio.run(scenario())
