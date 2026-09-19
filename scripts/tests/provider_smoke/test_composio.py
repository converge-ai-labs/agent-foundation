from __future__ import annotations

import argparse
import asyncio
import importlib.util
import ipaddress
import json
from pathlib import Path

import httpx2
import pytest
from a13n_harness.providers.connector.contracts import ConnectorProviderError


@pytest.fixture
def smoke(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "provider-smoke"))
    spec = importlib.util.spec_from_file_location(
        "composio_smoke", Path(__file__).parents[2] / "provider-smoke" / "composio.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        "a13n_harness.providers.endpoint_policy._resolve_addresses", lambda *_: [ipaddress.ip_address("8.8.8.8")]
    )
    args = argparse.Namespace(
        provider="composio",
        command="walkthrough",
        connector="github",
        connection_id=None,
        user_id="user-1",
        tool="GITHUB_LOOKUP",
        arguments='{"query":"hello"}',
        execute=False,
        auth_config_id="auth-1",
        callback_url="https://callback.example/complete",
    )
    calls = []
    state = {"owner": "user-1", "changed": False, "unknown": False, "inspected": False}

    def handler(req):
        calls.append(req)
        assert req.headers["x-api-key"] == "dummy-key"
        path = req.url.path
        version = "20260904_01" if state["changed"] and state["inspected"] else "20260903_01"
        account = {"id": "account-1", "toolkit": {"slug": "github"}, "user_id": state["owner"], "status": "ACTIVE"}
        if path.endswith("/toolkits"):
            item = {"slug": "github", "name": "GitHub"}
            item.update({"meta": {"version": version}})
            value = {"items": [item]}
        elif path.endswith("/auth_configs"):
            item = {"id": "auth-1"}
            item.update({"toolkit": {"slug": "github"}, "status": "ENABLED", "auth_scheme": "OAUTH2"})
            value = {"items": [item], "total": 1}
        elif path.endswith("/toolkits/github"):
            value = {
                "slug": "github",
                "name": "GitHub",
                "meta": {"version": version},
                "auth_config_details": [{"mode": "OAUTH2", "fields": {"connected_account_initiation": {}}}],
            }
        elif path.endswith("/tools"):
            value = {"items": [{"slug": "GITHUB_LOOKUP", "version": version}]}
            assert req.url.params["toolkit_versions[github]"] == version
        elif path.endswith("/tools/GITHUB_LOOKUP"):
            schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
            value = {"slug": "GITHUB_LOOKUP", "version": version, "description": "Look up data"}
            value.update({"toolkit": {"slug": "github"}, "input_parameters": schema})
            assert req.url.params["version"] == version
        elif path.endswith("/link"):
            body = json.loads(req.content)
            assert body["auth_config_id"] == "auth-1"
            assert body["user_id"] == "user-1"
            assert body["callback_url"] == args.callback_url
            assert "idempotency-key" not in req.headers
            value = {
                "connected_account_id": "account-1",
                "redirect_url": "https://connect.composio.dev/link/test",
                "expires_at": "2026-09-09T18:00:00+00:00",
            }
        elif path.endswith("/complete_auth"):
            assert json.loads(req.content) == {"session_uri": "session-1", "user_id": "user-1"}
            value = {"connected_account_id": "account-1", "toolkit_slug": "github"}
        elif path.endswith("/account-1"):
            state["inspected"] = True
            value = account
        elif "/execute" in path:
            body = json.loads(req.content)
            assert body["arguments"] == {"query": "hello"}
            assert body["connected_account_id"] == "account-1"
            assert body["user_id"] == "user-1"
            assert body["version"] == version
            assert req.headers["idempotency-key"].startswith("smoke-")
            if state["unknown"]:
                return httpx2.Response(503, json={"error": "unavailable"})
            value = {"successful": True, "status": 200, "data": {"found": True}}
        else:
            raise AssertionError((req.method, path))
        return httpx2.Response(200, json=value)

    def run():

        async def execute():
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
                return await module.run(args, "dummy-key", client)

        return asyncio.run(execute())

    return (module, args, calls, state, run)


@pytest.mark.parametrize("command", ["tools", "describe"])
def test_preview_needs_no_account_or_authorization(smoke, monkeypatch, capsys, command):
    _, args, calls, _, run = smoke
    args.command = command
    args.user_id = None
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("preview prompted for account or authorization"))
    assert run() == 0
    assert all(call.method == "GET" for call in calls)
    assert all("/tools" in call.url.path or "/toolkits/github" in call.url.path for call in calls)
    output = capsys.readouterr().out
    assert "GITHUB_LOOKUP" in output
    assert "dummy-key" not in output


def test_walkthrough_previews_then_authorizes_then_executes(smoke, monkeypatch, capsys):
    module, _, calls, _, run = smoke
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        if "Existing connection ID" in prompt:
            assert any(call.url.path.endswith("/tools/GITHUB_LOOKUP") for call in calls)
            assert all(call.method == "GET" for call in calls)
            return ""
        if "Type AUTHORIZE" in prompt:
            assert all(call.method == "GET" for call in calls)
            return "AUTHORIZE"
        if "Open the authorization URL" in prompt:
            assert any(call.method == "POST" for call in calls)
            return ""
        if "Type CALL" in prompt:
            assert any(call.url.path.endswith("/account-1") for call in calls)
            return "CALL"
        raise AssertionError(prompt)

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(module, "getpass", lambda _: "session-1")
    assert run() == 0
    assert len([call for call in calls if "/execute" in call.url.path]) == 1
    assert "dummy-key" not in capsys.readouterr().out
    assert len(prompts) == 4


def test_declined_authorization_creates_no_account(smoke, monkeypatch):
    _, _, calls, _, run = smoke
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert run() == 0
    assert all(call.method == "GET" for call in calls)


@pytest.mark.parametrize("failure", ["owner", "changed", "unknown"])
def test_bound_execution_checks_identity_version_and_unknown_outcomes(smoke, failure):
    _, args, calls, state, run = smoke
    args.command = "call"
    args.connection_id = "account-1"
    args.execute = True
    if failure == "owner":
        state["owner"] = "someone-else"
        with pytest.raises(ConnectorProviderError, match="owner_mismatch"):
            run()
    elif failure == "changed":
        state["changed"] = True
        with pytest.raises(ValueError, match="Tool changed"):
            run()
    else:
        state["unknown"] = True
        assert run() == 1
    assert len([call for call in calls if call.method == "POST"]) == (1 if failure == "unknown" else 0)


def test_cli_preview_does_not_require_account_selectors(smoke, monkeypatch):
    module, _, _, _, _ = smoke
    monkeypatch.setattr("sys.argv", ["composio.py", "tools", "--connector", "github"])
    parsed = module.parse_args()
    assert parsed.connection_id is None
    assert parsed.user_id is None


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_cli_reports_http_failure_without_exposing_secrets(smoke, monkeypatch, capsys, status):
    module, args, _, _, _ = smoke
    args.command = "discover"
    args.connector = None
    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setenv(f"{args.provider.upper()}_API_KEY", "dummy-key")
    client_type = httpx2.AsyncClient
    requests = []

    def reject(request):
        requests.append(request)
        return httpx2.Response(
            status, headers={"set-cookie": "secret-cookie"}, json={"message": "dummy-key secret-provider-body"}
        )

    monkeypatch.setattr(
        module.httpx2, "AsyncClient", lambda **kwargs: client_type(transport=httpx2.MockTransport(reject), **kwargs)
    )
    assert module.main() == 1
    assert len(requests) == 1
    output = capsys.readouterr()
    assert f'"http_status": {status}' in output.out
    assert '"method": "GET"' in output.out
    assert "/toolkits" in output.out
    expected = "rate_limited" if status == 429 else "provider_unavailable" if status == 503 else "provider_rejected"
    assert f'"error": "{expected}"' in output.out
    for secret in ("dummy-key", "secret-cookie", "secret-provider-body"):
        assert secret not in output.out + output.err
