from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from a13n_envd_client import (
    AcceptedWebSocketTransport,
    EIPDeviceConnection,
    EIPMethodError,
    EIPRequestTimeoutError,
    EIPSessionStateError,
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
    ShellExecParams,
)
from websockets.asyncio.server import serve


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
    tmp_path: Path, carrier: str, *, limits: dict[str, int] | None = None, monitoring: dict[str, object] | None = None
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
        yield device
    finally:
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
def test_real_shared_device_independent_sessions_and_transfers(tmp_path: Path, carrier: str):
    async def scenario():
        async with running_device(tmp_path, carrier) as device:
            assert device.protocol_version == "0.3"
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
                device.open_session(working_directory=device_path(tmp_path / "alpha")),
                device.open_session(working_directory=device_path(tmp_path / "beta")),
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
            gamma = await device.open_session(working_directory=device_path(tmp_path / "alpha"))
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
                await device.open_session(working_directory=device_path(tmp_path / "absent"))
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
            async with await device.open_session(working_directory=device_path(alias)) as session:
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


def test_session_boundary_mismatch_closes_unpublished_session(tmp_path, monkeypatch):
    from a13n_envd_client import EIPProtocolError

    async def scenario():
        async with running_device(tmp_path, "stdio") as device:
            original = device._bind

            def tampered(descriptor):
                boundary = descriptor.boundary.model_copy(update={"policy_digest": "0" * 64})
                return original(descriptor.model_copy(update={"boundary": boundary}))

            monkeypatch.setattr(device, "_bind", tampered)
            for _ in range(3):
                with pytest.raises(EIPProtocolError, match="boundary"):
                    await device.open_session()
                assert not device._sessions
            monkeypatch.setattr(device, "_bind", original)
            async with await device.open_session() as session:
                assert session.descriptor.boundary == device.descriptor.boundary

    asyncio.run(scenario())


def test_session_policy_presence_must_match_device_mode_before_dispatch(tmp_path, monkeypatch):
    from a13n_envd_client.eip.v1 import EgressMode, EgressPolicy, PublicDestinations

    async def scenario():
        async with running_device(tmp_path, "stdio") as device:
            policy = EgressPolicy(destinations=PublicDestinations(mode="public"))
            with pytest.raises(ValueError, match="exactly for controlled"):
                await device.open_session(egress=policy)
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
            session = await device.open_session(working_directory=device_path(tmp_path / "alpha"))
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
