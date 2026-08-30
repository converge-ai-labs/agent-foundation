from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from a13n_environment_provider import (
    AcceptedWebSocketEIPSessionSource,
    EIPEnvironmentAttachment,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    HttpEIPSessionSource,
    LocalEnvdEnvironmentProvider,
    LocalEnvdProviderConfiguration,
    LocalEnvdProviderRuntime,
    LocalEnvdShellProfile,
    LocalEnvdWorkspaceConfiguration,
    StdioEIPSessionSource,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    CommandRequest,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentOutputPolicy,
    EnvironmentPermissionSet,
    PortTarget,
    ShellCommand,
)
from a13n_harness.environment.advanced import (
    EnvironmentRuntimeMount,
    create_environment_provider_binding,
    create_environment_runtime,
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
                        "allowed_operations": ["open_reader", "open_writer", "find", "search"],
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


async def exercise_attachment(
    attachment: EIPEnvironmentAttachment,
    expected: bytes,
    search_conformance: Any,
) -> None:
    provider_binding = create_environment_provider_binding(attachment)
    run_binding = create_environment_runtime(
        mounts={
            "workspace": EnvironmentRuntimeMount(
                binding=provider_binding,
                permission_ceiling=EnvironmentPermissionSet(
                    operations=frozenset(
                        {
                            EnvironmentAction.FILE_READ_BYTES,
                            EnvironmentAction.FILE_WRITE_BYTES,
                            EnvironmentAction.FILE_QUERY,
                            EnvironmentAction.FILE_SEARCH_TEXT,
                        }
                    )
                ),
                working_directory="/",
            )
        },
        default_mount="workspace",
    )
    instance = AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="agent"),
        agent_instance_id="agent-eip-e2e",
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield expected[:137]
        yield expected[137:]

    async with run_binding.bind(run_id="run-eip-e2e", instance=instance) as environment:
        result = await environment.files.write_bytes_stream(
            "/workspace/payload.bin",
            chunks(),
            mode="create",
        )
        assert result.bytes_written == len(expected)
        assert await environment.files.read_bytes("/workspace/payload.bin") == expected
        await search_conformance.assert_operator(environment.files, root="/workspace")


def _operation(
    action: EnvironmentManagementAction,
    suffix: str,
) -> EnvironmentOperationContext:
    return EnvironmentOperationContext(
        operation_id=f"operation-{suffix}",
        action=action,
        resource_correlation="resource-local-envd-e2e",
        attempt=1,
    )


def _local_envd_run_binding(attachment: EIPEnvironmentAttachment):
    return create_environment_runtime(
        mounts={
            "workspace": EnvironmentRuntimeMount(
                binding=create_environment_provider_binding(attachment),
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                working_directory="/",
            )
        },
        default_mount="workspace",
    )


def test_local_envd_provider_runs_full_harness_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "local-envd-workspace"
        runtime_parent = tmp_path / "local-envd-runtimes"
        workspace.mkdir()
        runtime_parent.mkdir()
        executable = Path(sys.executable).resolve()
        provider = LocalEnvdEnvironmentProvider(
            LocalEnvdProviderConfiguration(
                environment_id="local-envd-harness-e2e",
                workspace=LocalEnvdWorkspaceConfiguration(path=workspace),
                trusted_executable_roots=(executable.parent.parent,),
                shell_profiles=(
                    LocalEnvdShellProfile(
                        profile_id="python",
                        executable=executable,
                        fixed_arguments=("-c",),
                    ),
                ),
                max_output_preview_bytes=1024,
                max_output_bytes_per_stream=1024,
                max_spool_bytes=2048,
            ),
            LocalEnvdProviderRuntime(
                executable=agent_envd_binary(),
                allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
            ),
        )
        instance = AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="agent"),
            agent_instance_id="agent-local-envd-e2e",
        )
        policy = EnvironmentOutputPolicy(
            max_inline_bytes=4,
            max_output_bytes=64,
            overflow="retain",
        )
        resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))
        old_handle = None
        first_generation = None

        async with resource:
            async with resource.acquire_attachment() as attachment:
                binding = _local_envd_run_binding(attachment)
                async with binding.bind(run_id="run-local-envd-1", instance=instance) as environment:
                    await environment.files.write_text(
                        "/workspace/message.txt",
                        "local envd harness",
                        mode="create",
                    )
                    assert (await environment.files.read_text("/workspace/message.txt")).text == "local envd harness"

                    shell_result = await environment.shell.exec(
                        CommandRequest(
                            command=ShellCommand(
                                profile_id="python",
                                script="import sys; sys.stdout.write('retained-output-value')",
                            ),
                            output_policy=policy,
                        )
                    )
                    first_generation = shell_result.receipt.observed_generation
                    reference = shell_result.output.stdout.reference
                    assert reference is not None
                    retained = await environment.outputs.read(reference, policy=policy)
                    chunks = list(retained.chunks)
                    while retained.next_cursor is not None:
                        retained = await environment.outputs.read(
                            reference,
                            cursor=retained.next_cursor,
                            policy=policy,
                        )
                        chunks.extend(retained.chunks)
                    assert b"".join(chunk.data for chunk in chunks) == b"retained-output-value"
                    await environment.outputs.release(reference=reference)

                    port = reserve_port()
                    server_script = (
                        "import socket,time; "
                        "sock=socket.socket(); "
                        "sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
                        f"sock.bind(('127.0.0.1',{port})); "
                        "sock.listen(); time.sleep(30)"
                    )
                    started = await environment.processes.start(
                        CommandRequest(
                            command=ShellCommand(
                                profile_id="python",
                                script=server_script,
                            ),
                            output_policy=policy,
                        )
                    )
                    old_handle = started.process.handle
                    listening = await environment.ports.wait(
                        PortTarget(port=port),
                        desired="listening",
                        timeout_seconds=5,
                    )
                    assert listening.status == "listening"
                    await environment.processes.kill(old_handle)
                    completed = await environment.processes.wait(
                        old_handle,
                        condition="tree_cleaned",
                        timeout_seconds=5,
                    )
                    assert completed.status.cleanup == "complete"
                    await environment.processes.release(old_handle)

            async with resource.acquire_attachment() as attachment:
                binding = _local_envd_run_binding(attachment)
                async with binding.bind(run_id="run-local-envd-2", instance=instance) as environment:
                    result = await environment.shell.exec(
                        CommandRequest(
                            command=ShellCommand(
                                profile_id="python",
                                script="print('second attachment')",
                            ),
                            output_policy=policy,
                        )
                    )
                    assert result.receipt.observed_generation == first_generation

            paused = await provider.pause(
                resource,
                operation=_operation(EnvironmentManagementAction.PAUSE, "pause"),
                mode=EnvironmentPauseMode.FILESYSTEM,
            )
            assert not tuple(runtime_parent.iterdir())

        resumed = await provider.resume(
            paused,
            operation=_operation(EnvironmentManagementAction.RESUME, "resume"),
        )
        async with resumed:
            async with resumed.acquire_attachment() as attachment:
                binding = _local_envd_run_binding(attachment)
                async with binding.bind(run_id="run-local-envd-3", instance=instance) as environment:
                    result = await environment.shell.exec(
                        CommandRequest(
                            command=ShellCommand(
                                profile_id="python",
                                script="print('resumed')",
                            ),
                            output_policy=policy,
                        )
                    )
                    assert result.receipt.observed_generation != first_generation
                    assert old_handle is not None
                    with pytest.raises(EnvironmentError) as stale:
                        await environment.processes.inspect(old_handle)
                    assert stale.value.code == "environment_stale_mount"
            running = resumed.state

        await provider.destroy(
            running,
            operation=_operation(EnvironmentManagementAction.DESTROY, "destroy"),
        )
        assert workspace.joinpath("message.txt").read_text() == "local envd harness"
        assert not tuple(runtime_parent.iterdir())

    asyncio.run(scenario())


def test_harness_attachment_runs_over_stdio_http_and_reverse_websocket(
    tmp_path: Path,
    file_search_conformance: Any,
) -> None:
    async def scenario() -> None:
        payload = bytes(range(256)) * 8

        stdio_runtime, stdio_workspace, _credential, stdio_config = prepare_environment(tmp_path, "stdio")
        file_search_conformance.populate(stdio_workspace)
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
            file_search_conformance,
        )
        assert (stdio_workspace / "payload.bin").read_bytes() == payload
        await stop_daemon(stdio_process)

        http_runtime, http_workspace, http_credential, http_config = prepare_environment(tmp_path, "http")
        file_search_conformance.populate(http_workspace)
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
            file_search_conformance,
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
        file_search_conformance.populate(ws_workspace)
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
                file_search_conformance,
            )
            assert (ws_workspace / "payload.bin").read_bytes() == payload
        finally:
            await stop_daemon(ws_process)
            websocket_server.close()
            await websocket_server.wait_closed()

    asyncio.run(scenario())
