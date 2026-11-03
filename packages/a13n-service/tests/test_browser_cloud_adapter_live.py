"""Opt-in Service/Harness cloud adapter test; consumes one supplier session per run."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_service.distribution import OSS
from a13n_service.providers.browser import cloud
from a13n_service.providers.browser.builtins import builtins
from a13n_service.providers.registry import Registry
from a13n_service.runs.browsers import execution, lifecycle
from a13n_service.settings import Browsers

from .test_browsers import bound_run, command

pytestmark = pytest.mark.anyio


async def test_cloud_service_harness_adapter(service, runs_kit, scripted_model, monkeypatch, cloud_browser_credentials):
    kind, secret = cloud_browser_credentials
    key = secret.get_secret_value()
    base = "https://api.browserbase.com" if kind == "browserbase" else "https://api.onkernel.com"
    headers = {"X-BB-API-Key": key} if kind == "browserbase" else {"Authorization": f"Bearer {key}"}
    async with httpx2.AsyncClient(base_url=base, headers=headers, timeout=30, follow_redirects=False) as api:
        if kind == "browserbase":
            result = await api.get("/v1/sessions")
            assert result.status_code == 200
            projects = {item["projectId"] for item in result.json() if item.get("projectId")}
            assert len(projects) == 1, "set up an account with one discoverable project for this test"
            config = {"project_id": projects.pop()}
            recipe = {"timeout": 180}
        else:
            result = await api.get("/auth/context")
            assert result.status_code == 200
            context = result.json()
            config = {
                "organization_id": context["organization"]["id"],
                "project_id": context["authorization"]["effective_scope"]["project_id"],
            }
            recipe = {"timeout_seconds": 180}

        # Record the exact create response ID before any later inspect/connect can fail.
        # This is test cleanup instrumentation, not production recovery from lost responses.
        captured = {}
        request = cloud.CloudAPI.request

        async def observe_request(self, method, path, **kwargs):
            is_create = method == "POST" and path == ("/v1/sessions" if kind == "browserbase" else "/browsers")
            if is_create:
                captured["dispatched"] = True
            response = await request(self, method, path, **kwargs)
            if is_create:
                data = response.json()
                captured["id"] = data["id" if kind == "browserbase" else "session_id"]
            return response

        monkeypatch.setattr(cloud.CloudAPI, "request", observe_request)
        await runs_kit.pause_sweeps(service)
        service.runtime = replace(
            service.runtime, registry=Registry.of((*OSS.providers, *builtins(Browsers(root=None))))
        )
        service.app.state.runtime = service.runtime
        response = await service.client.post(
            f"{service.api}/browser-providers",
            json={"type": kind, "name": "Cloud live", "config": config, "credential": {"api_key": key}},
        )
        assert response.status_code == 201
        response = await service.client.post(
            f"{service.api}/browsers",
            json={"provider_id": response.json()["id"], "name": "Cloud adapter live", "recipe": recipe},
        )
        assert response.status_code == 201
        browser = response.json()
        invocation = SimpleNamespace(invocation=SimpleNamespace(tool_call_id="cloud-live", tool_name="browser_tabs"))
        monkeypatch.setattr(execution, "current_invocation_scope", lambda: invocation)
        try:
            lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
            async with execution.open_backend(
                service.runtime, lease, binding, SimpleNamespace(tool_call=AsyncMock()), frozenset({"tabs", "observe"})
            ) as backend:
                await backend.tabs("new")
                observation = await backend.observe()
                assert observation.revision
            await lifecycle.reconcile(service.runtime, browser["workspace_id"], browser["id"], inspect_ready=True)
            current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
            assert current["status"] == "ready", current.get("failure")
            async with execution.open_backend(
                service.runtime, lease, binding, SimpleNamespace(tool_call=AsyncMock()), frozenset({"tabs", "observe"})
            ) as backend:
                await backend.observe()
            from a13n_service.runs.attempts import AttemptControl
            from a13n_service.runs.execute import execute

            scripted_model.say("Done")
            await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
            assert (await runs_kit.get_run(service, lease.run_id))["status"] == "completed"
            await command(service, runs_kit, browser["id"], "release")
            await lifecycle.reconcile(service.runtime, browser["workspace_id"], browser["id"])
            current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
            assert current["status"] == "deleted", current.get("failure")
        finally:
            # Verify exact supplier terminal state, falling back to exact-ID cleanup on failures.
            session_id = captured.get("id")
            if session_id:
                path = ("/v1/sessions/" if kind == "browserbase" else "/browsers/") + session_id
                for _ in range(20):
                    result = await api.get(path, params={"include_deleted": "true"} if kind == "kernel" else None)
                    assert result.status_code == 200, "exact supplier cleanup inspection failed"
                    data = result.json()
                    terminal = (
                        bool(data.get("deleted_at"))
                        if kind == "kernel"
                        else data.get("status") in {"ERROR", "TIMED_OUT", "COMPLETED"}
                    )
                    if terminal:
                        break
                    if kind == "kernel":
                        await api.delete(path)
                    else:
                        await api.post(path, json={"projectId": config["project_id"], "status": "REQUEST_RELEASE"})
                    await asyncio.sleep(1)
                assert terminal, "exact supplier cleanup remains unresolved"
            else:
                assert not captured.get("dispatched"), "supplier create outcome unknown; manual investigation required"
