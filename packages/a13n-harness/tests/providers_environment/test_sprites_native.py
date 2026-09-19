"""Real public operations over a fixture-owned native WebSocket endpoint."""

import asyncio
import json
import sys
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.commands import CommandRequest, ShellCommand
from a13n_harness.providers.environment.models import EnvironmentState
from a13n_harness.providers.environment.native.http import NativeHTTP
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy
from a13n_harness.providers.environment.sprites import provider as sprites
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Fixture executes POSIX guest helpers on the Host")


def test_sprites_native_roundtrip_and_reconstruction(tmp_path, monkeypatch):
    async def scenario():
        targets = {}
        calls = []

        def http(request):
            calls.append(request.method)
            if request.method == "POST":
                body = json.loads(request.content)
                targets[body["name"]] = {**body, "id": f"native-{len(calls)}", "status": "cold"}
                return httpx2.Response(201, json=targets[body["name"]])
            name = request.url.path.rsplit("/", 1)[1]
            if request.method == "DELETE":
                targets.pop(name, None)
                return httpx2.Response(204)
            return httpx2.Response(200, json=targets[name]) if name in targets else httpx2.Response(404)

        def transport_init(self, key, url, token, timeout, **kwargs):
            self.key = key
            self.client = httpx2.AsyncClient(base_url=url, transport=httpx2.MockTransport(http))

        monkeypatch.setattr(NativeHTTP, "__init__", transport_init)

        async def exec_socket(ws):
            args = parse_qs(urlsplit(ws.request.path).query)["cmd"]
            process = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            frame = await ws.recv()
            assert frame[0] == 0
            assert await ws.recv() == b"\x04"
            stdout, stderr = await process.communicate(frame[1:])
            assert process.returncode == 0, stderr.decode()
            await ws.send(b"\x01" + stdout)
            await ws.send(b"\x02" + stderr)
            await ws.send(bytes([3, process.returncode]))

        async with serve(exec_socket, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def local_connect(url, **kwargs):
                parsed = urlsplit(url)
                return connect(f"ws://127.0.0.1:{port}{parsed.path}?{parsed.query}", **kwargs)

            monkeypatch.setattr(sprites, "connect", local_connect)
            provider = ProviderCatalog(select_builtin_environment_providers(["sprites"])).require("sprites")
            config = provider.validate_environment({"root": str(tmp_path), "python": sys.executable})
            runtime = await provider.runtime_factory(
                configuration=provider.configuration_model(organization="fixture"),
                credential=provider.credential_model(api_key="fixture"),
                allow_create=True,
                operation_id="op-test",
            )

            def create(state=None):
                return provider.construct(configuration=config, environment_id="env-test", state=state, runtime=runtime)

            first = create()
            await first.prepare()
            await first.enter(mount_id="mount-one")
            await first.operations.files.write_text("/hello", "persisted", mode="upsert")
            result = await first.operations.shell.exec(
                CommandRequest(
                    command=ShellCommand(profile_id="default", script="cat hello; printf error >&2"),
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=1024, max_output_bytes=1024, overflow="truncate"
                    ),
                )
            )
            assert result.output.stdout.inline == b"persisted"
            assert result.output.stderr.inline == b"error"
            assert result.status.exit_code == 0
            state = EnvironmentState.model_validate_json(first.dump_state().model_dump_json())
            identity = first.descriptor.backing_identity
            await first.close()
            second = create(state)
            await second.enter(mount_id="mount-two")
            await second.prepare()
            assert (await second.operations.files.read_text("/hello")).text == "persisted"
            assert second.descriptor.backing_identity == identity
            assert calls.count("POST") == 1
            await second.close()
            cleanup = create(state)
            await cleanup.destroy()
            await cleanup.close()
            assert not targets

    asyncio.run(scenario())
