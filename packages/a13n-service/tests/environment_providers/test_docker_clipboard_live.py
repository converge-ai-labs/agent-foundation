"""Opt-in production Envd clipboard and human-handoff acceptance on owned Docker desktops.

Image must contain current Envd, the desktop access supervisor, GTK3 GI and python3-xlib.
No hosted models, host display sockets or user credentials are used.
"""

import asyncio
import io
import json
import os
import struct
import tarfile
from pathlib import Path
from uuid import uuid4

import pytest
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPMethodError
from a13n_harness.environment.computer import ComputerPressKeys
from a13n_harness.environment.models import COMPUTER_ACTIONS, EnvironmentPermissionSet
from a13n_service.providers.environments.docker.definition import DOCKER
from a13n_service.runs.environments.maintenance import maintain_environments
from a13n_service.runs.environments.schemas import Handle

from ..environments_support import environment, reserve, stored
from ..test_desktop_assistance import control, post
from ..test_desktop_takeover import open_desktop
from .desktop_access_rfb import RFB
from .test_computer_x11 import returned

IMAGE = os.environ.get("A13N_TEST_DOCKER_CLIPBOARD_IMAGE")
pytestmark = [pytest.mark.anyio, pytest.mark.skipif(not IMAGE, reason="Requires disposable Docker clipboard image")]


async def eventually(read, predicate):
    async with asyncio.timeout(20):
        while True:
            value = await read()
            if predicate(value):
                return value
            await asyncio.sleep(0.02)


@pytest.fixture
async def desktop(service, runs_kit, tmp_path):
    import docker

    await runs_kit.pause_sweeps(service)
    engine = docker.from_env()
    box = relay = None
    env_id = None
    # Forward raw WebSocket traffic from a loopback-published container to the private desktop.
    # This is test routing only; Service still owns credentials and revocation.
    relay_script = """import asyncio,json
from pathlib import Path
async def handle(r,w):
 u=None; tasks=[]
 try:
  route=json.loads(Path('/route/route.json').read_text())
  rr,u=await asyncio.open_connection(route['ip'],6081)
  async def copy(a,b):
   while data:=await a.read(65536): b.write(data);await b.drain()
  tasks=[asyncio.create_task(copy(r,u)),asyncio.create_task(copy(rr,w))]
  await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
 finally:
  for task in tasks: task.cancel()
  await asyncio.gather(*tasks,return_exceptions=True)
  w.close()
  if u:u.close()
async def main():
 async with await asyncio.start_server(handle,'0.0.0.0',6081) as s:await s.serve_forever()
asyncio.run(main())
"""
    (tmp_path / "relay.py").write_text(relay_script)
    try:
        relay = await asyncio.to_thread(
            engine.containers.run,
            IMAGE,
            entrypoint=["python3", "/route/relay.py"],
            command=[],
            detach=True,
            ports={"6081/tcp": ("127.0.0.1", None)},
            volumes={str(tmp_path): {"bind": "/route", "mode": "ro"}},
        )
        await asyncio.to_thread(relay.reload)
        viewer = (
            "ws://127.0.0.1:" + relay.attrs["NetworkSettings"]["Ports"]["6081/tcp"][0]["HostPort"] + "/{container_id}"
        )
        provider = await service.client.post(
            f"{service.api}/environment-providers",
            json={
                "type": "docker",
                "name": "Clipboard acceptance",
                "config": {"envd_http": {"host": "127.0.0.1"}},
            },
        )
        assert provider.status_code == 201, provider.text
        template = await service.client.post(
            f"{service.api}/environment-templates",
            json={
                "name": "Clipboard desktop",
                "provider_id": provider.json()["id"],
                "config": {
                    "recipe": {
                        "image": IMAGE,
                        "desktop": True,
                        "desktop_assistance": True,
                        "desktop_viewer_url": viewer,
                        "execution": "envd_http",
                        "pull_policy": "never",
                    }
                },
            },
        )
        assert template.status_code == 201, template.text
        instance = await reserve(service, template.json()["id"])
        env_id = instance["id"]
        async with asyncio.timeout(60):
            while True:
                await maintain_environments(service.runtime, owner="clipboard-test")
                state = await environment(service, env_id)
                if state["status"] == "ready":
                    break
                assert state["status"] in {"creating", "pending"}, state
                await asyncio.sleep(0.5)
        box = await asyncio.to_thread(engine.containers.get, "a13n-" + env_id)
        await asyncio.to_thread(box.reload)
        (tmp_path / "route.json").write_text(
            json.dumps({"ip": box.attrs["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]})
        )
        source = Path(__file__).resolve().parents[4] / "dev/fixtures/linux-desktop/paste.py"
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            data = source.read_bytes()
            info = tarfile.TarInfo("paste-fixture.py")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            data = source.with_name("clipboard-owner.py").read_bytes()
            info = tarfile.TarInfo("clipboard-owner.py")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        await asyncio.to_thread(box.put_archive, "/tmp", archive.getvalue())
        await asyncio.to_thread(box.exec_run, ["chown", "sandbox:sandbox", "/workspace"])
        await asyncio.to_thread(
            box.exec_run,
            [
                "env",
                "DISPLAY=:99",
                "XAUTHORITY=/run/a13n-desktop/Xauthority",
                "/usr/bin/python3",
                "/tmp/paste-fixture.py",
            ],
            user="sandbox",
            detach=True,
        )

        async def read():
            result = await asyncio.to_thread(box.exec_run, ["cat", "/workspace/paste-state.json"])
            return json.loads(result.output) if result.exit_code == 0 else {}

        async def command(action):
            code = (
                "from pathlib import Path;Path('/workspace/paste-command.json').write_text("
                + repr(json.dumps({"action": action, "id": uuid4().hex}))
                + ")"
            )
            result = await asyncio.to_thread(box.exec_run, ["python3", "-c", code], user="sandbox")
            assert result.exit_code == 0

        await eventually(read, lambda value: value.get("ready"))
        handle = Handle.model_validate((await stored(service, env_id)).handle)
        connector = DOCKER.execution.execution_connector(
            DOCKER.execution_environment(handle.recipe),
            configuration={"envd_http": {"host": "127.0.0.1"}},
            environment_id=env_id,
            state=handle.state,
        )
        yield env_id, connector, read, command
    finally:
        if box is None and env_id is not None:
            try:
                box = await asyncio.to_thread(engine.containers.get, "a13n-" + env_id)
            except docker.errors.NotFound:
                pass
        for owned in (box, relay):
            if owned is not None:
                await asyncio.to_thread(owned.remove, force=True)
        await asyncio.to_thread(engine.close)


async def test_clipboard_read_write_lifetime_and_replacement(desktop):
    _, connector, read, command = desktop
    async with await connector.open() as bound:
        computer = bound.operations.computer
        assert computer is not None
        for text in ["中文🙂 e\u0301\n\n第二行\n", "空格  和\tTab\n", "中🙂abc\n" * 1024, "CRLF\r\nnext\n", ""]:
            await command("reset")
            await eventually(read, lambda v: v["text"] == "")
            result = await computer.write_clipboard(text)
            assert result.effect == "executed" and result.cleanup_complete
            # The old paste window would have expired; publication must outlive this unrelated call.
            await computer.describe()
            await asyncio.sleep(0.9)
            value = await computer.read_clipboard()
            assert value.status == "text" and value.text == text.replace("\r\n", "\n")
            result = await computer.execute(ComputerPressKeys(keys=("control", "v")))
            assert result.effect == "executed" and result.input_cleanup_complete
            await eventually(read, lambda v, expected=value.text: v["text"] == expected and not v["keys_held"])
        # Real application copy -> Agent read.
        await computer.write_clipboard("copy 中文🙂\n")
        await computer.execute(ComputerPressKeys(keys=("control", "v")))
        await eventually(read, lambda v: v["text"] == "copy 中文🙂\n")
        await computer.execute(ComputerPressKeys(keys=("control", "a")))
        await computer.execute(ComputerPressKeys(keys=("control", "c")))
        await asyncio.sleep(0.1)
        assert (await computer.read_clipboard()).text == "copy 中文🙂\n"
        await command("replace_clipboard")
        await eventually(lambda: computer.read_clipboard(), lambda v: v.text == "replacement-owner")
    # Closing the old publisher must preserve the GTK owner's selection.
    async with await connector.open() as bound:
        computer = bound.operations.computer
        assert computer is not None
        assert (await computer.read_clipboard()).text == "replacement-owner"
        await computer.write_clipboard("session two")
        async with await connector.open() as newer:
            other = newer.operations.computer
            assert other is not None
            await other.write_clipboard("newer session")
            assert (await computer.read_clipboard()).text == "newer session"
        await eventually(read, lambda v: v["owner"] == 0)


async def test_clipboard_human_control_and_agent_resume(service, scripted_model, runs_kit, desktop):
    env_id, _, read, _ = desktop
    agent = await runs_kit.create_agent(service, scripted_model, toolsets={"computer": {"enabled": True}})
    started = await runs_kit.start_thread(
        service,
        agent,
        "Paste then hand control to a human",
        environments=[
            {
                "name": "workspace",
                "environment_id": env_id,
                "permission_ceiling": EnvironmentPermissionSet(operations=COMPUTER_ACTIONS).model_dump(mode="json"),
            }
        ],
    )
    scripted_model.call("computer_observe", {}, call_id="observe")
    task = await runs_kit.attempt(service)
    await scripted_model.request()
    assert returned(await scripted_model.request(), "observe")["ok"]
    text = "Agent 中文\n第二行\n"
    write_gate = asyncio.Event()
    scripted_model.call("computer_write_clipboard", {"text": text}, call_id="write", gate=write_gate)
    view = await open_desktop(service, started["run"])
    write_gate.set()
    assert returned(await scripted_model.request(), "write")["effect"] == "executed"
    scripted_model.call("computer_press_keys", {"keys": ["control", "v"]}, call_id="paste")
    assert returned(await scripted_model.request(), "paste")["effect"] == "executed"
    # Hold an actual model response while remote control requests cross the network.
    # An empty script queue expires after ten seconds and can steal a later resume turn.
    pause_gate = asyncio.Event()
    scripted_model.call("computer_observe", {}, call_id="pause-boundary", gate=pause_gate)
    await eventually(read, lambda v: v["text"] == text)
    requested = await post(service, view, "request-control", {})
    assert requested.status_code == 200, requested.text
    assert (await post(service, view, "control", control(view))).status_code == 409
    # Let the in-flight model response reach the requested safe pause boundary.
    pause_gate.set()
    await asyncio.wait_for(task, 20)
    run = await runs_kit.get_run(service, started["run"]["id"])
    assert run["status"] == "waiting", run
    await eventually(read, lambda v, expected=text: v["text"] == expected and not v["keys_held"])
    view = (await service.client.get(f"{service.api}/runs/{run['id']}/desktop-control")).json()
    claimed = await post(service, view, "control", control(view))
    assert claimed.status_code == 200, claimed.text
    grant = claimed.json()
    view = grant["assistance"]
    viewer = grant
    rfb = await asyncio.to_thread(RFB, viewer["websocket_url"], viewer["password"])
    try:
        for key in "human":
            await asyncio.to_thread(rfb.send, struct.pack("!BBHI", 4, 1, 0, ord(key)))
            await asyncio.to_thread(rfb.send, struct.pack("!BBHI", 4, 0, 0, ord(key)))
        await eventually(read, lambda v: v["text"] == text + "human")
        completed = await post(service, view, "complete", control(view))
        assert completed.status_code == 200, completed.text
        assert await asyncio.to_thread(rfb.closed)
    finally:
        await asyncio.to_thread(rfb.close)
    resumed = await service.client.post(
        f"{service.api}/runs/{run['id']}/resume", json={"calls": {}, "approvals": {}}, headers=runs_kit.fresh_key()
    )
    assert resumed.status_code == 201, resumed.text
    scripted_model.call("computer_observe", {}, call_id="fresh")
    following = await runs_kit.attempt(service)
    # Drain any source-run request and wait for the fresh observation return.
    async with asyncio.timeout(20):
        while True:
            request = await scripted_model.request()
            if any(m.get("tool_call_id") == "fresh" for m in request["messages"]):
                assert returned(request, "fresh")["ok"]
                break
    scripted_model.call("computer_write_clipboard", {"text": "\nAgent继续完成"}, call_id="write-after")
    assert returned(await scripted_model.request(), "write-after")["effect"] == "executed"
    scripted_model.call("computer_press_keys", {"keys": ["control", "v"]}, call_id="finish")
    assert returned(await scripted_model.request(), "finish")["effect"] == "executed"
    scripted_model.say("Completed after human handback.")
    await following
    assert (await runs_kit.get_run(service, resumed.json()["id"]))["status"] == "completed"
    await eventually(read, lambda v: v["text"] == text + "human\nAgent继续完成")


async def test_bounded_clipboard_reads_cancellation_and_replay(desktop):
    _, connector, read, command = desktop
    async with await connector.open() as bound:
        computer = bound.operations.computer
        assert computer is not None
        client = computer._session.client
        for mode in ["no_text", "invalid", "oversize", "huge_targets", "incr_oversize", "incr_invalid", "incr"]:
            await command("owner:" + mode)
            await eventually(read, lambda v, selected=mode: v["probe"] == selected + ":ready")
            if mode in {"no_text", "incr"}:
                value = await computer.read_clipboard()
                assert (value.status, value.text) == (("no_text", None) if mode == "no_text" else ("text", "中文🙂\n"))
            else:
                from a13n_harness.environment.models import EnvironmentError

                with pytest.raises(EnvironmentError):
                    await computer.read_clipboard()
        await command("owner:stall")
        await eventually(read, lambda v: v["probe"] == "stall:ready")
        call = asyncio.create_task(
            client.computer_read_clipboard(
                eip.ComputerReadClipboardParams(context=eip.EIPCallContext(operation_id="cancel-read"))
            )
        )
        await eventually(read, lambda v: v["probe"] == "stall:requested")
        # A queued write must time out without replacing the clipboard while read holds admission.
        original_owner = (await read())["owner"]
        with pytest.raises(EIPMethodError) as queued:
            await asyncio.wait_for(
                client.computer_write_clipboard(
                    eip.ComputerWriteClipboardParams(
                        context=eip.EIPCallContext(operation_id="queued-write", timeout_ms=50),
                        text="must not publish",
                    )
                ),
                2,
            )
        assert queued.value.error.data.error_type in {eip.ErrorType.CANCELLED, eip.ErrorType.TIMEOUT}
        assert (await read())["owner"] == original_owner
        await client.operation_cancel(
            eip.OperationCancelParams(
                context=eip.EIPCallContext(operation_id="cancel"), target_operation_id="cancel-read"
            )
        )
        with pytest.raises(EIPMethodError) as error:
            await asyncio.wait_for(call, 2)
        assert error.value.error.data.error_type == eip.ErrorType.CANCELLED
        # Absolute acquisition timeout and a small caller byte budget are explicit errors.
        with pytest.raises(EIPMethodError) as error:
            await asyncio.wait_for(
                client.computer_read_clipboard(
                    eip.ComputerReadClipboardParams(context=eip.EIPCallContext(operation_id="timeout-read"))
                ),
                7,
            )
        assert error.value.error.data.error_type == eip.ErrorType.TIMEOUT
        write = eip.ComputerWriteClipboardParams(context=eip.EIPCallContext(operation_id="write-once"), text="first")
        first = await client.computer_write_clipboard(write)
        await computer.write_clipboard("second")
        assert await client.computer_write_clipboard(write) == first
        assert (await computer.read_clipboard()).text == "second"
        with pytest.raises(EIPMethodError):
            await client.computer_read_clipboard(
                eip.ComputerReadClipboardParams(context=eip.EIPCallContext(operation_id="small"), max_bytes=1)
            )
        await computer.write_clipboard("x" * 16384)
        assert len((await computer.read_clipboard()).text) == 16384
        for value in ["x" * 16385, "中" * 6000, "a\0b"]:
            with pytest.raises(EIPMethodError):
                await client.computer_write_clipboard(
                    eip.ComputerWriteClipboardParams(
                        context=eip.EIPCallContext(operation_id="invalid-" + uuid4().hex), text=value
                    )
                )
        assert len((await computer.read_clipboard()).text) == 16384


async def test_session_close_drains_stalled_clipboard_read(desktop):
    _, connector, read, command = desktop
    async with await connector.open() as bound:
        computer = bound.operations.computer
        assert computer is not None
        await computer.write_clipboard("owned before replacement")
        await command("owner:stall")
        await eventually(read, lambda v: v["probe"] == "stall:ready")
        owner = (await read())["owner"]
        call = asyncio.create_task(computer.read_clipboard())
        await eventually(read, lambda v: v["probe"] == "stall:requested")
        await asyncio.wait_for(computer._session.close(), 4)
        from a13n_harness.environment.models import EnvironmentError

        with pytest.raises(EnvironmentError):
            await asyncio.wait_for(call, 1)
        assert (await read())["owner"] == owner
    # Teardown must leave another Session able to publish and use the desktop.
    async with await connector.open() as following:
        computer = following.operations.computer
        assert computer is not None
        await computer.write_clipboard("after close 中文")
        assert (await computer.read_clipboard()).text == "after close 中文"
