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
from a13n_envd_client import EIPSession, HttpTransport
from a13n_environment import (
    ArgvCommand,
    CommandRequest,
    EnvironmentOutputPolicy,
    EnvironmentProviderError,
    EnvironmentState,
    HttpEnvdBackendConfiguration,
    HttpEnvdCredential,
    HttpEnvdEnvironmentProvider,
    HttpEnvdProviderRuntime,
    RemoteEnvdProviderConfiguration,
    WebSocketEnvdConnections,
    WebSocketEnvdEnvironmentProvider,
    WebSocketEnvdProviderRuntime,
)
from a13n_environment.commands import PortTarget
from a13n_environment.docker.provider import _open_docker_eip_session
from a13n_environment.files import FileQueryRequest, FileTextSearchRequest
from a13n_environment.models import EnvironmentError
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
    return EnvironmentState(provider_key=provider.key, state_version="1", state={"daemon_environment_id": native})


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
                "root_mount_id": "workspace",
                "mounts": [
                    {
                        "mount_id": "workspace",
                        "native_root": str(workspace),
                        "writable": True,
                        "allow_command_execution": True,
                        "max_file_bytes": 1024 * 1024,
                        "allowed_operations": [
                            "stat",
                            "read_text",
                            "write_text",
                            "open_reader",
                            "open_writer",
                            "list",
                            "find",
                            "search",
                            "command_cwd",
                        ],
                    }
                ],
                "trusted_executable_roots": [str(Path(sys.executable).resolve().parent)],
            }
        )
    )
    env = {
        "A13N_ENVD_ENVIRONMENT_ID": NATIVE_ID,
        "A13N_ENVD_RUNTIME_DIR": str(runtime),
        "A13N_ENVD_TRANSPORT": transport,
        "A13N_ENVD_EXECUTION_ISOLATION": "disabled",
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


def http_runtime(endpoint, token=TOKEN):
    return HttpEnvdProviderRuntime(
        HttpEnvdBackendConfiguration(endpoint=endpoint, request_timeout=5),
        HttpEnvdCredential(token=SecretStr(token)),
    )


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


async def test_docker_session_recovers_when_published_port_precedes_real_envd(binary, tmp_path):
    disconnected = asyncio.Event()

    async def early_connection(reader, writer):
        try:
            await reader.read(65536)
        finally:
            writer.close()
            await writer.wait_closed()
            disconnected.set()

    # Model Docker's published route accepting a connection before envd starts.
    listener = await asyncio.start_server(early_connection, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]

    async def connect():
        async with _open_docker_eip_session(
            f"http://127.0.0.1:{port}", TOKEN, expected_environment_id=NATIVE_ID
        ) as session:
            assert session.descriptor.environment_id == NATIVE_ID
            assert (await session.readiness()).ready

    task = asyncio.create_task(connect())
    try:
        await asyncio.wait_for(disconnected.wait(), 5)
        listener.close()
        await listener.wait_closed()
        async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}"):
            await asyncio.wait_for(task, 5)
    finally:
        listener.close()
        await listener.wait_closed()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def exercise(environment):
    await environment.prepare()
    generation = environment.descriptor.generation
    await environment.enter(
        thread_id="thread-test", run_id="run-test", agent_instance_id="agent-test", mount_id="mount-test"
    )
    await environment.ensure_ready(frozenset({"files", "shell", "processes", "outputs"}))
    files = environment.operations.files
    assert files is not None
    await files.write_text("/text.txt", "remote envd\n", mode="create")
    assert (await files.read_text("/text.txt")).text == "remote envd\n"
    query = await files.query(FileQueryRequest(root="/", pattern="*.{txt,py}", max_results=10))
    assert [entry.path for entry in query.entries] == ["/text.txt"]
    search = await files.search_text(
        FileTextSearchRequest(root="/", pattern="REMOTE", case_sensitive=False, include="*.{txt,py}", max_matches=10)
    )
    assert [match.text for match in search.matches] == ["remote envd"]
    single = await files.search_text(
        FileTextSearchRequest(
            root="/text.txt", pattern="REMOTE", case_sensitive=False, include="/text.txt", max_matches=1
        )
    )
    assert [(match.path, match.text) for match in single.matches] == [("/text.txt", "remote envd")]
    assert not single.has_more
    with pytest.raises(EnvironmentError) as missing:
        await files.search_text(FileTextSearchRequest(root="/missing.txt", pattern="needle", max_matches=1))
    assert missing.value.code == "environment_not_found"
    assert missing.value.safe_projection()["message"]
    for arguments, field, reason in [
        ({"pattern": "(", "regex": True}, "pattern", "invalid_regex"),
        ({"pattern": "ok", "include": "{broken}"}, "include", "invalid_glob"),
        ({"pattern": ""}, "pattern", "empty_pattern"),
    ]:
        with pytest.raises(EnvironmentError) as error:
            await files.search_text(FileTextSearchRequest(root="/", max_matches=10, **arguments))
        assert error.value.code == "environment_request_invalid"
        assert error.value.details["field"] == field
        assert error.value.details["reason"] == reason
        assert error.value.details["hint"]
    payload = bytes(range(256)) * 100

    async def chunks():
        yield payload[:123]
        yield payload[123:]

    await files.write_bytes_stream("/binary.dat", chunks(), mode="create")
    assert await files.read_bytes("/binary.dat") == payload
    shell = environment.operations.shell
    assert shell is not None
    policy = EnvironmentOutputPolicy(max_inline_bytes=128, max_output_bytes=4096, overflow="retain")
    request = CommandRequest(
        command=ArgvCommand(executable=Path(sys.executable).resolve().name, arguments=("-c", "print('from-envd')")),
        cwd="/",
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


async def test_http_provider_operations_identity_and_sequential_reentry(binary, tmp_path):
    port = free_port()
    endpoint = f"http://127.0.0.1:{port}"
    provider = HttpEnvdEnvironmentProvider()
    async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}") as (process, workspace):
        env = provider.create_environment(
            configuration=RemoteEnvdProviderConfiguration(),
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
        again = provider.create_environment(
            configuration=RemoteEnvdProviderConfiguration(),
            environment_id=LOGICAL_ID,
            state=env.dump_state(),
            runtime=http_runtime(endpoint),
        )
        try:
            await again.prepare()
            assert again.descriptor.generation == generation
        finally:
            await again.close()


async def test_closed_eip_process_and_port_facets_raise_environment_errors(binary, tmp_path):
    port = free_port()
    provider = HttpEnvdEnvironmentProvider()
    async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}"):
        environment = provider.create_environment(
            configuration=RemoteEnvdProviderConfiguration(),
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
async def test_http_provider_rejects_untrusted_or_incompatible_peer(binary, tmp_path, failure):
    port = free_port()
    provider = HttpEnvdEnvironmentProvider()
    endpoint = f"http://127.0.0.1:{port}"
    async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}"):
        selected = state(provider, "wrong-env" if failure == "identity" else NATIVE_ID)
        env = provider.create_environment(
            configuration=RemoteEnvdProviderConfiguration(
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


async def test_http_active_or_abandoned_session_is_not_taken_over(binary, tmp_path):
    port = free_port()
    endpoint = f"http://127.0.0.1:{port}"
    provider = HttpEnvdEnvironmentProvider()
    async with daemon(binary, tmp_path, "http", f"127.0.0.1:{port}"):
        original = await EIPSession.initialize(HttpTransport(endpoint, TOKEN), expected_environment_id=NATIVE_ID)
        # No provider may assume transport loss proves that another session is gone.
        for abort in (False, True):
            if abort:
                await original.abort()
            env = provider.create_environment(
                configuration=RemoteEnvdProviderConfiguration(),
                environment_id=LOGICAL_ID,
                state=state(provider),
                runtime=http_runtime(endpoint),
            )
            with pytest.raises(EnvironmentProviderError):
                await env.prepare()
            assert env.dump_state() == state(provider)
            await env.close()


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
                provider = WebSocketEnvdEnvironmentProvider()
                runtime = WebSocketEnvdProviderRuntime(hub)
                env = provider.create_environment(
                    configuration=RemoteEnvdProviderConfiguration(),
                    environment_id=LOGICAL_ID,
                    state=state(provider),
                    runtime=runtime,
                )
                try:
                    generation = await exercise(env)
                finally:
                    await env.close()
                assert process.returncode is None
                again = provider.create_environment(
                    configuration=RemoteEnvdProviderConfiguration(),
                    environment_id=LOGICAL_ID,
                    state=env.dump_state(),
                    runtime=runtime,
                )
                try:
                    await again.prepare()
                    assert again.descriptor.generation == generation
                finally:
                    await again.close()
    assert not errors
