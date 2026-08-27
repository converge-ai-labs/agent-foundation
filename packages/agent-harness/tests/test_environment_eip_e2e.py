from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_environment_provider import (
    AcceptedWebSocketEIPSessionSource,
    EIPEnvironmentAttachment,
    HttpEIPSessionSource,
    StdioEIPSessionSource,
)
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentPermissionSet,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    create_environment_provider_binding,
    create_environment_run_binding,
)
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

_TOKEN = "harness-eip-attachment-token"
_ENVIRONMENT_ID = "env-harness-eip"


def agent_envd_binary() -> Path:
    configured = os.environ.get("AGENT_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("set AGENT_ENVD_TEST_BINARY to run Rust daemon E2E tests")
    binary = Path(configured)
    assert binary.is_file(), f"agent-envd test binary does not exist: {binary}"
    return binary


def reserve_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def prepare_environment(tmp_path: Path, name: str) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / name
    runtime = root / "runtime"
    workspace = root / "workspace"
    root.mkdir()
    runtime.mkdir()
    workspace.mkdir()
    credential = root / "attachment-token"
    credential.write_text(f"{_TOKEN}\n")
    config = root / "agent-envd.json"
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


async def start_daemon(
    *,
    runtime: Path,
    config: Path,
    transport_environment: dict[str, str],
    stdio: bool,
) -> asyncio.subprocess.Process:
    environment = {
        "AGENT_ENVD_ENVIRONMENT_ID": _ENVIRONMENT_ID,
        "AGENT_ENVD_RUNTIME_DIR": str(runtime),
        "AGENT_ENVD_EXECUTION_ISOLATION": "disabled",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        **transport_environment,
    }
    return await asyncio.create_subprocess_exec(
        str(agent_envd_binary()),
        "--config",
        str(config),
        stdin=asyncio.subprocess.PIPE if stdio else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )


async def stop_daemon(process: asyncio.subprocess.Process) -> bytes:
    if process.returncode is None:
        process.terminate()
    returncode = await asyncio.wait_for(process.wait(), timeout=5)
    assert process.stderr is not None
    stderr = await process.stderr.read()
    assert returncode == 0, stderr.decode("utf-8", errors="replace")
    return stderr


async def wait_until_listening(port: int) -> None:
    for _ in range(100):
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.03)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise AssertionError("agent-envd listener did not become ready")


async def exercise_attachment(attachment: EIPEnvironmentAttachment, expected: bytes) -> None:
    provider_binding = create_environment_provider_binding(attachment)
    run_binding = create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(
                EnvironmentBindingRequest(
                    binding_id="binding-eip",
                    binding_revision=1,
                    alias="workspace",
                    permission_ceiling=EnvironmentPermissionSet(
                        operations=frozenset(
                            {
                                EnvironmentAction.FILE_READ_BYTES,
                                EnvironmentAction.FILE_WRITE_BYTES,
                            }
                        )
                    ),
                    default_working_directory="/",
                    provider_binding=provider_binding,
                ),
            ),
            default_binding_id="binding-eip",
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    instance = AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="agent"),
        agent_instance_id="agent-eip-e2e",
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield expected[:137]
        yield expected[137:]

    async with run_binding.bind(run_id="run-eip-e2e", instance=instance) as environment:
        await environment.activate()
        result = await environment.files.write_bytes_stream(
            "/workspace/payload.bin",
            chunks(),
            mode="create",
        )
        assert result.bytes_written == len(expected)
        assert await environment.files.read_bytes("/workspace/payload.bin") == expected


def test_harness_attachment_runs_over_stdio_http_and_reverse_websocket(tmp_path: Path) -> None:
    async def scenario() -> None:
        payload = bytes(range(256)) * 8

        stdio_runtime, stdio_workspace, _credential, stdio_config = prepare_environment(tmp_path, "stdio")
        stdio_process = await start_daemon(
            runtime=stdio_runtime,
            config=stdio_config,
            transport_environment={"AGENT_ENVD_TRANSPORT": "stdio"},
            stdio=True,
        )
        await exercise_attachment(
            EIPEnvironmentAttachment(
                attachment_id="attachment-stdio",
                environment_id=_ENVIRONMENT_ID,
                session_source=StdioEIPSessionSource(stdio_process, request_timeout=5),
            ),
            payload,
        )
        assert (stdio_workspace / "payload.bin").read_bytes() == payload
        await stop_daemon(stdio_process)

        http_runtime, http_workspace, http_credential, http_config = prepare_environment(tmp_path, "http")
        http_port = reserve_port()
        http_process = await start_daemon(
            runtime=http_runtime,
            config=http_config,
            transport_environment={
                "AGENT_ENVD_TRANSPORT": "http",
                "AGENT_ENVD_HTTP_BIND": f"127.0.0.1:{http_port}",
                "AGENT_ENVD_HTTP_CREDENTIAL_FILE": str(http_credential),
                "AGENT_ENVD_HTTP_PLAINTEXT_SCOPE": "loopback",
            },
            stdio=False,
        )
        await wait_until_listening(http_port)
        await exercise_attachment(
            EIPEnvironmentAttachment(
                attachment_id="attachment-http",
                environment_id=_ENVIRONMENT_ID,
                session_source=HttpEIPSessionSource(
                    f"http://127.0.0.1:{http_port}",
                    _TOKEN,
                    request_timeout=5,
                ),
            ),
            payload,
        )
        assert (http_workspace / "payload.bin").read_bytes() == payload
        await stop_daemon(http_process)

        accepted: asyncio.Queue[ServerConnection] = asyncio.Queue()

        async def authorize(connection: ServerConnection, request: Request) -> Response | None:
            if request.headers.get("Authorization") != f"Bearer {_TOKEN}":
                return connection.respond(401, "unauthorized")
            return None

        async def handler(connection: ServerConnection) -> None:
            await accepted.put(connection)
            await connection.wait_closed()

        websocket_server = await serve(
            handler,
            "127.0.0.1",
            0,
            subprotocols=["eip.v1"],
            compression=None,
            process_request=authorize,
            ping_interval=None,
        )
        assert websocket_server.sockets
        websocket_port = websocket_server.sockets[0].getsockname()[1]
        ws_runtime, ws_workspace, ws_credential, ws_config = prepare_environment(tmp_path, "websocket")
        ws_process = await start_daemon(
            runtime=ws_runtime,
            config=ws_config,
            transport_environment={
                "AGENT_ENVD_TRANSPORT": "reverse_websocket",
                "AGENT_ENVD_REVERSE_WS_URL": f"ws://127.0.0.1:{websocket_port}/eip",
                "AGENT_ENVD_REVERSE_WS_CREDENTIAL_FILE": str(ws_credential),
            },
            stdio=False,
        )
        connection = await asyncio.wait_for(accepted.get(), timeout=5)
        try:
            await exercise_attachment(
                EIPEnvironmentAttachment(
                    attachment_id="attachment-websocket",
                    environment_id=_ENVIRONMENT_ID,
                    session_source=AcceptedWebSocketEIPSessionSource(connection, request_timeout=5),
                ),
                payload,
            )
            assert (ws_workspace / "payload.bin").read_bytes() == payload
        finally:
            await stop_daemon(ws_process)
            websocket_server.close()
            await websocket_server.wait_closed()

    asyncio.run(scenario())
