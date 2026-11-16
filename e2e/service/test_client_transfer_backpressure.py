"""A real Worker imports a reverse Envd file across an asymmetric, rate-limited TCP link."""

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest

from .api import eventually, expect
from .scripted import tool_results
from .stack import read_rows

BINARY = os.environ.get("A13N_ENVD_TEST_BINARY")
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.isolated_service(start_workers=1),
]


@dataclass
class ThrottledLink:
    connections: int = 0
    uploaded: int = 0
    tasks: set[asyncio.Task] = field(default_factory=set)

    async def connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, port: int) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        self.connections += 1
        upstream = None
        try:
            response, upstream = await asyncio.open_connection("127.0.0.1", port)

            async def pump(source, destination, rate, *, upload=False):
                await asyncio.sleep(0.035)
                while data := await source.read(1024):
                    # Limit actual carrier bytes, not the application's consumption of file chunks.
                    await asyncio.sleep(len(data) / rate)
                    destination.write(data)
                    await destination.drain()
                    if upload:
                        self.uploaded += len(data)

            pumps = [
                asyncio.create_task(pump(reader, upstream, 20_000, upload=True)),
                asyncio.create_task(pump(response, writer, 200_000)),
            ]
            try:
                done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    finished.result()
            finally:
                for pending in pumps:
                    pending.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
        except (ConnectionError, OSError):
            pass
        finally:
            for connection in (writer, upstream):
                if connection is not None:
                    connection.close()
                    try:
                        await connection.wait_closed()
                    except (ConnectionError, OSError):
                        pass
            self.tasks.discard(task)

    @asynccontextmanager
    async def serve(self, port):
        server = await asyncio.start_server(lambda r, w: self.connect(r, w, port), "127.0.0.1", 0)
        try:
            yield server.sockets[0].getsockname()[1]
        finally:
            server.close()
            await server.wait_closed()
            active = tuple(self.tasks)
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)


@pytest.mark.parametrize("frame_bytes", [None, 8192], ids=["default-chunks", "small-frame-diagnostic"])
async def test_asset_ref_and_sibling_controls_survive_slow_reverse_link(stack, frame_bytes, request):
    if BINARY is None or not Path(BINARY).is_file():
        reason = "Build this checkout's a13n-envd and set A13N_ENVD_TEST_BINARY"
        if request.config.getoption("--require-all"):
            pytest.fail(reason)
        pytest.skip(reason)
    api, model = stack.api, stack.model
    workspace = stack.directory / "native"
    workspace.mkdir()
    payload = os.urandom(1024 * 1024)
    source = workspace / "binary.dat"
    source.write_bytes(payload)
    probe = workspace / "control.txt"
    probe.write_text("control is responsive")
    device = "device-" + uuid4().hex
    registered = expect(
        await api.client.post(
            "/api/v1/client-environments",
            json={"device_id": device, "name": "Slow link", "default_working_directory": str(workspace)},
        ),
        201,
    )
    environment_id = registered["client"]["environment"]["id"]
    credential = stack.directory / "device-token"
    credential.write_text(registered["token"])
    credential.chmod(0o600)
    agent = await api.create_agent(
        "Import across slow link",
        await api.create_model(model.base_url),
        default_environment_id=environment_id,
        toolsets={"object_store": {"enabled": True}, "files": {"enabled": True}},
    )
    config = {"default_working_directory": str(workspace), "full_control": True, "idle_timeout_ms": 3_600_000}
    if frame_bytes is not None:
        config["limits"] = {"max_transfer_frame_bytes": frame_bytes}
    link = ThrottledLink()
    async with link.serve(stack.control.port) as port:
        with (stack.directory / "envd.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                BINARY,
                env={
                    "PATH": os.environ["PATH"],
                    "A13N_ENVD_DEVICE_ID": device,
                    "A13N_ENVD_RUNTIME_DIR": str(stack.directory / "envd-state"),
                    "A13N_ENVD_TRANSPORT": "reverse_websocket",
                    "A13N_ENVD_REVERSE_WS_URL": f"wss://127.0.0.1:{port}" + registered["connection_path"],
                    "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE": str(credential),
                    "A13N_ENVD_REVERSE_WS_CA_FILE": str(stack.stores.certificate),
                    "A13N_ENVD_CONFIG_JSON": json.dumps(config),
                },
                stdout=asyncio.subprocess.DEVNULL,
                stderr=log,
            )
            try:

                async def online():
                    assert process.returncode is None
                    current = expect(await api.client.get(f"/api/v1/client-environments/{environment_id}"), 200)
                    return current["environment"]["client_connection"]["state"] == "online"

                await eventually(online)
                await model.call("asset_ref", {"path": "/workspace/binary.dat"}, call_id="import", to="[import]")
                await model.say("imported", to="[import]")
                receipt = await api.start(agent, "[import] Import the binary file")

                async def transferring():
                    if link.uploaded > 64 * 1024:
                        return True
                    current = await api.run(receipt["run"]["id"])
                    assert current["status"] not in {"completed", "failed", "cancelled", "waiting"}, (
                        current["status"],
                        [value for request in await model.requests("[import]") for value in tool_results(request)],
                    )
                    return False

                await eventually(transferring)
                # Independent Sessions share this carrier. Real control responses must arrive while
                # the file is still being transferred, not only after the data queue empties.
                siblings = []
                for index in range(3):
                    marker = f"[control-{index}]"
                    await model.call(
                        "view", {"file_path": "/workspace/control.txt"}, call_id=f"probe-{index}", to=marker
                    )
                    await model.say("responsive", to=marker)
                    sibling = await api.start(agent, marker + " Read the control file")
                    siblings.append((marker, sibling))
                for marker, sibling in siblings:
                    result = await api.sealed(sibling["run"]["id"], timeout=90)
                    assert result["status"] == "completed", result
                    recorded = await model.requests(marker)
                    assert any(
                        "control is responsive" in value for request in recorded for value in tool_results(request)
                    )
                    assert link.uploaded < len(payload), "control made progress only after the transfer drained"

                result = await api.sealed(receipt["run"]["id"], timeout=120)
                assert result["status"] == "completed", result
                requests = await model.requests("[import]")
                values = [json.loads(value) for request in requests for value in tool_results(request)]
                imported = next((value for value in values if isinstance(value, dict) and "ref" in value), None)
                assert imported is not None, values
                assert imported["ref"].startswith("a13n-object://")
                assert imported["size"] == len(payload)
                assert imported["digest"] == hashlib.sha256(payload).hexdigest()
                [published] = await asyncio.to_thread(
                    read_rows,
                    stack.database,
                    "SELECT content_ref, retention FROM thread_objects WHERE id = :id AND thread_id = :thread",
                    id=imported["object_id"],
                    thread=receipt["thread"]["id"],
                )
                assert published["retention"] == "temporary"
                assert (stack.directory / "objects" / published["content_ref"]).read_bytes() == payload
                assert link.connections == 1, "the carrier reconnected during a valid transfer"
                assert await online()
            except BaseException:
                print("Slow-link diagnostic:", {"uploaded": link.uploaded, "connections": link.connections})
                print("Native daemon:", (stack.directory / "envd.log").read_text())
                print("Model tools:", [tool_results(row) for row in await model.requests()])
                raise
            finally:
                if process.returncode is None:
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
