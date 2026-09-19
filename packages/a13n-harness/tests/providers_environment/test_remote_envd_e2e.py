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
from a13n_envd_client.eip.v1 import DirectoryListParams
from a13n_harness.providers.environment.commands import ArgvCommand, CommandRequest, PortTarget
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.files import FileQueryRequest, FileTextSearchRequest
from a13n_harness.providers.environment.models import EnvironmentError, EnvironmentState
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    RemoteEnvdEnvironmentConfiguration,
)
from a13n_harness.providers.environment.remote_envd.connections import WebSocketEnvdConnections
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD, HttpEnvdProviderRuntime
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD, WebSocketEnvdProviderRuntime
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy
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


async def exercise(environment):
    await environment.prepare()
    generation = environment.descriptor.generation
    root = environment.descriptor.working_directory
    assert root is not None
    await environment.enter(mount_id="mount-test")
    await environment.ensure_ready(frozenset({"files", "shell", "processes", "outputs"}))
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
    assert handle.mount_id == "mount-test"
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
            async with runtime.open_session(expected_device_id=NATIVE_ID, required_methods=frozenset()) as session:
                # A missing directory is a binding failure, not a lost Device.
                with pytest.raises(EIPClientError):
                    async with runtime.open_session(
                        expected_device_id=NATIVE_ID,
                        required_methods=frozenset(),
                        working_directory=str(second_root / "missing"),
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
        env = provider.construct(
            configuration=RemoteEnvdEnvironmentConfiguration(),
            environment_id=LOGICAL_ID,
            state=state(provider),
            runtime=http_runtime(endpoint),
        )
        try:
            generation = await exercise(env)
        finally:
            await env.close()
        assert process.returncode is None
        assert (workspace / "text.txt").read_text() == "remote envd\n"
        again = provider.construct(
            configuration=RemoteEnvdEnvironmentConfiguration(),
            environment_id=LOGICAL_ID,
            state=env.dump_state(),
            runtime=http_runtime(endpoint),
        )
        try:
            await again.prepare()
            assert again.descriptor.generation != generation
            assert again.descriptor.generation.split(":")[0] == generation.split(":")[0]
        finally:
            await again.close()


async def test_closed_eip_process_and_port_facets_raise_environment_errors(binary, tmp_path, http_runtime):
    port = free_port()
    provider = HTTP_ENVD
    async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}"):
        environment = provider.construct(
            configuration=RemoteEnvdEnvironmentConfiguration(),
            environment_id=LOGICAL_ID,
            state=state(provider),
            runtime=http_runtime(f"http://127.0.0.1:{port}"),
        )
        try:
            await environment.prepare()
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
        env = provider.construct(
            configuration=RemoteEnvdEnvironmentConfiguration(
                required_methods=("missing.method",) if failure == "methods" else ()
            ),
            environment_id=LOGICAL_ID,
            state=selected,
            runtime=http_runtime(endpoint, "wrong-token" if failure == "credential" else TOKEN),
        )
        with pytest.raises(EnvironmentProviderError) as error:
            await env.prepare()
        assert TOKEN not in str(error.value)
        assert env.operations.files is None and env.dump_state() == selected
        await env.close()


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
            provider.construct(
                configuration=RemoteEnvdEnvironmentConfiguration(working_directory=str(workspace)),
                environment_id=LOGICAL_ID,
                state=state(provider),
                runtime=runtime,
            )
            for _ in range(2)
        ]
        try:
            await asyncio.gather(first.prepare(), second.prepare())
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
                    provider.construct(
                        configuration=RemoteEnvdEnvironmentConfiguration(),
                        environment_id=f"env-scope-{index}",
                        state=state(provider),
                        runtime=WebSocketEnvdProviderRuntime(hub),
                    )
                    for index in range(2)
                ]
                first, second = environments
                try:
                    await asyncio.gather(*(environment.prepare() for environment in environments))
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
                    async with hub.open_session(expected_device_id=NATIVE_ID, required_methods=frozenset()) as session:
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
                env = provider.construct(
                    configuration=RemoteEnvdEnvironmentConfiguration(),
                    environment_id=LOGICAL_ID,
                    state=state(provider),
                    runtime=runtime,
                )
                try:
                    generation = await exercise(env)
                finally:
                    await env.close()
                assert process.returncode is None
                again = provider.construct(
                    configuration=RemoteEnvdEnvironmentConfiguration(),
                    environment_id=LOGICAL_ID,
                    state=env.dump_state(),
                    runtime=runtime,
                )
                try:
                    await again.prepare()
                    assert again.descriptor.generation != generation
                    assert again.descriptor.generation.split(":")[0] == generation.split(":")[0]
                finally:
                    await again.close()
    assert not errors
