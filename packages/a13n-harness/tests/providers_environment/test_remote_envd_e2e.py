"""Real-daemon remote Provider and Host SDK tests; no model or external service."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from a13n_envd_client.eip.v1 import DirectoryListParams, EIPPath, SessionConfiguration
from a13n_harness.environment.commands import ArgvCommand, CommandRequest, PortTarget
from a13n_harness.environment.envd_policy import EnvdSessionConfiguration
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.files import FileQueryRequest, FileTextSearchRequest
from a13n_harness.environment.models import EnvironmentError, EnvironmentState
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.environment.session_recovery import configuration_digest
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    RemoteEnvdEnvironmentConfiguration,
)
from a13n_harness.providers.environment.remote_envd.connections import WebSocketEnvdConnections
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD, HttpEnvdProviderRuntime
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD, WebSocketEnvdProviderRuntime
from pydantic import SecretStr
from websockets.asyncio.server import serve

pytestmark = pytest.mark.anyio
TOKEN = "remote-envd-test-token"
NATIVE_ID = "env-native"
LOGICAL_ID = "env-logical"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def binary():
    configured = os.environ.get("A13N_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("set A13N_ENVD_TEST_BINARY to run remote Envd integration tests")
    result = Path(configured)
    assert result.is_file()
    return result


def state(provider, native=NATIVE_ID):
    return EnvironmentState(provider_key=provider.type, state_version="1", state={"device_id": native})


@asynccontextmanager
async def daemon(binary, tmp_path, transport, address):
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    workspace.mkdir()
    runtime.mkdir()
    token = tmp_path / "token"
    token.write_text(TOKEN)
    config = tmp_path / "envd.json"
    config.write_text(
        json.dumps(
            {
                "default_working_directory": str(workspace),
                "limits": {"max_file_bytes": 1024 * 1024},
                "trusted_executable_roots": [str(Path(sys.executable).resolve().parent)],
            }
        )
    )
    env = {
        "A13N_ENVD_DEVICE_ID": NATIVE_ID,
        "A13N_ENVD_RUNTIME_DIR": str(runtime),
        "A13N_ENVD_TRANSPORT": transport,
    }
    if transport == "http":
        env.update(
            A13N_ENVD_HTTP_BIND=address,
            A13N_ENVD_HTTP_CREDENTIAL_FILE=str(token),
            A13N_ENVD_HTTP_PLAINTEXT_SCOPE="loopback",
        )
    else:
        env.update(A13N_ENVD_REVERSE_WS_URL=address, A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE=str(token))
    process = await asyncio.create_subprocess_exec(
        str(binary), "--config", str(config), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        if transport == "http":
            async with asyncio.timeout(5):
                while True:
                    try:
                        _, writer = await asyncio.open_connection("127.0.0.1", int(address.rsplit(":", 1)[1]))
                    except OSError:
                        await asyncio.sleep(0.02)
                        continue
                    writer.close()
                    await writer.wait_closed()
                    break
        yield process, workspace
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode(errors="replace")
        assert TOKEN.encode() not in stdout + stderr


@pytest.fixture
async def http_runtime():
    runtimes = []

    def create(endpoint, token=TOKEN):
        runtime = HttpEnvdProviderRuntime(
            HttpEnvdConnectionConfiguration(endpoint=endpoint, request_timeout=5),
            HttpEnvdCredential(token=SecretStr(token)),
        )
        runtimes.append(runtime)
        return runtime

    yield create
    await asyncio.gather(*(runtime.close() for runtime in runtimes))


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def connector(provider, recipe, *, environment_id, state, runtime):
    return provider.execution_connector(
        recipe, configuration=runtime.configuration, environment_id=environment_id, state=state, runtime=runtime
    )


async def exercise(environment):
    generation = environment.descriptor.generation
    root = environment.descriptor.working_directory
    assert root is not None
    await environment.check_ready(frozenset({"files", "shell", "processes", "outputs"}))
    files = environment.operations.files
    assert files is not None
    await files.write_text(f"{root}/text.txt", "remote envd\n", mode="create")
    assert (await files.read_text(f"{root}/text.txt")).text == "remote envd\n"
    query = await files.query(FileQueryRequest(root=root, pattern="*.{txt,py}", max_results=10))
    assert [entry.path for entry in query.entries] == [f"{root}/text.txt"]
    search = await files.search_text(
        FileTextSearchRequest(root=root, pattern="REMOTE", case_sensitive=False, include="*.{txt,py}", max_matches=10)
    )
    assert [match.text for match in search.matches] == ["remote envd"]
    single = await files.search_text(
        FileTextSearchRequest(
            root=f"{root}/text.txt", pattern="REMOTE", case_sensitive=False, include="/text.txt", max_matches=1
        )
    )
    assert [(match.path, match.text) for match in single.matches] == [(f"{root}/text.txt", "remote envd")]
    assert not single.has_more
    with pytest.raises(EnvironmentError) as missing:
        await files.search_text(FileTextSearchRequest(root=f"{root}/missing.txt", pattern="needle", max_matches=1))
    assert missing.value.code == "environment_not_found"
    assert missing.value.safe_projection()["message"]
    for arguments, field, reason in [
        ({"pattern": "(", "regex": True}, "pattern", "invalid_regex"),
        ({"pattern": "ok", "include": "{broken}"}, "include", "invalid_glob"),
        ({"pattern": ""}, "pattern", "empty_pattern"),
    ]:
        with pytest.raises(EnvironmentError) as error:
            await files.search_text(FileTextSearchRequest(root=root, max_matches=10, **arguments))
        assert error.value.code == "environment_request_invalid"
        assert error.value.details["field"] == field
        assert error.value.details["reason"] == reason
        assert error.value.details["hint"]
    payload = bytes(range(256)) * 100

    async def chunks():
        yield payload[:123]
        yield payload[123:]

    await files.write_bytes_stream(f"{root}/binary.dat", chunks(), mode="create")
    assert await files.read_bytes(f"{root}/binary.dat") == payload
    shell = environment.operations.shell
    assert shell is not None
    policy = EnvironmentOutputPolicy(max_inline_bytes=128, max_output_bytes=4096, overflow="retain")
    request = CommandRequest(
        command=ArgvCommand(executable=Path(sys.executable).resolve().name, arguments=("-c", "print('from-envd')")),
        cwd=root,
        output_policy=policy,
    )
    executed = await shell.exec(request)
    assert executed.status.exit_code == 0
    assert executed.output.stdout.inline == b"from-envd\n"
    processes = environment.operations.processes
    assert processes is not None
    started = await processes.start(request)
    handle = started.process.handle
    assert handle.identity.environment_id == LOGICAL_ID
    assert handle.execution_id == environment.execution_id
    info = await processes.wait(handle, condition="tree_cleaned", timeout_seconds=5)
    assert info.status.exit_code == 0
    await processes.release(handle)
    return generation


@pytest.mark.parametrize("detect_restart", ["describe", "session", "disconnect"])
async def test_http_runtime_replaces_stale_connection_on_next_explicit_use(
    binary, tmp_path, http_runtime, detect_restart
):
    from a13n_envd_client import EIPClientError

    address = f"127.0.0.1:{free_port()}"
    runtime = http_runtime(f"http://{address}")
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    async with daemon(binary, first_root, "http", address):
        first = await runtime.acquire_device(expected_device_id=NATIVE_ID)
        old_session = await first.open_session()
        original_generation = first.descriptor.generation
    try:
        if detect_restart == "disconnect":
            with pytest.raises(EIPClientError):
                await runtime.describe(expected_device_id=NATIVE_ID)
        async with daemon(binary, second_root, "http", address):
            # Detecting a lost generation fails that use; it never retries an open.
            with pytest.raises(EIPClientError):
                if detect_restart in {"describe", "disconnect"}:
                    # An individual failed HTTP request did not invalidate the
                    # Device. Its changed generation now ends the old scope.
                    await runtime.describe(expected_device_id=NATIVE_ID)
                else:
                    async with runtime.open_session(expected_device_id=NATIVE_ID, required_methods=frozenset()):
                        pytest.fail("A stale Session open must not be retried")
            current = await runtime.describe(expected_device_id=NATIVE_ID)
            assert current.generation != original_generation
            replacement = await runtime.acquire_device(expected_device_id=NATIVE_ID)
            assert replacement is not first
            with pytest.raises(EIPClientError):
                await old_session.readiness()
            async with runtime.open_session(expected_device_id=NATIVE_ID, required_methods=frozenset()) as binding:
                session = binding.session
                # A missing directory is a binding failure, not a lost Device.
                with pytest.raises(EIPClientError):
                    async with runtime.open_session(
                        expected_device_id=NATIVE_ID,
                        required_methods=frozenset(),
                        configuration=EnvdSessionConfiguration(working_directory=str(second_root / "missing")),
                    ):
                        pytest.fail("Opening a missing directory must fail")
                assert await runtime.acquire_device(expected_device_id=NATIVE_ID) is replacement
                assert session.descriptor.generation == current.generation
                assert session.descriptor.session_id != old_session.descriptor.session_id
                assert (await session.readiness()).ready
            assert await runtime.acquire_device(expected_device_id=NATIVE_ID) is replacement
    finally:
        await old_session.abort()


async def test_http_provider_operations_identity_and_sequential_reentry(binary, tmp_path, http_runtime):
    port = free_port()
    endpoint = f"http://127.0.0.1:{port}"
    provider = HTTP_ENVD
    async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}") as (process, workspace):
        env = connector(
            provider,
            RemoteEnvdEnvironmentConfiguration(),
            environment_id=LOGICAL_ID,
            state=state(provider),
            runtime=http_runtime(endpoint),
        )
        try:
            env = await env.open()
            generation = await exercise(env)
        finally:
            await env.close()
        assert process.returncode is None
        assert (workspace / "text.txt").read_text() == "remote envd\n"
        again = connector(
            provider,
            RemoteEnvdEnvironmentConfiguration(),
            environment_id=LOGICAL_ID,
            state=env.state,
            runtime=http_runtime(endpoint),
        )
        try:
            again = await again.open()
            assert again.descriptor.generation != generation
            assert again.descriptor.generation.split(":")[0] == generation.split(":")[0]
        finally:
            await again.close()


async def test_closed_eip_process_and_port_facets_raise_environment_errors(binary, tmp_path, http_runtime):
    port = free_port()
    provider = HTTP_ENVD
    async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}"):
        environment = connector(
            provider,
            RemoteEnvdEnvironmentConfiguration(),
            environment_id=LOGICAL_ID,
            state=state(provider),
            runtime=http_runtime(f"http://127.0.0.1:{port}"),
        )
        try:
            environment = await environment.open()
            processes, ports, shell = (
                environment.operations.processes,
                environment.operations.ports,
                environment.operations.shell,
            )
            policy = EnvironmentOutputPolicy(max_inline_bytes=8, max_output_bytes=4096, overflow="retain")
            request = CommandRequest(
                command=ArgvCommand(executable=Path(sys.executable).resolve().name, arguments=("-c", "print('done')")),
                output_policy=policy,
            )
            started = await processes.start(request)
            handle = started.process.handle
            await processes.wait(handle, condition="tree_cleaned", timeout_seconds=5)
        finally:
            await environment.close()
        target = PortTarget(port=12345)
        for operation in (
            lambda: processes.inspect(handle),
            lambda: processes.rebind(handle.identity, output_policy=policy),
            lambda: processes.read_output(handle, policy=policy),
            lambda: processes.write_stdin(handle, b"late"),
            lambda: processes.close_stdin(handle),
            lambda: processes.signal(handle, "terminate"),
            lambda: processes.wait(handle, condition="tree_cleaned", timeout_seconds=1),
            lambda: processes.kill(handle),
            lambda: processes.release(handle),
            lambda: processes.start(request),
            lambda: shell.exec(request),
            lambda: ports.inspect(target),
            lambda: ports.wait(target, desired="listening", timeout_seconds=1),
        ):
            with pytest.raises(EnvironmentError) as error:
                await operation()
            assert error.value.code == "environment_unavailable"


@pytest.mark.parametrize("failure", ["credential", "identity", "methods"])
async def test_http_provider_rejects_untrusted_or_incompatible_peer(binary, tmp_path, failure, http_runtime):
    port = free_port()
    provider = HTTP_ENVD
    endpoint = f"http://127.0.0.1:{port}"
    async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}"):
        selected = state(provider, "wrong-env" if failure == "identity" else NATIVE_ID)
        env = connector(
            provider,
            RemoteEnvdEnvironmentConfiguration(required_methods=("missing.method",) if failure == "methods" else ()),
            environment_id=LOGICAL_ID,
            state=selected,
            runtime=http_runtime(endpoint, "wrong-token" if failure == "credential" else TOKEN),
        )
        with pytest.raises(EnvironmentProviderError) as error:
            await env.open()
        assert TOKEN not in str(error.value)
        assert env.state == selected


async def test_http_device_discovery_and_independent_session_ownership(binary, tmp_path, http_runtime):
    port = free_port()
    provider = HTTP_ENVD
    async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}") as (_, workspace):
        runtime = http_runtime(f"http://127.0.0.1:{port}")
        device = await runtime.acquire_device(expected_device_id=NATIVE_ID)
        assert not device._sessions
        descriptor = await runtime.describe(expected_device_id=NATIVE_ID)
        listing = await runtime.list_directories(
            DirectoryListParams(
                expected_device_id=NATIVE_ID,
                expected_generation=descriptor.generation,
                path=str(workspace),
                limit=10,
            )
        )
        assert listing.path == str(workspace) and not device._sessions
        first, second = [
            connector(
                provider,
                RemoteEnvdEnvironmentConfiguration(working_directory=str(workspace)),
                environment_id=LOGICAL_ID,
                state=state(provider),
                runtime=runtime,
            )
            for _ in range(2)
        ]
        try:
            first, second = await asyncio.gather(first.open(), second.open())
            assert len(device._sessions) == 2
            assert first.descriptor.generation != second.descriptor.generation
            await first.close()
            assert len(device._sessions) == 1
            await second.operations.files.write_text(str(workspace / "survived"), "yes", mode="create")
            assert (workspace / "survived").read_text() == "yes"
        finally:
            await asyncio.gather(first.close(), second.close())
        assert not device._sessions
        assert (await runtime.describe(expected_device_id=NATIVE_ID)).device_id == NATIVE_ID


async def test_websocket_concurrent_scopes_keep_one_carrier_and_independent_processes(binary, tmp_path):
    async with WebSocketEnvdConnections() as hub:

        async def handler(connection):
            await hub.attach(NATIVE_ID, connection)

        async with serve(handler, "127.0.0.1", 0, subprotocols=["eip.v1"], compression=None) as server:
            port = server.sockets[0].getsockname()[1]
            async with daemon(binary, tmp_path, "reverse_websocket", f"ws://127.0.0.1:{port}/envd"):
                provider = WEBSOCKET_ENVD
                environments = [
                    connector(
                        provider,
                        RemoteEnvdEnvironmentConfiguration(),
                        environment_id=f"env-scope-{index}",
                        state=state(provider),
                        runtime=WebSocketEnvdProviderRuntime(hub),
                    )
                    for index in range(2)
                ]
                first, second = environments
                try:
                    environments = await asyncio.gather(*(environment.open() for environment in environments))
                    first, second = environments
                    attachment = hub._attachments[NATIVE_ID]
                    assert first.operations.processes is not second.operations.processes
                    processes = second.operations.processes
                    assert processes is not None
                    request = CommandRequest(
                        command=ArgvCommand(
                            executable=Path(sys.executable).resolve().name,
                            arguments=("-c", "import time; time.sleep(0.2); print('survived')"),
                        ),
                        cwd="/",
                        output_policy=EnvironmentOutputPolicy(
                            max_inline_bytes=128, max_output_bytes=4096, overflow="retain"
                        ),
                    )
                    started = await processes.start(request)
                    await first.close()
                    assert hub._attachments[NATIVE_ID] is attachment
                    info = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=5)
                    assert info.status.exit_code == 0
                    await processes.release(started.process.handle)
                    await second.close()
                    assert hub._attachments[NATIVE_ID] is attachment
                    async with hub.open_session(expected_device_id=NATIVE_ID, required_methods=frozenset()) as binding:
                        session = binding.session
                        assert (await session.readiness()).ready
                finally:
                    await asyncio.gather(*(environment.close() for environment in environments))


async def test_websocket_host_sdk_real_daemon_operations_and_reconnection(binary, tmp_path):
    errors = []
    async with WebSocketEnvdConnections() as hub:

        async def handler(connection):
            try:
                await hub.attach(NATIVE_ID, connection)
            except EnvironmentProviderError as error:
                errors.append(error)

        def authorize(connection, request):
            if request.headers.get("Authorization") != f"Bearer {TOKEN}":
                return connection.respond(401, "unauthorized")
            return None

        async with serve(
            handler, "127.0.0.1", 0, subprotocols=["eip.v1"], process_request=authorize, compression=None
        ) as server:
            port = server.sockets[0].getsockname()[1]
            async with daemon(binary, tmp_path, "reverse_websocket", f"ws://127.0.0.1:{port}/envd") as (process, _):
                provider = WEBSOCKET_ENVD
                runtime = WebSocketEnvdProviderRuntime(hub)
                env = connector(
                    provider,
                    RemoteEnvdEnvironmentConfiguration(),
                    environment_id=LOGICAL_ID,
                    state=state(provider),
                    runtime=runtime,
                )
                try:
                    env = await env.open()
                    generation = await exercise(env)
                finally:
                    await env.close()
                assert process.returncode is None
                again = connector(
                    provider,
                    RemoteEnvdEnvironmentConfiguration(),
                    environment_id=LOGICAL_ID,
                    state=env.state,
                    runtime=runtime,
                )
                try:
                    again = await again.open()
                    assert again.descriptor.generation != generation
                    assert again.descriptor.generation.split(":")[0] == generation.split(":")[0]
                finally:
                    await again.close()
    assert not errors


@pytest.mark.parametrize("closed", [False, True])
async def test_http_checkpoint_attaches_live_session_or_opens_after_close(binary, tmp_path, http_runtime, closed):
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState

    address = f"127.0.0.1:{free_port()}"
    endpoint = f"http://{address}"
    async with daemon(binary, tmp_path, "http", address):
        first = http_runtime(endpoint)
        device = await first.acquire_device(expected_device_id=NATIVE_ID)
        original = await device.open_session()
        saved = EnvdSessionState(
            target=endpoint,
            descriptor=original.descriptor,
            configuration_digest=configuration_digest(SessionConfiguration()),
        )
        # Round-trip the exact value a Host stores, without keeping a client object.
        recovery = EnvdSessionRecovery(EnvdSessionState.model_validate_json(saved.model_dump_json()))
        if closed:
            await original.close()
        restored = http_runtime(endpoint)
        async with restored.open_session(
            expected_device_id=NATIVE_ID, required_methods=frozenset(), session_recovery=recovery
        ) as binding:
            session = binding.session
            assert (session.session_id == original.session_id) is not closed
            assert (await session.readiness()).ready
            assert recovery.state is not None and recovery.state.descriptor == session.descriptor
            descriptor = session.descriptor
        assert recovery.state is not None
        verifier = await http_runtime(endpoint).acquire_device(expected_device_id=NATIVE_ID)
        resumed = await verifier.attach_session(descriptor)
        assert (await resumed.readiness()).ready
        await resumed.close()


async def test_http_recovery_late_close_fails_without_reopening_or_replaying(binary, tmp_path, http_runtime):
    from a13n_envd_client import EIPClientError
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState

    address = f"127.0.0.1:{free_port()}"
    endpoint = f"http://{address}"
    async with daemon(binary, tmp_path, "http", address) as (_, workspace):
        device = await http_runtime(endpoint).acquire_device(expected_device_id=NATIVE_ID)
        original = await device.open_session()
        recovery = EnvdSessionRecovery(
            EnvdSessionState(
                target=endpoint,
                descriptor=original.descriptor,
                configuration_digest=configuration_digest(SessionConfiguration()),
            )
        )
        restored = http_runtime(endpoint)
        with pytest.raises(EIPClientError):
            async with restored.open_session(
                expected_device_id=NATIVE_ID, required_methods=frozenset(), session_recovery=recovery
            ) as binding:
                session = binding.session
                assert session.session_id == original.session_id
                await original.close()
                async with session.open_writer(
                    EIPPath(path=str(workspace / "must-not-exist")), mode="create"
                ) as writer:
                    await writer.write(b"not replayed")
        assert not (workspace / "must-not-exist").exists()
        assert recovery.state is not None


async def test_http_recovery_after_daemon_restart_opens_new_generation(binary, tmp_path, http_runtime):
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState

    address = f"127.0.0.1:{free_port()}"
    endpoint = f"http://{address}"
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    async with daemon(binary, first_root, "http", address):
        device = await http_runtime(endpoint).acquire_device(expected_device_id=NATIVE_ID)
        original = await device.open_session()
        saved = EnvdSessionState(
            target=endpoint,
            descriptor=original.descriptor,
            configuration_digest=configuration_digest(SessionConfiguration()),
        )
    async with daemon(binary, second_root, "http", address):
        recovery = EnvdSessionRecovery(saved)
        restored = http_runtime(endpoint)
        async with restored.open_session(
            expected_device_id=NATIVE_ID, required_methods=frozenset(), session_recovery=recovery
        ) as binding:
            session = binding.session
            assert session.descriptor.generation != saved.descriptor.generation
            assert session.session_id != saved.descriptor.session_id
            assert (await session.readiness()).ready


@pytest.mark.parametrize("failure", ["transport", "protocol"])
async def test_http_recovery_does_not_open_fresh_session_after_uncertain_attach(
    binary, tmp_path, http_runtime, monkeypatch, failure
):
    from unittest.mock import AsyncMock

    from a13n_envd_client import EIPProtocolError, EIPTransportError
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState

    address = f"127.0.0.1:{free_port()}"
    endpoint = f"http://{address}"
    async with daemon(binary, tmp_path, "http", address):
        device = await http_runtime(endpoint).acquire_device(expected_device_id=NATIVE_ID)
        original = await device.open_session()
        recovery = EnvdSessionRecovery(
            EnvdSessionState(
                target=endpoint,
                descriptor=original.descriptor,
                configuration_digest=configuration_digest(SessionConfiguration()),
            )
        )
        restored = http_runtime(endpoint)
        next_device = await restored.acquire_device(expected_device_id=NATIVE_ID)
        error = (EIPTransportError if failure == "transport" else EIPProtocolError)("attachment failed")
        opened = AsyncMock(wraps=next_device.open_session)
        monkeypatch.setattr(next_device, "attach_session", AsyncMock(side_effect=error))
        monkeypatch.setattr(next_device, "open_session", opened)
        with pytest.raises(type(error), match="attachment failed"):
            async with restored.open_session(
                expected_device_id=NATIVE_ID, required_methods=frozenset(), session_recovery=recovery
            ):
                pytest.fail("An uncertain attach must fail")
        opened.assert_not_awaited()
        assert recovery.state is not None
        assert (await original.readiness()).ready


async def test_http_recovery_uses_current_credentials(binary, tmp_path, http_runtime):
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState

    address = f"127.0.0.1:{free_port()}"
    endpoint = f"http://{address}"
    async with daemon(binary, tmp_path, "http", address):
        device = await http_runtime(endpoint).acquire_device(expected_device_id=NATIVE_ID)
        original = await device.open_session()
        recovery = EnvdSessionRecovery(
            EnvdSessionState(
                target=endpoint,
                descriptor=original.descriptor,
                configuration_digest=configuration_digest(SessionConfiguration()),
            )
        )
        restored = http_runtime(endpoint, token="invalid-current-token")
        with pytest.raises(EnvironmentProviderError):
            async with restored.open_session(
                expected_device_id=NATIVE_ID, required_methods=frozenset(), session_recovery=recovery
            ):
                pytest.fail("Checkpoint state must not bypass authentication")
        assert (await original.readiness()).ready


@pytest.mark.parametrize("closed", [False, True])
async def test_reverse_websocket_recovers_session_after_carrier_reconnect(binary, tmp_path, closed):
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState

    async with WebSocketEnvdConnections() as hub:
        sockets = asyncio.Queue()

        async def handler(connection):
            await sockets.put(connection)
            await hub.attach(NATIVE_ID, connection)

        async with serve(handler, "127.0.0.1", 0, subprotocols=["eip.v1"], compression=None) as server:
            port = server.sockets[0].getsockname()[1]
            async with daemon(binary, tmp_path, "reverse_websocket", f"ws://127.0.0.1:{port}/envd"):
                socket = await asyncio.wait_for(sockets.get(), 5)
                original_device = await hub.acquire_device(expected_device_id=NATIVE_ID)
                original = await original_device.open_session()
                saved = EnvdSessionState(
                    target=NATIVE_ID,
                    descriptor=original.descriptor,
                    configuration_digest=configuration_digest(SessionConfiguration()),
                )
                if closed:
                    await original.close()
                await socket.close()
                await asyncio.wait_for(sockets.get(), 10)
                async with asyncio.timeout(10):
                    while True:
                        device = await hub.acquire_device(expected_device_id=NATIVE_ID)
                        if device is not original_device:
                            break
                        await asyncio.sleep(0.01)
                recovery = EnvdSessionRecovery(EnvdSessionState.model_validate_json(saved.model_dump_json()))
                execution = await WEBSOCKET_ENVD.execution_connector(
                    {},
                    state=state(WEBSOCKET_ENVD),
                    runtime=WebSocketEnvdProviderRuntime(hub),
                    session_recovery=recovery,
                ).open()
                try:
                    assert recovery.state is not None
                    descriptor = recovery.state.descriptor
                    assert descriptor.generation == saved.descriptor.generation
                    assert (descriptor.session_id == original.session_id) is not closed
                    assert execution.operations.files is not None
                    await execution.operations.files.write_text(str(tmp_path / "once"), "once", mode="create")
                finally:
                    await execution.close()
                assert recovery.state is not None
                assert (tmp_path / "once").read_text() == "once"
                resumed = await device.attach_session(descriptor)
                assert (await resumed.readiness()).ready
                await resumed.close()


@pytest.mark.parametrize("changed", ["target", "device", "generation", "directory", "methods", "boundary"])
async def test_session_recovery_checks_current_binding_before_attach(
    binary, tmp_path, http_runtime, monkeypatch, changed
):
    from unittest.mock import AsyncMock

    from a13n_envd_client import EIPSessionStateError
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState

    address = f"127.0.0.1:{free_port()}"
    endpoint = f"http://{address}"
    async with daemon(binary, tmp_path, "http", address):
        original = await (await http_runtime(endpoint).acquire_device(expected_device_id=NATIVE_ID)).open_session()
        saved = EnvdSessionState(
            target=endpoint,
            descriptor=original.descriptor,
            configuration_digest=configuration_digest(SessionConfiguration()),
        )
        if changed == "target":
            saved = saved.model_copy(update={"target": "another-target"})
        elif changed == "directory":
            saved = saved.model_copy(
                update={
                    "configuration_digest": configuration_digest(
                        SessionConfiguration(working_directory="/another-directory")
                    )
                }
            )
        else:
            changes = {
                "device": {"device_id": "another-device"},
                "generation": {"generation": "another-generation"},
                "methods": {
                    "available_methods": tuple(
                        method for method in original.descriptor.available_methods if method != "file.read_text"
                    )
                },
                "boundary": {"boundary": original.descriptor.boundary.model_copy(update={"policy_digest": "0" * 64})},
            }
            saved = saved.model_copy(update={"descriptor": original.descriptor.model_copy(update=changes[changed])})
        recovery = EnvdSessionRecovery(saved)
        runtime = http_runtime(endpoint)
        device = await runtime.acquire_device(expected_device_id=NATIVE_ID)
        attach = AsyncMock(wraps=device.attach_session)
        opened = AsyncMock(wraps=device.open_session)
        monkeypatch.setattr(device, "attach_session", attach)
        monkeypatch.setattr(device, "open_session", opened)
        scope = runtime.open_session(
            expected_device_id=NATIVE_ID, required_methods=frozenset({"file.read_text"}), session_recovery=recovery
        )
        if changed == "directory":
            with pytest.raises(EIPSessionStateError, match="retire the current binding first"):
                async with scope:
                    pytest.fail("Configuration changes require explicit retirement")
            opened.assert_not_awaited()
            attach.assert_not_awaited()
        elif changed in {"boundary", "methods"}:
            from a13n_envd_client import EIPProtocolError

            await original.detach()
            message = "boundary changed" if changed == "boundary" else "available methods widened"
            with pytest.raises(EIPProtocolError, match=message):
                async with scope:
                    pytest.fail("Changed Session facts must fail exact attachment")
            opened.assert_not_awaited()
            attach.assert_awaited_once()
        else:
            async with scope as binding:
                session = binding.session
                assert session.session_id != original.session_id
            opened.assert_awaited_once()
            attach.assert_not_awaited()
        assert recovery.state is not None
        if changed not in {"boundary", "methods"}:
            assert (await original.readiness()).ready


async def test_session_recovery_busy_takeover_never_opens_a_duplicate(binary, tmp_path, http_runtime, monkeypatch):
    from unittest.mock import AsyncMock

    from a13n_envd_client import EIPMethodError
    from a13n_envd_client.eip.v1 import EIPError, EIPErrorData
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState

    address = f"127.0.0.1:{free_port()}"
    endpoint = f"http://{address}"
    async with daemon(binary, tmp_path, "http", address):
        original = await (await http_runtime(endpoint).acquire_device(expected_device_id=NATIVE_ID)).open_session()
        recovery = EnvdSessionRecovery(
            EnvdSessionState(
                target=endpoint,
                descriptor=original.descriptor,
                configuration_digest=configuration_digest(SessionConfiguration()),
            )
        )
        runtime = http_runtime(endpoint)
        device = await runtime.acquire_device(expected_device_id=NATIVE_ID)
        error = EIPMethodError(
            EIPError(
                code=-32030,
                message="Session has in-flight operations",
                data=EIPErrorData(error_type="busy", retry_hint="never", dispatch_stage="pre_dispatch"),
            )
        )
        monkeypatch.setattr(device, "attach_session", AsyncMock(side_effect=error))
        opened = AsyncMock(wraps=device.open_session)
        monkeypatch.setattr(device, "open_session", opened)
        with pytest.raises(EIPMethodError):
            async with runtime.open_session(
                expected_device_id=NATIVE_ID, required_methods=frozenset(), session_recovery=recovery
            ):
                pytest.fail("Busy takeover must not allocate a Session")
        opened.assert_not_awaited()
        assert (await original.readiness()).ready
        assert recovery.state is not None
        assert recovery.state.descriptor.owner_epoch == original.descriptor.owner_epoch + 1


@pytest.mark.parametrize("provider", ["docker", "e2b", "daytona", "modal", "vercel"])
async def test_managed_defaults_reach_real_envd_commands_and_children(
    binary, tmp_path, http_runtime, monkeypatch, provider
):
    """Use each managed adapter, replacing only cloud attachment with a real local daemon."""
    import importlib
    from types import SimpleNamespace

    from a13n_harness.environment.commands import CommandEnvironment

    module = importlib.import_module(f"a13n_harness.providers.environment.{provider}.http")
    port = free_port()
    async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}") as (_, workspace):
        runtime = http_runtime(f"http://127.0.0.1:{port}")

        def session(reference, *, session_recovery=None):
            return runtime.open_session(
                expected_device_id=NATIVE_ID,
                required_methods=frozenset({"shell.exec", "process.start"}),
            )

        monkeypatch.setattr(module, "http_session", session)
        defaults = {"APP_MODE": "default ' $HOME 中文"}
        config = SimpleNamespace(environment=defaults)
        reference = SimpleNamespace(
            config=config,
            _configuration=config,
            environment_id=LOGICAL_ID,
            state=EnvironmentState(provider_key=provider, state_version="1", state={}),
            require_target=lambda: None,
        )
        script = (
            "import os,subprocess,sys,json; "
            "child=subprocess.check_output([sys.executable,'-c',\"import os; print(os.getenv('APP_MODE', '<absent>'))\"],text=True); "
            "print(json.dumps([os.getenv('APP_MODE','<absent>'),child.strip()]))"
        )
        for attempt in range(2):
            execution = module.http_execution(reference)
            await execution.open(execution_id=f"attempt-{attempt}")
            try:
                shell = execution.operations.shell
                processes = execution.operations.processes
                assert shell is not None and processes is not None
                for overrides, expected in [
                    (CommandEnvironment(), defaults["APP_MODE"]),
                    (CommandEnvironment(set={"APP_MODE": "override"}), "override"),
                    (CommandEnvironment(unset=("APP_MODE",)), "<absent>"),
                ]:
                    request = CommandRequest(
                        command=ArgvCommand(executable=Path(sys.executable).resolve().name, arguments=("-c", script)),
                        cwd=str(workspace),
                        environment=overrides,
                        output_policy=EnvironmentOutputPolicy(
                            max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"
                        ),
                    )
                    result = await shell.exec(request)
                    assert result.status.exit_code == 0
                    assert json.loads(result.output.stdout.inline) == [expected, expected]
                    started = await processes.start(request)
                    info = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=5)
                    assert info.status.exit_code == 0
                    assert json.loads(info.output.stdout.inline) == [expected, expected]
                    await processes.release(started.process.handle)
            finally:
                await execution.close()
        assert defaults == {"APP_MODE": "default ' $HOME 中文"}


async def test_session_context_shares_live_borrowers_and_restores_after_final_release(binary, tmp_path, http_runtime):
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery

    endpoint = f"http://127.0.0.1:{free_port()}"
    checkpoints = []

    async def checkpoint(state):
        checkpoints.append(state)

    context = EnvdSessionRecovery(checkpoint=checkpoint)
    async with daemon(binary, tmp_path, "http", endpoint.removeprefix("http://")):
        runtime = http_runtime(endpoint)

        def borrow(recovery=context):
            return runtime.open_session(
                expected_device_id=NATIVE_ID,
                required_methods=frozenset(),
                session_recovery=recovery,
            )

        first_scope, second_scope = borrow(), borrow()
        first = await first_scope.__aenter__()
        second = await second_scope.__aenter__()
        assert first.session is second.session
        descriptor = first.session.descriptor
        assert checkpoints[-1].descriptor == descriptor
        await first_scope.__aexit__(None, None, None)
        assert (await second.session.readiness()).ready
        await second_scope.__aexit__(None, None, None)
        assert context.state is not None
        async with borrow() as successor:
            assert successor.session.session_id == descriptor.session_id
            assert successor.session.descriptor.owner_epoch > descriptor.owner_epoch
            async with borrow(EnvdSessionRecovery()) as unrelated:
                assert unrelated.session.session_id != descriptor.session_id
        assert len(checkpoints) == 3  # Open, epoch reservation, attached descriptor.


@pytest.mark.parametrize("failure", ["open", "checkpoint", "cancel_checkpoint", "attach"])
async def test_session_recovery_unwinds_failed_acquisition(binary, tmp_path, http_runtime, monkeypatch, failure):
    from a13n_envd_client import EIPSession, EIPTransportError
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery
    from anyio import create_task_group, sleep_forever

    endpoint = f"http://127.0.0.1:{free_port()}"
    events = []
    durable = None
    failing = False
    checkpoint_entered = asyncio.Event()

    async def checkpoint(state):
        nonlocal durable
        if failing and failure in {"checkpoint", "cancel_checkpoint"}:
            if failure == "cancel_checkpoint":
                checkpoint_entered.set()
                await sleep_forever()
            raise OSError("checkpoint failed")
        durable = state

    detach = EIPSession.detach

    async def tracked_detach(self):
        events.append("detach")
        await detach(self)

    monkeypatch.setattr(EIPSession, "detach", tracked_detach)
    context = EnvdSessionRecovery(checkpoint=checkpoint)
    async with daemon(binary, tmp_path, "http", endpoint.removeprefix("http://")):
        runtime = http_runtime(endpoint)
        device = await runtime.acquire_device(expected_device_id=NATIVE_ID)

        async def acquire():
            context.state = durable
            async with runtime.open_session(
                expected_device_id=NATIVE_ID, required_methods=frozenset(), session_recovery=context
            ):
                assert not failing

        if failure == "attach":
            await acquire()
            events.clear()
        method = "attach_session" if failure == "attach" else "open_session"
        original = getattr(device, method)

        async def fail(*args, **kwargs):
            raise EIPTransportError("acquisition failed")

        failing = True
        if failure in {"open", "attach"}:
            monkeypatch.setattr(device, method, fail)
        if failure == "cancel_checkpoint":
            async with create_task_group() as group:
                group.start_soon(acquire)
                async with asyncio.timeout(5):
                    await checkpoint_entered.wait()
                group.cancel_scope.cancel()
        else:
            with pytest.raises((EIPTransportError, OSError)):
                await acquire()
        expected = ["detach"] if failure in {"checkpoint", "cancel_checkpoint"} else []
        assert events == expected
        assert context._scope is None and context._session is None and context._users == 0
        failing = False
        monkeypatch.setattr(device, method, original)
        await acquire()
        assert events[len(expected) :] == ["detach"]


async def test_dead_session_late_borrower_does_not_detach_successor(binary, tmp_path, http_runtime, monkeypatch):
    from a13n_envd_client import EIPSession
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery

    endpoint = f"http://127.0.0.1:{free_port()}"
    detached = []
    detach = EIPSession.detach

    async def tracked_detach(self):
        detached.append(self.descriptor.owner_epoch)
        await detach(self)

    monkeypatch.setattr(EIPSession, "detach", tracked_detach)
    context = EnvdSessionRecovery()
    async with daemon(binary, tmp_path, "http", endpoint.removeprefix("http://")):
        runtime = http_runtime(endpoint)

        def borrow():
            return runtime.open_session(
                expected_device_id=NATIVE_ID, required_methods=frozenset(), session_recovery=context
            )

        old_scope = borrow()
        old = await old_scope.__aenter__()
        old.session._finish()
        async with borrow() as successor:
            assert successor.session.session_id == old.session.session_id
            assert successor.session.descriptor.owner_epoch > old.session.descriptor.owner_epoch
            assert detached == [old.session.descriptor.owner_epoch]
            await old_scope.__aexit__(None, None, None)
            assert detached == [old.session.descriptor.owner_epoch]
            assert context._users == 1
            assert (await successor.session.readiness()).ready
        assert detached == [old.session.descriptor.owner_epoch, successor.session.descriptor.owner_epoch]
        assert context._users == 0


async def test_uncertain_takeover_keeps_checkpoint_and_advances_epoch_on_retry(
    binary, tmp_path, http_runtime, monkeypatch
):
    from a13n_envd_client import EIPTransportError
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery

    endpoint = f"http://127.0.0.1:{free_port()}"
    context = EnvdSessionRecovery()
    async with daemon(binary, tmp_path, "http", endpoint.removeprefix("http://")):
        runtime = http_runtime(endpoint)

        def borrow():
            return runtime.open_session(
                expected_device_id=NATIVE_ID,
                required_methods=frozenset(),
                session_recovery=context,
            )

        async with borrow() as first:
            original_id = first.session.session_id
        device = await runtime.acquire_device(expected_device_id=NATIVE_ID)
        attach = device.attach_session

        async def lose_reply(descriptor, **kwargs):
            session = await attach(descriptor, **kwargs)
            session._finish()  # Lose the local wrapper after native takeover succeeded.
            raise EIPTransportError("lost takeover response")

        monkeypatch.setattr(device, "attach_session", lose_reply)
        with pytest.raises(EIPTransportError, match="lost takeover"):
            async with borrow():
                pytest.fail("Lost response must not expose a Session")
        assert context.state is not None
        reserved = context.state.descriptor.owner_epoch
        monkeypatch.setattr(device, "attach_session", attach)
        async with borrow() as recovered:
            assert recovered.session.session_id == original_id
            assert recovered.session.descriptor.owner_epoch == reserved + 1


@pytest.mark.parametrize(
    "failure",
    [
        "handoff",
        "lost_identity",
        "lost_reply",
        "checkpoint",
        "observation",
        "cancelled_start",
        "publication_cancel",
        "disclosure",
        "disclosure_handoff",
        "large_output_handoff",
    ],
)
async def test_owned_shell_handoff_recovers_same_process_without_start_replay(
    binary, tmp_path, http_runtime, monkeypatch, failure
):
    from dataclasses import replace

    from a13n_harness import AgentIdentityRef, AgentInstanceContext
    from a13n_harness.environment.coordinator import create_environment_runtime
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState
    from a13n_harness.tool_execution import MemoryToolExecutionStore, ToolExecutionRuntime
    from a13n_harness.toolsets.process_manager import _ProcessController

    from ..environment_helpers import Source

    endpoint = f"http://127.0.0.1:{free_port()}"
    instance = AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="agent"), agent_instance_id="agent-1"
    )
    store = MemoryToolExecutionStore()
    disclosure = failure in {"disclosure", "disclosure_handoff", "large_output_handoff"}
    payload = (
        "Bearer test-sensitive-value\n" + "x" * (80000 if failure == "large_output_handoff" else 40000) + "\nfinished"
    )

    async def assert_disclosed(result):
        from types import SimpleNamespace

        from a13n_harness.capabilities.tool_execution import _result
        from a13n_harness.tools._output import DEFAULT_TOOL_OUTPUT_CHARS
        from a13n_harness.toolsets.output import tool_output_size

        assert result.record.status == "succeeded", result
        assert "test-sensitive-value" not in json.dumps(result.result)
        assert tool_output_size(result.result) <= DEFAULT_TOOL_OUTPUT_CHARS
        assert result.result["disclosure"]["content_complete"]
        retained = await store.get("thread-a", record.execution_id)
        assert retained.artifacts == result.artifacts
        assert len(retained.artifacts) == 2
        for artifact in retained.artifacts:
            assert b"test-sensitive-value" not in artifact.data
            assert b"Bearer [REDACTED]" in artifact.data
            assert b"x" * 40000 in artifact.data

        async def spill(data, *, suffix):
            path = tmp_path / ("materialized" + suffix)
            path.write_bytes(data)
            return str(path)

        deps = SimpleNamespace(
            _spill_tool_result=spill,
            _tool_executions=SimpleNamespace(delivered_result=lambda call, record, value: value),
        )
        delivered = await _result(SimpleNamespace(deps=deps, tool_call_id="retrieve"), retained)
        materialized = delivered.return_value
        assert "tool-execution-artifact://" not in json.dumps(materialized)
        assert Path(materialized["disclosure"]["text_file_paths"]["/stdout/text"]).read_text() == (
            "ready\n" + payload.replace("test-sensitive-value", "[REDACTED]") + "\n"
        )

    original_process_id = None
    start_returned, lose_start_reply = asyncio.Event(), asyncio.Event()
    publication_started, fail_publication = asyncio.Event(), asyncio.Event()
    if failure in {"lost_reply", "cancelled_start"}:
        from a13n_envd_client.eip.v1 import EIPClient
        from a13n_envd_client.errors import EIPTransportError

        original_start = EIPClient.process_start

        async def lose_reply(client, params):
            nonlocal original_process_id
            result = await original_start(client, params)
            original_process_id = result.process.handle.root
            if failure == "cancelled_start":
                start_returned.set()
                await lose_start_reply.wait()
            raise EIPTransportError("injected lost start response")

        monkeypatch.setattr(EIPClient, "process_start", lose_reply)
    elif failure == "checkpoint":
        original_save = store.save

        async def fail_identity_checkpoint(execution):
            if execution.shell is not None and execution.shell.process.identity is not None:
                monkeypatch.setattr(store, "save", original_save)
                raise OSError("injected checkpoint failure")
            await original_save(execution)

        monkeypatch.setattr(store, "save", fail_identity_checkpoint)
    elif failure == "publication_cancel":
        original_save = store.save

        async def fail_result_publication(execution):
            if execution.record.result_available:
                publication_started.set()
                await fail_publication.wait()
                monkeypatch.setattr(store, "save", original_save)
                raise OSError("injected result publication failure")
            await original_save(execution)

        monkeypatch.setattr(store, "save", fail_result_publication)
    completions = []
    first = ToolExecutionRuntime(store=store, on_completed=completions.append)
    context = EnvdSessionRecovery()
    async with daemon(binary, tmp_path, "http", endpoint.removeprefix("http://")) as (_, workspace):

        def environment(provider_runtime, recovery):
            connection = HTTP_ENVD.execution_connector(
                RemoteEnvdEnvironmentConfiguration(working_directory=str(workspace)),
                configuration=provider_runtime.configuration,
                environment_id=LOGICAL_ID,
                state=state(HTTP_ENVD),
                runtime=provider_runtime,
                session_recovery=recovery,
            )
            return create_environment_runtime(mounts={"workspace": Source(connection)}, default_mount="workspace")

        owner_connection = http_runtime(endpoint)
        scope = environment(owner_connection, context)
        async with scope.bind(thread_id="thread-a", run_id="run-a", instance=instance, host_refs={}) as bound:
            await scope._activate()
            controller = _ProcessController(bound)
            if failure == "observation":

                async def lose_observation(*args, **kwargs):
                    raise EnvironmentError("injected disconnect", code="environment_unavailable")

                monkeypatch.setattr(controller, "_observe_owned", lose_observation)
            mount_id = bound.resolve_path(".").mount_id
            request = CommandRequest(
                command=ArgvCommand(
                    executable=Path(sys.executable).resolve().name,
                    arguments=(
                        "-u",
                        "-c",
                        "import sys; open('admissions','a').write('1'); print('ready'); "
                        + (
                            "print('finished')"
                            if failure == "publication_cancel"
                            else f"sys.stdin.readline(); print({payload!r})"
                            if disclosure
                            else "print(sys.stdin.readline().strip())"
                        ),
                    ),
                ),
                keep_stdin_open=True,
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=4096, max_output_bytes=131072 if disclosure else 4096, overflow="truncate"
                ),
            )

            async def start():
                return await controller.run_owned(
                    request,
                    alias="workspace",
                    expected_mount_id=mount_id,
                    runtime=first,
                    execution_id=record.execution_id,
                )

            record = first.admit(
                start, thread_id="thread-a", run_id="run-a", tool_call_id="call-a", tool_name="shell_exec"
            )
            original_identity = None
            if failure == "cancelled_start":
                await start_returned.wait()
                await first.cancel("thread-a", record.execution_id)
                lose_start_reply.set()
            elif failure == "publication_cancel":
                await publication_started.wait()
                await first.cancel("thread-a", record.execution_id)
                fail_publication.set()
            if failure in {"handoff", "lost_identity", "disclosure", "disclosure_handoff", "large_output_handoff"}:
                async with asyncio.timeout(5):
                    while first.resource("thread-a", record.execution_id) is None:
                        assert not (await first.info("thread-a", record.execution_id)).terminal
                        await asyncio.sleep(0.01)
                original_identity = first.resource("thread-a", record.execution_id).identity
                if failure == "disclosure":
                    await first.call_resource(
                        "thread-a", record.execution_id, {"operation": "input", "data": "finish\n"}
                    )
                    await assert_disclosed(await first.wait("thread-a", record.execution_id, timeout_seconds=5))
                    assert (workspace / "admissions").read_text() == "1"
                    assert [item.execution_id for item in completions] == [record.execution_id]
                    await controller.close()
                    assert await first.close() == ()
                    return
                # Release after the identity checkpoint, with no later native
                # checkpoint to accidentally make the receipt durable.
                await first.release_foreground("thread-a", record.execution_id)
                first.stop_admission()
                first.yield_recoverable()
            else:
                await first.release_foreground("thread-a", record.execution_id)
            async with asyncio.timeout(5):
                await first.join()
            await controller.close()
        await owner_connection.close()
        saved = await store.get("thread-a", record.execution_id)
        assert saved is not None and not saved.record.terminal and saved.shell is not None
        assert saved.record.foreground_released
        assert saved.record.cancel_requested == (failure in {"cancelled_start", "publication_cancel"})
        assert completions == []
        if failure == "lost_identity":
            saved = replace(
                saved,
                shell=saved.shell.model_copy(
                    update={"process": saved.shell.process.model_copy(update={"identity": None})}
                ),
            )
        assert context.state is not None
        restored = EnvdSessionRecovery(EnvdSessionState.model_validate_json(context.state.model_dump_json()))
        successor = ToolExecutionRuntime(store=store, on_completed=completions.append)
        scope = environment(http_runtime(endpoint), restored)
        async with scope.bind(thread_id="thread-a", run_id="recovery", instance=instance, host_refs={}) as bound:
            await scope._activate()

            async def recover():
                from a13n_harness.toolsets.process_manager import recover_shell

                return await recover_shell(bound, saved.shell, runtime=successor, execution_id=record.execution_id)

            if failure == "publication_cancel":
                # Failed publication must leave the only native output intact,
                # even though cancellation arrived during the store write.
                native = await bound.processes.recover(saved.shell.process, alias="workspace")
                assert native.output.stdout.inline == b"ready\nfinished\n"
                bound.processes.detach(native.handle)
            successor.resume_shell(saved, recover)
            if saved.record.cancel_requested:
                result = await successor.wait("thread-a", record.execution_id, timeout_seconds=5)
                assert result.record.status == "cancelled", result
                assert (workspace / "admissions").read_text() == "1"
                assert [item.execution_id for item in completions] == [record.execution_id]
                return
            async with asyncio.timeout(5):
                while successor.resource("thread-a", record.execution_id) is None:
                    assert not (await successor.info("thread-a", record.execution_id)).terminal
                    await asyncio.sleep(0.01)
            recovered_identity = successor.resource("thread-a", record.execution_id).identity
            if original_identity is not None:
                assert recovered_identity == original_identity
            if original_process_id is not None:
                assert recovered_identity.process_id == str(original_process_id)
            await successor.call_resource("thread-a", record.execution_id, {"operation": "input", "data": "finished\n"})
            result = await successor.wait("thread-a", record.execution_id, timeout_seconds=5)
            assert result.record.status == "succeeded", result
            if failure == "large_output_handoff":
                from a13n_harness.tools._output import DEFAULT_TOOL_OUTPUT_CHARS
                from a13n_harness.toolsets.output import tool_output_size

                stdout = result.result["stdout"]
                assert stdout["available_end"] == len("ready\n" + payload + "\n")
                assert stdout["producer_complete"]
                assert not stdout["content_complete"]
                assert stdout["offload"]["reason"] == "write_unavailable"
                assert stdout["offload"]["path"] is None
                assert stdout["offload"]["bytes"] == 0
                assert stdout["text"].startswith("ready\nBearer [REDACTED]\n")
                assert "finished" not in stdout["text"]
                assert tool_output_size(result.result) <= DEFAULT_TOOL_OUTPUT_CHARS
                retained = await store.get("thread-a", record.execution_id)
                assert retained.result == result.result
                assert retained.artifacts == result.artifacts == ()
            elif disclosure:
                await assert_disclosed(result)
            else:
                assert result.result["stdout"]["text"] == "ready\nfinished\n"
        assert (workspace / "admissions").read_text() == "1"
        assert [item.execution_id for item in completions] == [record.execution_id]
        assert await first.close() == ()
        assert await successor.close() == ()


async def test_dead_session_borrower_cannot_pin_or_detach_replacement(binary, tmp_path, http_runtime):
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery

    endpoint = f"http://127.0.0.1:{free_port()}"
    recovery = EnvdSessionRecovery()
    async with daemon(binary, tmp_path, "http", endpoint.removeprefix("http://")):
        old_runtime, new_runtime = http_runtime(endpoint), http_runtime(endpoint)

        def borrow(runtime):
            return runtime.open_session(
                expected_device_id=NATIVE_ID, required_methods=frozenset(), session_recovery=recovery
            )

        old_scope = borrow(old_runtime)
        old = await old_scope.__aenter__()
        original = old.session.descriptor
        # A heartbeat failure closes the client without destroying the native Session.
        old.session._finish()
        await old_runtime.close()
        assert old.session.is_closed
        new_scope, shared_scope = borrow(new_runtime), borrow(new_runtime)
        new, shared = await asyncio.gather(new_scope.__aenter__(), shared_scope.__aenter__())
        try:
            assert new.session is shared.session
            assert new.session.session_id == original.session_id
            assert new.session.descriptor.owner_epoch == original.owner_epoch + 1
            await old_scope.__aexit__(None, None, None)
            assert (await new.session.readiness()).ready
            await shared_scope.__aexit__(None, None, None)
            assert (await new.session.readiness()).ready
        finally:
            await new_scope.__aexit__(None, None, None)
        assert new.session.is_closed
        async with borrow(new_runtime) as final:
            assert final.session.session_id == original.session_id
            assert final.session.descriptor.owner_epoch == original.owner_epoch + 2
