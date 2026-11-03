"""Opt-in Service/Harness cloud adapter test; consumes one supplier session per run."""

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
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
    base = {
        "browserbase": "https://api.browserbase.com",
        "kernel": "https://api.onkernel.com",
        "browser_use": "https://api.browser-use.com",
    }[kind]
    headers = {
        "browserbase": {"X-BB-API-Key": key},
        "kernel": {"Authorization": f"Bearer {key}"},
        "browser_use": {"X-Browser-Use-API-Key": key},
    }[kind]
    async with httpx2.AsyncClient(base_url=base, headers=headers, timeout=30, follow_redirects=False) as api:
        if kind == "browserbase":
            result = await api.get("/v1/sessions")
            assert result.status_code == 200
            projects = {item["projectId"] for item in result.json() if item.get("projectId")}
            assert len(projects) == 1, "set up an account with one discoverable project for this test"
            config = {"project_id": projects.pop()}
            recipe = {"timeout": 180}
        elif kind == "browser_use":
            result = await api.get("/api/v2/billing/account")
            assert result.status_code == 200
            config = {"project_id": result.json()["projectId"]}
            recipe = {"timeout_minutes": 1}
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
            is_create = (
                method == "POST"
                and path
                == {"browserbase": "/v1/sessions", "kernel": "/browsers", "browser_use": "/api/v4/browsers"}[kind]
            )
            if is_create:
                if kind == "browser_use":
                    allocation = kwargs["body"]["metadata"]["a13n_allocation"]
                    # Persist before the request so an interrupted process can be reconciled.
                    evidence = Path(os.environ["A13N_CLOUD_BROWSER_EVIDENCE"])
                    fd = os.open(evidence, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w") as output:
                        output.write(allocation)
                        output.flush()
                        os.fsync(output.fileno())
                    captured["allocation"] = allocation
                captured["dispatched"] = True
            response = await request(self, method, path, **kwargs)
            if is_create:
                data = response.json()
                captured["id"] = data["session_id" if kind == "kernel" else "id"]
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
        stage = "bind"
        try:
            lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
            stage = "connect_and_tools"
            async with execution.open_backend(
                service.runtime,
                lease,
                binding,
                SimpleNamespace(tool_call=AsyncMock()),
                frozenset({"tabs", "observe", "navigate", "read", "act"}),
            ) as backend:
                await backend.tabs("new")
                await backend.observe()
                navigation = json.loads(await backend.navigate("https://example.com"))
                observation = await backend.observe()
                assert observation.revision
                content = json.loads(await backend.read("text"))
                assert navigation.get("verification") == "passed", (
                    f"navigation verification={navigation.get('verification')!r}, "
                    f"dispatch={navigation.get('dispatch')!r}, load_state={navigation.get('load_state')!r}"
                )
                assert navigation.get("title") == "Example Domain"
                assert navigation.get("url") == "https://example.com/"
                assert content.get("url") == "https://example.com/"
                assert content.get("kind") == "text"
                assert any("documentation examples" in item.get("text", "") for item in content["items"])
                from a13n_harness.capabilities.browser.actions import Press

                interaction = json.loads(
                    await backend.interact(Press(kind="press", key="Tab"), revision=observation.revision)
                )
                assert interaction.get("dispatch") == "dispatched"
                assert "error" not in interaction
            stage = "inspect"
            await lifecycle.reconcile(service.runtime, browser["workspace_id"], browser["id"], inspect_ready=True)
            current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
            assert current["status"] == "ready", current.get("failure")
            async with execution.open_backend(
                service.runtime,
                lease,
                binding,
                SimpleNamespace(tool_call=AsyncMock()),
                frozenset({"tabs", "observe", "navigate", "read", "act"}),
            ) as backend:
                await backend.observe()
            from a13n_service.runs.attempts import AttemptControl
            from a13n_service.runs.execute import execute

            stage = "harness_run"
            scripted_model.say("Done")
            await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
            assert (await runs_kit.get_run(service, lease.run_id))["status"] == "completed"
            stage = "release"
            await command(service, runs_kit, browser["id"], "release")
            await lifecycle.reconcile(service.runtime, browser["workspace_id"], browser["id"])
            current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
            assert current["status"] == "deleted", current.get("failure")
        except Exception as exc:
            exc.add_note(f"live test stage: {stage}")
            raise
        finally:
            # Verify exact supplier terminal state, falling back to exact-ID cleanup on failures.
            primary_error = sys.exception()
            cleanup_error = None
            session_id = captured.get("id")
            if not session_id and kind == "browser_use" and captured.get("allocation"):
                try:
                    finder = cloud.BrowserUse(
                        cloud.BrowserUseConfiguration(**config),
                        SimpleNamespace(
                            request=lambda method, path, **kwargs: api.request(
                                method, path, params=kwargs.get("params")
                            ),
                            close=AsyncMock(),
                        ),
                    )
                    try:
                        item = await finder._find(captured["allocation"])
                        if item is not None:
                            session_id = item["id"]
                    finally:
                        await finder.close()
                except Exception as exc:
                    cleanup_error = f"metadata lookup failed: {type(exc).__name__}"
            try:
                if session_id:
                    path = {"browserbase": "/v1/sessions/", "kernel": "/browsers/", "browser_use": "/api/v4/browsers/"}[
                        kind
                    ] + session_id
                    for _ in range(20):
                        result = await api.get(path, params={"include_deleted": "true"} if kind == "kernel" else None)
                        assert result.status_code == 200, "exact supplier cleanup inspection failed"
                        data = result.json()
                        if kind == "browser_use":
                            assert data.get("metadata", {}).get("a13n_allocation") == captured.get("allocation"), (
                                "cleanup identity mismatch"
                            )
                        terminal = (
                            bool(data.get("deleted_at"))
                            if kind == "kernel"
                            else data.get("status") == "stopped"
                            if kind == "browser_use"
                            else data.get("status") in {"ERROR", "TIMED_OUT", "COMPLETED"}
                        )
                        if terminal:
                            break
                        if kind == "kernel":
                            await api.delete(path)
                        elif kind == "browser_use":
                            await api.patch(path, json={"action": "stop"})
                        else:
                            await api.post(path, json={"projectId": config["project_id"], "status": "REQUEST_RELEASE"})
                        await asyncio.sleep(1)
                    assert terminal, "exact supplier cleanup remains unresolved"
                elif captured.get("dispatched"):
                    cleanup_error = cleanup_error or "supplier create outcome unresolved; manual investigation required"
            except Exception as exc:
                cleanup_error = f"supplier cleanup failed: {type(exc).__name__}"
            if cleanup_error:
                if primary_error is not None:
                    primary_error.add_note(cleanup_error)
                else:
                    pytest.fail(cleanup_error, pytrace=False)
