"""Local operator scaffolding for trying both remote transports with one command.

Only this demo owns the subprocess and temporary files. The remote Provider does
not launch, stop, or delete anything on the remote machine.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import SecretStr

from .remote import RemoteExampleResult, run_http, run_websocket


async def run_demo(executable: Path, transport: Literal["http", "websocket"]) -> RemoteExampleResult:
    executable = executable.expanduser().resolve(strict=True)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    token = SecretStr(secrets.token_urlsafe(32))
    native_id = "env-demo-daemon"
    with TemporaryDirectory(prefix="envd-example-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        credential_file = root / "credential"
        credential_file.write_text(token.get_secret_value())
        credential_file.chmod(0o600)
        config = root / "envd.json"
        config.write_text(
            json.dumps(
                {
                    "device_id": native_id,
                    "default_working_directory": str(workspace),
                    "trusted_executable_roots": [],
                    "shell_profiles": [],
                    "limits": {"max_file_bytes": 1024 * 1024},
                }
            )
        )
        env = {
            "A13N_ENVD_RUNTIME_DIR": str(root / "runtime"),
        }
        if transport == "http":
            env.update(
                A13N_ENVD_TRANSPORT="http",
                A13N_ENVD_HTTP_BIND=f"127.0.0.1:{port}",
                A13N_ENVD_HTTP_CREDENTIAL_FILE=str(credential_file),
                A13N_ENVD_HTTP_PLAINTEXT_SCOPE="loopback",
            )
        else:
            env.update(
                A13N_ENVD_TRANSPORT="reverse_websocket",
                A13N_ENVD_REVERSE_WS_URL=f"ws://127.0.0.1:{port}",
                A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE=str(credential_file),
            )
        process = await asyncio.create_subprocess_exec(
            str(executable),
            "--config",
            str(config),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            if transport == "http":
                async with asyncio.timeout(10):
                    while True:
                        try:
                            _, writer = await asyncio.open_connection("127.0.0.1", port)
                        except OSError:
                            if process.returncode is not None:
                                raise RuntimeError("Demo daemon exited before becoming ready") from None
                            await asyncio.sleep(0.03)
                            continue
                        writer.close()
                        await writer.wait_closed()
                        break
                result = await run_http(f"http://127.0.0.1:{port}", token, native_id)
            else:
                result = await run_websocket(token=token, device_id=native_id, port=port)
            assert process.returncode is None, "Provider close must preserve the daemon"
            assert (workspace / "provider-example.txt").read_text() == result.text
            return result
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.communicate(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.communicate()
