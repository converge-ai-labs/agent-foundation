from __future__ import annotations

import argparse
import asyncio
import importlib.util
import ipaddress
from pathlib import Path

import httpx2
import pytest


@pytest.fixture
def smoke(monkeypatch):
    directory = Path(__file__).parents[2] / "provider-smoke"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("openconnector_smoke", directory / "openconnector.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("a13n_service.endpoint_policy._resolve_addresses", lambda *_: [ipaddress.ip_address("8.8.8.8")])
    args = argparse.Namespace(
        provider="openconnector",
        command="walkthrough",
        connector="github",
        tool="github.get_current_user",
        endpoint="https://connector.oomol.com",
        deployment="cloud",
        allow_private_domain=[],
        connection_id=None,
        arguments="{}",
        execute=False,
    )
    calls = []
    state = {"connected": False, "http_status": 200}

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer dummy-key"
        assert "x-api-key" not in request.headers
        if state["http_status"] != 200:
            return httpx2.Response(state["http_status"], json={"message": "dummy-key secret-body"})
        action = {
            "id": "github.get_current_user",
            "service": "github",
            "description": "Current user",
            "inputSchema": {"type": "object"},
            "outputSchema": {"allOf": [{"type": "object"}]},
        }
        if request.url.path == "/v1/providers":
            data = [{"service": "github", "displayName": "GitHub", "authTypes": ["oauth2"]}]
        elif request.url.path == "/v1/actions":
            data = [action]
        elif request.url.path == "/v1/apps":
            data = (
                [
                    {
                        "id": "account-1",
                        "service": "github",
                        "alias": "work",
                        "isDefault": False,
                        "status": "active",
                        "credentialSummary": "secret-account-data",
                    }
                ]
                if state["connected"]
                else []
            )
        elif request.method == "GET":
            data = action
        else:
            data = {"login": "demo"}
        return httpx2.Response(200, json={"success": True, "data": data})

    def run():
        async def execute():
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
                return await module.run(args, "dummy-key", client)

        return asyncio.run(execute())

    return module, args, calls, state, handler, run


@pytest.mark.parametrize("command", ["discover", "tools", "describe"])
def test_previews_do_not_inspect_or_authorize_accounts(smoke, monkeypatch, capsys, command):
    _, args, calls, _, _, run = smoke
    args.command = command
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected authorization prompt"))
    assert run() == 0
    assert all(call.method == "GET" and call.url.path != "/v1/apps" for call in calls)
    assert "dummy-key" not in capsys.readouterr().out


def test_walkthrough_previews_then_waits_for_console_then_confirms_call(smoke, monkeypatch, capsys):
    _, _, calls, state, _, run = smoke

    def answer(prompt):
        if "After completing authorization" in prompt:
            assert any(call.url.path == "/v1/actions" for call in calls)
            assert all(call.method == "GET" for call in calls)
            state["connected"] = True
            return ""
        if "Exact connection ID" in prompt:
            return "account-1"
        if "Type CALL" in prompt:
            assert all(call.method == "GET" for call in calls)
            return "CALL"
        raise AssertionError(prompt)

    monkeypatch.setattr("builtins.input", answer)
    assert run() == 0
    assert len([call for call in calls if call.method == "POST"]) == 1
    output = capsys.readouterr().out
    assert "secret-account-data" not in output
    assert "dummy-key" not in output


def test_declining_call_never_dispatches(smoke, monkeypatch):
    _, args, calls, state, _, run = smoke
    state["connected"] = True
    args.connection_id = "account-1"
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert run() == 0
    assert all(call.method == "GET" for call in calls)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_bearer_failures_are_reported_without_secrets(smoke, monkeypatch, capsys, status):
    module, args, calls, state, handler, _ = smoke
    state["http_status"] = status
    args.command = "discover"
    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setenv("OPENCONNECTOR_API_KEY", "dummy-key")
    client_type = httpx2.AsyncClient
    monkeypatch.setattr(
        module.httpx2, "AsyncClient", lambda **kwargs: client_type(transport=httpx2.MockTransport(handler), **kwargs)
    )
    assert module.main() == 1
    assert len(calls) == 1
    output = capsys.readouterr()
    assert f'"http_status": {status}' in output.out
    assert "/v1/providers" in output.out
    assert "dummy-key" not in output.out + output.err
    assert "secret-body" not in output.out + output.err
