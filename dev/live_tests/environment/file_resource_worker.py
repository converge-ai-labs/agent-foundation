"""Run inside an owned Linux container whose /limited is a 1 MiB tmpfs."""

import asyncio
import errno
import hashlib
import json
import os
import socket
import sys
import tempfile
import traceback
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.providers.environment.local_envd.provider import LOCAL_ENVD
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_harness.providers.environment.models import EnvironmentError, EnvironmentState
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.connections import WebSocketEnvdConnections
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD, HttpEnvdProviderRuntime
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD, WebSocketEnvdProviderRuntime
from pydantic import SecretStr
from websockets.asyncio.server import serve

ROOT = Path("/limited")
TOKEN = "disposable-container-loopback-token"


def snapshot(root=ROOT):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}


@asynccontextmanager
async def environment(kind):
    async with AsyncExitStack() as stack:
        if kind == "direct_local":
            provider = DIRECT_LOCAL
            configuration = {"root": {"path": str(ROOT)}}
            runtime = None
            state = None
        elif kind == "local_envd":
            provider = LOCAL_ENVD
            configuration = {"workspace": {"path": str(ROOT)}, "max_file_bytes": 4 * 1024 * 1024}
            runtime = LocalEnvdProviderRuntime(
                executable=Path("/usr/local/bin/a13n-envd"),
                allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
            )
            state = None
        else:
            configuration = {}
            if kind == "http_envd":
                provider = HTTP_ENVD
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                endpoint = f"http://127.0.0.1:{port}"
                runtime = HttpEnvdProviderRuntime(
                    HttpEnvdConnectionConfiguration(endpoint=endpoint, request_timeout=10),
                    HttpEnvdCredential(token=SecretStr(TOKEN)),
                )
            else:
                provider = WEBSOCKET_ENVD
                hub = await stack.enter_async_context(WebSocketEnvdConnections())

                async def attach(connection):
                    await hub.attach("env-storage", connection)

                def authorize(connection, request):
                    if request.headers.get("Authorization") != "Bearer " + TOKEN:
                        return connection.respond(401, "unauthorized")
                    return None

                server = await stack.enter_async_context(
                    serve(
                        attach,
                        "127.0.0.1",
                        0,
                        subprotocols=["eip.v1"],
                        process_request=authorize,
                        compression=None,
                    )
                )
                endpoint = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/envd"
                runtime = WebSocketEnvdProviderRuntime(hub)
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="file-resource-")))
            (directory / "runtime").mkdir()
            token = directory / "token"
            token.write_text(TOKEN)
            token.chmod(0o600)
            config = directory / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "root_mount_id": "workspace",
                        "mounts": [
                            {
                                "mount_id": "workspace",
                                "native_root": str(ROOT),
                                "writable": True,
                                "allow_command_execution": False,
                                "max_file_bytes": 4 * 1024 * 1024,
                            }
                        ],
                    }
                )
            )
            values = {
                "A13N_ENVD_ENVIRONMENT_ID": "env-storage",
                "A13N_ENVD_RUNTIME_DIR": str(directory / "runtime"),
                "A13N_ENVD_EXECUTION_ISOLATION": "disabled",
            }
            if kind == "http_envd":
                values.update(
                    A13N_ENVD_TRANSPORT="http",
                    A13N_ENVD_HTTP_BIND=endpoint.removeprefix("http://"),
                    A13N_ENVD_HTTP_CREDENTIAL_FILE=str(token),
                    A13N_ENVD_HTTP_PLAINTEXT_SCOPE="loopback",
                )
            else:
                values.update(
                    A13N_ENVD_TRANSPORT="reverse_websocket",
                    A13N_ENVD_REVERSE_WS_URL=endpoint,
                    A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE=str(token),
                )
            log = stack.enter_context((directory / "daemon.log").open("wb"))
            daemon = await asyncio.create_subprocess_exec(
                "/usr/local/bin/a13n-envd",
                "--config",
                str(config),
                env=values,
                stdout=log,
                stderr=log,
            )

            async def stop():
                if daemon.returncode is None:
                    daemon.terminate()
                try:
                    await asyncio.wait_for(daemon.wait(), 5)
                except TimeoutError:
                    daemon.kill()
                    await daemon.wait()

            stack.push_async_callback(stop)
            if kind == "http_envd":
                async with asyncio.timeout(10):
                    while True:
                        assert daemon.returncode is None, (directory / "daemon.log").read_text()
                        try:
                            _, writer = await asyncio.open_connection("127.0.0.1", port)
                        except OSError:
                            await asyncio.sleep(0.02)
                            continue
                        writer.close()
                        await writer.wait_closed()
                        break
            state = EnvironmentState(
                provider_key=provider.type, state_version="1", state={"daemon_environment_id": "env-storage"}
            )

        adapter = provider.construct(
            environment_id="env-storage",
            configuration=provider.validate_environment(schema_version="1", value=configuration),
            state=state,
            runtime=runtime,
        )
        stack.push_async_callback(adapter.close)
        await adapter.enter(mount_id="workspace")
        await adapter.prepare()
        try:
            yield adapter
        except BaseException:
            traceback.print_exc()
            raise


async def main(kind):
    assert os.geteuid() != 0
    assert ROOT.is_mount(), "The storage test must not fill an ordinary host directory"
    fs = os.statvfs(ROOT)
    capacity = fs.f_blocks * fs.f_frsize
    assert 0 < capacity <= 1024 * 1024
    (ROOT / "destination").write_text("ORIGINAL")
    fs = os.statvfs(ROOT)
    (ROOT / "filler").write_bytes(b"x" * (fs.f_bavail * fs.f_frsize - 65536))
    before = snapshot()
    codes = {}
    async with environment(kind) as adapter:
        files = adapter.operations.files

        async def payload():
            for _ in range(8):
                yield b"y" * 65536

        for mode in ("create", "replace", "append"):
            path = "/new" if mode == "create" else "/destination"
            try:
                await files.write_bytes_stream(path, payload(), mode=mode)
            except EnvironmentError as error:
                assert error.code == "environment_provider_failure", error.code
                codes[mode] = error.code
            else:
                raise AssertionError("Oversized write succeeded on the full filesystem")
            assert snapshot() == before, "Failed publication changed files or leaked a candidate"
        try:
            (ROOT / "probe").write_bytes(b"z" * (2 * 1024 * 1024))
        except OSError as error:
            assert error.errno == errno.ENOSPC
        else:
            raise AssertionError("The kernel did not report ENOSPC")
        finally:
            (ROOT / "probe").unlink(missing_ok=True)
        assert snapshot() == before
        (ROOT / "filler").unlink()
        await files.write_text("/destination", "RECOVERED", mode="replace")
        assert await files.read_bytes("/destination") == b"RECOVERED"
        assert {path.name for path in ROOT.iterdir()} == {"destination"}
    print(
        json.dumps(
            {
                "backend": kind,
                "capacity": capacity,
                "errno": errno.ENOSPC,
                "failures": codes,
                "destination_preserved": True,
                "staging_cleaned": True,
                "recovered": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
