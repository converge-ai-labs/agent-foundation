from __future__ import annotations

import asyncio
import json
import os
import ssl
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from a13n_envd_client import AcceptedWebSocketTransport, EIPMethodError, EIPSession
from a13n_envd_client.eip.v1 import EIPPath, FileWriteMode
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

_TOKEN = "test-attachment-token-without-secret-meaning"
_ENVIRONMENT_ID = "env-websocket-e2e"
_TLS_FIXTURES = Path(__file__).parent / "fixtures" / "tls"


def a13n_envd_binary() -> Path:
    configured = os.environ.get("A13N_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("set A13N_ENVD_TEST_BINARY to run Rust daemon E2E tests")
    binary = Path(configured)
    assert binary.is_file(), f"a13n-envd test binary does not exist: {binary}"
    return binary


async def start_daemon(
    binary: Path,
    *,
    endpoint: str,
    credential_file: Path,
    config_file: Path,
    runtime_dir: Path,
    ca_file: Path | None = None,
) -> asyncio.subprocess.Process:
    environment = {
        "A13N_ENVD_ENVIRONMENT_ID": _ENVIRONMENT_ID,
        "A13N_ENVD_TRANSPORT": "reverse_websocket",
        "A13N_ENVD_REVERSE_WS_URL": endpoint,
        "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE": str(credential_file),
        "A13N_ENVD_RUNTIME_DIR": str(runtime_dir),
        "A13N_ENVD_EXECUTION_ISOLATION": "disabled",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    if ca_file is not None:
        environment["A13N_ENVD_REVERSE_WS_CA_FILE"] = str(ca_file)
    return await asyncio.create_subprocess_exec(
        str(binary),
        "--config",
        str(config_file),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )


async def stop_daemon(process: asyncio.subprocess.Process) -> bytes:
    process.terminate()
    returncode = await asyncio.wait_for(process.wait(), timeout=5)
    assert process.stderr is not None
    stderr = await process.stderr.read()
    assert returncode == 0, stderr.decode("utf-8", errors="replace")
    return stderr


def prepare_config(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    runtime = tmp_path / "runtime"
    workspace = tmp_path / "workspace"
    runtime.mkdir()
    workspace.mkdir()
    secrets = workspace / "secrets"
    secrets.mkdir()
    credential = secrets / "attachment-token"
    credential.write_text(f"{_TOKEN}\n")
    config = tmp_path / "a13n-envd.json"
    config.write_text(
        json.dumps(
            {
                "root_mount_id": "workspace",
                "mounts": [
                    {
                        "mount_id": "workspace",
                        "native_root": str(workspace),
                        "writable": True,
                        "allow_command_execution": False,
                        "max_file_bytes": 1024 * 1024,
                        "allowed_operations": ["open_reader", "open_writer"],
                    }
                ],
            }
        )
    )
    return runtime, workspace, credential, config


async def open_listener(
    *,
    tls: bool,
    authorize: Callable[[ServerConnection, Request], Awaitable[Response | None] | Response | None],
) -> tuple[object, asyncio.Queue[ServerConnection], str]:
    accepted: asyncio.Queue[ServerConnection] = asyncio.Queue()

    async def handler(connection: ServerConnection) -> None:
        pong_waiter = await connection.ping(b"before-initialize")
        await pong_waiter
        await accepted.put(connection)
        await connection.wait_closed()

    tls_context: ssl.SSLContext | None = None
    if tls:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.load_cert_chain(
            _TLS_FIXTURES / "server.pem",
            _TLS_FIXTURES / "server-key.pem",
        )
    server = await serve(
        handler,
        "127.0.0.1",
        0,
        ssl=tls_context,
        subprotocols=["eip.v1"],
        compression=None,
        process_request=authorize,
        ping_interval=None,
    )
    assert server.sockets
    port = server.sockets[0].getsockname()[1]
    scheme = "wss" if tls else "ws"
    return server, accepted, f"{scheme}://127.0.0.1:{port}/eip"


def test_real_daemon_authenticates_transfers_and_reconnects(tmp_path: Path) -> None:
    async def scenario() -> None:
        observed_authorization: list[str | None] = []

        async def authorize(connection: ServerConnection, request: Request) -> Response | None:
            authorization = request.headers.get("Authorization")
            observed_authorization.append(authorization)
            if authorization != f"Bearer {_TOKEN}":
                return connection.respond(401, "unauthorized")
            return None

        server, accepted, endpoint = await open_listener(tls=False, authorize=authorize)
        runtime, workspace, credential, config = prepare_config(tmp_path)
        process = await start_daemon(
            a13n_envd_binary(),
            endpoint=endpoint,
            credential_file=credential,
            config_file=config,
            runtime_dir=runtime,
        )
        try:
            first_connection = await asyncio.wait_for(accepted.get(), timeout=5)
            first = await EIPSession.initialize(
                AcceptedWebSocketTransport(first_connection),
                expected_environment_id=_ENVIRONMENT_ID,
                required_methods=("file.open_reader", "file.open_writer"),
                request_timeout=5,
            )
            generation = first.generation
            with pytest.raises(EIPMethodError):
                async with first.open_reader(EIPPath(mount_id="workspace", path="/secrets/attachment-token")):
                    pass

            path = EIPPath(mount_id="workspace", path="/binary.dat")
            payload = bytes(range(256)) * 8
            async with first.open_writer(path, mode=FileWriteMode.CREATE) as writer:
                await writer.write(payload[:777])
                await writer.write(payload[777:])
                committed = await writer.commit()
            assert committed.transferred_bytes == len(payload)
            assert (workspace / "binary.dat").read_bytes() == payload

            downloaded = bytearray()
            async with first.open_reader(path) as reader:
                async for chunk in reader:
                    downloaded.extend(chunk)
            assert bytes(downloaded) == payload

            await first.abort()
            second_connection = await asyncio.wait_for(accepted.get(), timeout=5)
            second = await EIPSession.initialize(
                AcceptedWebSocketTransport(second_connection),
                expected_environment_id=_ENVIRONMENT_ID,
                request_timeout=5,
            )
            assert second.generation == generation
            assert (await second.describe()).generation == generation
            await second.close()
        finally:
            stderr = await stop_daemon(process)
            server.close()
            await server.wait_closed()
        assert observed_authorization == [f"Bearer {_TOKEN}", f"Bearer {_TOKEN}"]
        assert _TOKEN.encode() not in stderr

    asyncio.run(scenario())


def test_real_daemon_validates_tls_and_hostname(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def authorize(connection: ServerConnection, request: Request) -> Response | None:
            if request.headers.get("Authorization") != f"Bearer {_TOKEN}":
                return connection.respond(401, "unauthorized")
            return None

        server, accepted, endpoint = await open_listener(tls=True, authorize=authorize)
        runtime, _workspace, credential, config = prepare_config(tmp_path)
        process = await start_daemon(
            a13n_envd_binary(),
            endpoint=endpoint,
            credential_file=credential,
            config_file=config,
            runtime_dir=runtime,
            ca_file=_TLS_FIXTURES / "ca.pem",
        )
        try:
            connection = await asyncio.wait_for(accepted.get(), timeout=5)
            session = await EIPSession.initialize(
                AcceptedWebSocketTransport(connection),
                expected_environment_id=_ENVIRONMENT_ID,
                request_timeout=5,
            )
            assert (await session.describe()).generation == session.generation
            await session.abort()
        finally:
            await stop_daemon(process)
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_rejected_token_stops_daemon_without_leaking_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def reject(connection: ServerConnection, request: Request) -> Response:
            assert request.headers.get("Authorization") == f"Bearer {_TOKEN}"
            return connection.respond(401, "unauthorized")

        server, _accepted, endpoint = await open_listener(tls=False, authorize=reject)
        runtime, _workspace, credential, config = prepare_config(tmp_path)
        process = await start_daemon(
            a13n_envd_binary(),
            endpoint=endpoint,
            credential_file=credential,
            config_file=config,
            runtime_dir=runtime,
        )
        try:
            returncode = await asyncio.wait_for(process.wait(), timeout=5)
            assert process.stderr is not None
            stderr = await process.stderr.read()
        finally:
            server.close()
            await server.wait_closed()
        assert returncode != 0
        assert b"attachment authorization failed" in stderr
        assert _TOKEN.encode() not in stderr

    asyncio.run(scenario())
