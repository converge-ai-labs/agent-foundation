"""Opt-in local Native/Docker tests: Chromium, private profiles and connected RFB input."""

import asyncio
import os
import struct
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import docker
import pytest
from a13n_service.providers.browser.definition import BrowserProviderError
from a13n_service.providers.browser.docker import Configuration, Docker
from a13n_service.providers.browser.selfhost import Recipe, blocking

from .environment_providers.desktop_access_rfb import RFB

pytestmark = pytest.mark.anyio


async def test_docker_profile_input_and_recovery(tmp_path):
    image = os.environ.get("A13N_TEST_BROWSER_DOCKER_IMAGE")
    if not image:
        pytest.skip("Requires A13N_TEST_BROWSER_DOCKER_IMAGE built from this checkout")
    identity = "browser_live_" + uuid4().hex[:16]
    provider = Docker(tmp_path, docker.from_env(), Configuration(image=image))
    live = None
    connection = None
    rfb = None
    try:
        live = await provider.create(identity, Recipe())
        assert live.status == "ready" and live.runtime_id
        connection = await provider.connect(live.state)
        target = (await connection.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
        page = await connection.attach(target)
        await connection.navigate(page, "http://127.0.0.1:9222/json/version")
        await connection.send("Page.bringToFront", page=page)
        await connection.evaluate(
            page, "document.body.innerHTML='<input id=entry autofocus>';document.querySelector('input').focus()"
        )
        cookie_result = await connection.send(
            "Network.setCookie",
            {
                "name": "retained",
                "value": identity,
                "url": "http://127.0.0.1:9222",
                "expires": (datetime.now(UTC) + timedelta(days=1)).timestamp(),
            },
            page,
        )
        assert cookie_result["success"]
        assert any(
            cookie["name"] == "retained" and cookie["value"] == identity
            for cookie in (await connection.send("Storage.getCookies"))["cookies"]
        )
        storage = {"securityOrigin": "http://127.0.0.1:9222", "isLocalStorage": True}
        await connection.send(
            "DOMStorage.setDOMStorageItem", {"storageId": storage, "key": "retained", "value": identity}, page
        )

        grant = await provider.ensure_viewer(
            live.state, "browser_live_input", episode=1, expires_at=datetime.now(UTC) + timedelta(seconds=8)
        )
        rfb = await blocking(RFB, grant.url, grant.state["password"])
        assert (await blocking(rfb.frame))["pixel_bytes"] > 0
        await blocking(rfb.send, struct.pack("!BBHI", 4, 1, 0, ord("a")))
        await blocking(rfb.send, struct.pack("!BBHI", 4, 0, 0, ord("a")))
        async with asyncio.timeout(5):
            while await connection.evaluate(page, "document.querySelector('input').value") != "a":
                await asyncio.sleep(0.1)
        # No management calls are made until the input process has expired independently.
        await provider.close()
        await asyncio.sleep(max(0, (grant.expires_at - datetime.now(UTC)).total_seconds()) + 0.5)
        assert await blocking(rfb.closed)
        await blocking(rfb.close)
        rfb = None
        provider = Docker(tmp_path, docker.from_env(), Configuration(image=image))
        recovered = await provider.inspect(identity, live.state)
        assert recovered is not None and recovered.runtime_id == live.runtime_id
        assert any(
            cookie["name"] == "retained" and cookie["value"] == identity
            for cookie in (await connection.send("Storage.getCookies"))["cookies"]
        )
        await connection.close()
        connection = None
        stopped = await provider.stop(identity, live.state)
        assert stopped.status == "stopped"
        resumed = await provider.start(identity, stopped.state, generation=2)
        assert resumed.runtime_id != live.runtime_id
        assert resumed.state["container_id"] == live.state["container_id"]
        connection = await provider.connect(resumed.state)
        target = (await connection.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
        page = await connection.attach(target)
        await connection.navigate(page, "http://127.0.0.1:9222/json/version")
        cookies = (await connection.send("Storage.getCookies"))["cookies"]
        assert any(cookie["name"] == "retained" and cookie["value"] == identity for cookie in cookies)
        entries = (await connection.send("DOMStorage.getDOMStorageItems", {"storageId": storage}, page))["entries"]
        assert ["retained", identity] in entries
        uploaded = await provider.upload_file(resumed.state, "fixture.txt", b"browser upload content")
        await connection.evaluate(page, "document.body.innerHTML='<input id=upload type=file>'")
        document = await connection.send("DOM.getDocument", page=page)
        node = await connection.send(
            "DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": "#upload"}, page
        )
        await connection.send("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [uploaded]}, page)
        assert (
            await connection.evaluate(page, "document.querySelector('input').files[0].text()")
            == "browser upload content"
        )
        await connection.evaluate(
            page,
            "const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['browser download content']));a.download='fixture.txt';a.click()",
        )
        async with asyncio.timeout(10):
            while not any(item["state"] == "completed" for item in connection.download_records.values()):
                await asyncio.sleep(0.1)
        guid = next(key for key, item in connection.download_records.items() if item["state"] == "completed")
        assert await provider.download_file(resumed.state, guid, max_bytes=1024) == b"browser download content"
        with pytest.raises(BrowserProviderError):
            await provider.download_file(resumed.state, guid, max_bytes=2)
        with pytest.raises(BrowserProviderError):
            await provider.download_file(resumed.state, "../profile/Local State", max_bytes=1024)
        with pytest.raises(BrowserProviderError):
            await provider.ensure_viewer(
                live.state, "browser_late_input", episode=2, expires_at=datetime.now(UTC) + timedelta(seconds=60)
            )
        await provider.delete(identity, resumed.state)
        assert await provider.inspect(identity, resumed.state) is None
        with pytest.raises(BrowserProviderError):
            await provider.start(identity, resumed.state, generation=3)
    except BaseException:
        async with provider.allocation(identity) as allocation:
            container = await provider.container(allocation)
            if container is not None:
                (tmp_path / "container.log").write_bytes(await blocking(container.logs))
                print((tmp_path / "container.log").read_text()[-12000:])
        raise
    finally:
        if rfb is not None:
            await blocking(rfb.close)
        if connection is not None:
            await connection.close()
        await provider.delete(identity, live.state if live else None)
        await provider.close()


@pytest.mark.parametrize("child", ["openbox", "desktop_guest.py"])
async def test_docker_critical_child_exit_ends_runtime(tmp_path, child):
    image = os.environ.get("A13N_TEST_BROWSER_DOCKER_IMAGE")
    if not image:
        pytest.skip("Requires A13N_TEST_BROWSER_DOCKER_IMAGE built from this checkout")
    identity = "browser_live_" + uuid4().hex[:16]
    provider = Docker(tmp_path, docker.from_env(), Configuration(image=image))
    try:
        live = await provider.create(identity, Recipe())
        async with provider.allocation(identity) as allocation:
            container = await provider.container(allocation)
        assert container is not None
        # Select a direct supervisor child, not this diagnostic exec or a descendant.
        result = await blocking(
            container.exec_run,
            [
                "python3",
                "-c",
                "import json,os,pathlib,signal,sys; "
                "parent=json.loads(pathlib.Path('/home/browser/data/runtime.json').read_text())['pid']; "
                "matches=[int(p.name) for p in pathlib.Path('/proc').glob('[0-9]*') "
                "if (p/'stat').exists() and int((p/'stat').read_text().rsplit(')',1)[1].split()[1])==parent "
                "and sys.argv[1] in (p/'cmdline').read_bytes().decode()]; "
                "assert len(matches)==1,matches; os.kill(matches[0],signal.SIGTERM)",
                child,
            ],
        )
        assert result.exit_code == 0, result.output
        async with asyncio.timeout(20):
            while True:
                await blocking(container.reload)
                if container.status == "exited":
                    break
                await asyncio.sleep(0.1)
        assert b"A required Browser runtime process exited" in await blocking(container.logs)
        stopped = await provider.inspect(identity, live.state)
        assert stopped is not None and stopped.status == "stopped"
        resumed = await provider.start(identity, stopped.state, generation=2)
        assert resumed.runtime_id != live.runtime_id
        assert resumed.state["container_id"] == live.state["container_id"]
    finally:
        await provider.delete(identity, None)
        await provider.close()


@pytest.mark.parametrize("kind", ["docker", "native"])
async def test_selfhost_service_asset_transfer(service, runs_kit, scripted_model, tmp_path, monkeypatch, kind):
    """Real Service admission/ownership/assets and both built-ins; no outside page network."""
    import json
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_service.distribution import OSS
    from a13n_service.providers.browser.builtins import builtins
    from a13n_service.providers.browser.native import Native
    from a13n_service.providers.registry import Registry
    from a13n_service.resources.assets.service import read_asset_content
    from a13n_service.runs.browsers import execution, files
    from a13n_service.settings import Browsers
    from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal

    from .test_browser_files import asset
    from .test_browsers import bound_run

    image = os.environ.get("A13N_TEST_BROWSER_DOCKER_IMAGE")
    chromium = os.environ.get("A13N_TEST_BROWSER_NATIVE_CHROMIUM")
    if not (image if kind == "docker" else chromium):
        pytest.skip(f"Requires configured live {kind} Browser")
    settings = Browsers(root=tmp_path, native_enabled=True, native_chromium=chromium or "chromium")
    await runs_kit.pause_sweeps(service)
    registry = Registry.of((*OSS.providers, *builtins(settings)))
    service.runtime = replace(service.runtime, registry=registry)
    service.app.state.runtime = service.runtime
    response = await service.client.post(
        f"{service.api}/browser-providers",
        json={"type": kind, "name": "Live Browser", "config": {"image": image} if kind == "docker" else {}},
    )
    assert response.status_code == 201, response.text
    response = await service.client.post(
        f"{service.api}/browsers", json={"provider_id": response.json()["id"], "name": "Live files"}
    )
    assert response.status_code == 201, response.text
    browser = response.json()
    invocation = SimpleNamespace(invocation=SimpleNamespace(tool_call_id="live-files", tool_name="browser_files"))
    monkeypatch.setattr(files, "current_invocation_scope", lambda: invocation)
    monkeypatch.setattr(execution, "current_invocation_scope", lambda: invocation)
    actor = Principal(
        service.tenant.principal_id, "user", (Grant(service.tenant.organization_id, None, BUILT_IN_ROLES["builder"]),)
    )
    try:
        lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
        input_asset = await asset(service, actor)
        async with execution.open_backend(
            service.runtime,
            lease,
            binding,
            SimpleNamespace(tool_call=AsyncMock()),
            frozenset({"tabs", "observe", "files"}),
        ) as backend:
            await backend.tabs("new")
            native = backend.native
            await native.browser.evaluate(
                native.page, "document.body.innerHTML='<label>Upload<input type=file></label>'"
            )
            observation = await backend.observe()
            target = next(item.target for item in observation.elements if "Upload" in item.description)
            await backend.upload(target, (f"asset:{input_asset.id}",), revision=observation.revision)
            assert (
                await native.browser.evaluate(native.page, "document.querySelector('input').files[0].text()")
                == "upload bytes"
            )
            await native.browser.evaluate(
                native.page,
                "const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['download bytes']));a.download='output.txt';a.click()",
            )
            async with asyncio.timeout(10):
                while not any(item["state"] == "completed" for item in native.browser.download_records.values()):
                    await asyncio.sleep(0.1)
            first = json.loads(await backend.downloads())
            second = json.loads(await backend.downloads())
            assert first == second
            (record,) = first["downloads"]
            assert "path" not in record
            _, content = await read_asset_content(
                service.runtime.storage, service.runtime.objects, actor, lease.workspace_id, record["asset_id"]
            )
            assert content == b"download bytes"

        from urllib.parse import urlsplit

        from .http_support import serve_http
        from .test_browsers import command

        await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
        response = await service.client.post(f"{service.api}/browsers/{browser['id']}/viewer")
        assert response.status_code == 200, response.text
        grant = response.json()
        assert grant["protocol"] == "vnc" and grant["password"] and grant["token"]
        async with serve_http(service.app, lifespan="off") as base:
            url = base.replace("http:", "ws:") + urlsplit(grant["url"]).path
            rfb = await blocking(RFB, url, grant["password"], subprotocols=["binary", "a13n.browser." + grant["token"]])
            try:
                assert (await blocking(rfb.frame))["pixel_bytes"] > 0
                # Revocation must terminate an already established input connection through the relay.
                await command(service, runs_kit, browser["id"], "handback")
                assert await blocking(rfb.closed)
            finally:
                await blocking(rfb.close)
    finally:
        cleanup = (
            Docker(tmp_path / "docker", docker.from_env(), Configuration(image=image))
            if kind == "docker"
            else Native(tmp_path / "native", chromium)
        )
        try:
            await cleanup.delete(browser["id"], None)
        finally:
            await cleanup.close()


async def test_docker_scripted_model_browser_tools(service, runs_kit, scripted_model, tmp_path):
    """Real model protocol -> Worker/Harness tools -> Docker/CDP, with frozen public Run binding."""
    from dataclasses import replace

    from a13n_service.distribution import OSS
    from a13n_service.infra.db import short_session
    from a13n_service.providers.browser.builtins import builtins
    from a13n_service.providers.registry import Registry
    from a13n_service.runs.attempts import AttemptControl
    from a13n_service.runs.browsers.tables import BrowserTabRow
    from a13n_service.runs.execute import execute
    from a13n_service.settings import Browsers
    from sqlalchemy import select

    from .test_browsers import bound_run

    image = os.environ.get("A13N_TEST_BROWSER_DOCKER_IMAGE")
    if not image:
        pytest.skip("Requires A13N_TEST_BROWSER_DOCKER_IMAGE built from this checkout")
    await runs_kit.pause_sweeps(service)
    registry = Registry.of((*OSS.providers, *builtins(Browsers(root=tmp_path))))
    service.runtime = replace(service.runtime, registry=registry)
    service.app.state.runtime = service.runtime
    response = await service.client.post(
        f"{service.api}/browser-providers", json={"type": "docker", "name": "Model Docker", "config": {"image": image}}
    )
    assert response.status_code == 201, response.text
    response = await service.client.post(
        f"{service.api}/browsers", json={"provider_id": response.json()["id"], "name": "Model Browser"}
    )
    assert response.status_code == 201, response.text
    browser = response.json()
    provider = Docker(tmp_path / "docker", docker.from_env(), Configuration(image=image))
    try:
        lease, binding = await bound_run(
            service,
            runs_kit,
            scripted_model,
            browser,
            toolsets={"browser": {"enabled": True, "tools": {"screenshot": {"enabled": False}}}},
        )
        accepted = await runs_kit.get_run(service, lease.run_id)
        assert accepted["browser_binding"] == binding.model_dump(mode="json")
        # Desired changes cannot rewrite an accepted Run's serialized binding.
        thread = await runs_kit.get_thread(service, lease.thread_id)
        response = await service.client.put(
            f"{service.api}/threads/{lease.thread_id}/browser", json=None, headers=runs_kit.if_match(thread)
        )
        assert response.status_code == 200, response.text
        scripted_model.call(
            "browser_tabs", {"action": "new", "url": "data:text/html,<label>Entry<input></label>"}, call_id="call_new"
        )
        scripted_model.call("browser_observe", {}, call_id="call_observe")
        scripted_model.call(
            "browser_act",
            {
                "action": {
                    "kind": "fill",
                    "target": {"kind": "locator", "role": "textbox", "name": "Entry"},
                    "text": "model-to-browser",
                }
            },
            call_id="call_fill",
        )
        scripted_model.call("browser_read", {"kind": "forms"}, call_id="call_read")
        scripted_model.say("Done")
        await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 90))
        run = await runs_kit.get_run(service, lease.run_id)
        assert run["status"] == "completed", run.get("failure")
        assert run["browser_binding"] == accepted["browser_binding"]
        async with short_session(service.runtime.storage) as session:
            target = await session.scalar(
                select(BrowserTabRow.target_id).where(BrowserTabRow.browser_id == browser["id"])
            )
        assert target
        live = await provider.inspect(browser["id"], None)
        assert live and live.status == "ready"
        connection = await provider.connect(live.state)
        try:
            page = await connection.attach(target)
            assert await connection.evaluate(page, "document.querySelector('input').value") == "model-to-browser"
        finally:
            await connection.close()
    finally:
        await provider.delete(browser["id"], None)
        await provider.close()
