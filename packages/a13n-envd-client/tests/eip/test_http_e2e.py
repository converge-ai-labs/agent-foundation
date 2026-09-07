from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path

import pytest
from a13n_envd_client import EIPSession, EIPTransportError, HttpTransport
from a13n_envd_client.eip.v1 import EIPPath, FileWriteMode

_TOKEN = "test-http-attachment-token"
_ENVIRONMENT_ID = "env-http-e2e"


def a13n_envd_binary() -> Path:
    configured = os.environ.get("A13N_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("set A13N_ENVD_TEST_BINARY to run Rust daemon E2E tests")
    binary = Path(configured)
    assert binary.is_file(), f"a13n-envd test binary does not exist: {binary}"
    return binary


def reserve_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


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
    raise AssertionError("a13n-envd HTTP listener did not become ready")


async def start_daemon(tmp_path: Path, port: int) -> tuple[asyncio.subprocess.Process, Path]:
    runtime = tmp_path / "runtime"
    workspace = tmp_path / "workspace"
    runtime.mkdir()
    workspace.mkdir()
    credential = tmp_path / "attachment-token"
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
    environment = {
        "A13N_ENVD_ENVIRONMENT_ID": _ENVIRONMENT_ID,
        "A13N_ENVD_TRANSPORT": "http",
        "A13N_ENVD_HTTP_BIND": f"127.0.0.1:{port}",
        "A13N_ENVD_HTTP_CREDENTIAL_FILE": str(credential),
        "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "loopback",
        "A13N_ENVD_RUNTIME_DIR": str(runtime),
        "A13N_ENVD_EXECUTION_ISOLATION": "disabled",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    process = await asyncio.create_subprocess_exec(
        str(a13n_envd_binary()),
        "--config",
        str(config),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    await wait_until_listening(port)
    return process, workspace


async def stop_daemon(process: asyncio.subprocess.Process) -> bytes:
    process.terminate()
    returncode = await asyncio.wait_for(process.wait(), timeout=5)
    assert process.stderr is not None
    stderr = await process.stderr.read()
    assert returncode == 0, stderr.decode("utf-8", errors="replace")
    return stderr


def test_real_daemon_http_auth_transfers_and_sequential_sessions(tmp_path: Path) -> None:
    async def scenario() -> None:
        port = reserve_port()
        process, workspace = await start_daemon(tmp_path, port)
        endpoint = f"http://127.0.0.1:{port}"
        try:
            with pytest.raises(EIPTransportError, match="status 401"):
                await EIPSession.initialize(
                    HttpTransport(endpoint, "wrong-token", request_timeout=5),
                    expected_environment_id=_ENVIRONMENT_ID,
                    request_timeout=5,
                )

            first = await EIPSession.initialize(
                HttpTransport(endpoint, _TOKEN, request_timeout=5),
                expected_environment_id=_ENVIRONMENT_ID,
                required_methods=("file.open_reader", "file.open_writer"),
                request_timeout=5,
            )
            generation = first.generation
            path = EIPPath(mount_id="workspace", path="/binary.dat")
            payload = bytes(range(256)) * 16
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
            await first.close()

            second = await EIPSession.initialize(
                HttpTransport(endpoint, _TOKEN, request_timeout=5),
                expected_environment_id=_ENVIRONMENT_ID,
                request_timeout=5,
            )
            assert second.generation == generation
            await second.close()
        finally:
            stderr = await stop_daemon(process)
        assert _TOKEN.encode() not in stderr

    asyncio.run(scenario())
