from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import pytest
from a13n_harness.providers.model.oauth.chatgpt import SCOPES, OpenAIChatGPTCredentials, OpenAIChatGPTOAuthFlow
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStore
from a13n_harness_ui.model_accounts.chatgpt import ChatGPTAccountStore
from a13n_harness_ui.model_accounts.login import LoginRequest
from a13n_harness_ui.model_accounts.models import AccountStoreError
from anyio import create_task_group, fail_after, sleep
from pydantic import SecretStr
from pydantic_ai.exceptions import UserError

from .test_app import _settings

pytestmark = pytest.mark.anyio


def credentials(host: str) -> OpenAIChatGPTCredentials:
    return OpenAIChatGPTCredentials(
        subject="test-subject",
        client_id="issued-client",
        ext_agent_host_id=host,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=SCOPES,
        access_token="private-access",
        refresh_token="private-refresh",
        id_token="private-identity",
        email="test@example.test",
    )


def callback(flow: OpenAIChatGPTOAuthFlow) -> str:
    return (
        flow.authorization.redirect_uri
        + "?"
        + urlencode({"state": flow.authorization.state, "code": "private-code", "client_id": "issued-client"})
    )


async def signed_in(path: Path, monkeypatch: pytest.MonkeyPatch) -> ChatGPTAccountStore:
    store = ChatGPTAccountStore(path)
    flow = await store.begin("first", "http://127.0.0.1:43111/auth/callback")
    host_id = flow.authorization.ext_agent_host_id
    assert UUID(host_id).version == 4 and UUID(host_id).urn == host_id

    async def exchange(self, url):
        assert self.validate_callback(url).client_id == "issued-client"
        assert json.loads(path.read_text())["openai_chatgpt"]["pending"] is None
        return credentials(self.authorization.ext_agent_host_id)

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)
    await store.complete("first", callback(flow))
    return store


@pytest.mark.parametrize("legacy_host", [False, True])
async def test_chatgpt_uses_one_auth_writer_preserves_keys_and_fences_pending(tmp_path, monkeypatch, legacy_host):
    path = tmp_path / "auth.json"
    keys = ApiKeyStore(path)
    await keys.put(ApiKeyInput(credential_ref="key-test", key=SecretStr("private-api-key")))
    await keys.document.update(lambda document: document.update({"unrelated": {"value": 42}}))
    if legacy_host:
        await keys.document.update(
            lambda document: document.update({"openai_chatgpt": {"host_id": "host-" + "a" * 32}})
        )
    store = await signed_in(path, monkeypatch)
    assert await keys.load("key-test") == "private-api-key"
    assert json.loads(path.read_text())["unrelated"] == {"value": 42}
    assert (await store.inspect()).usable
    assert "private-access" not in repr(await store.inspect())
    with pytest.raises(AccountStoreError, match="no longer pending"):
        await ChatGPTAccountStore(path).complete("first", "http://127.0.0.1:43111/auth/callback?code=private-code")
    flow = await store.begin("returning", "http://127.0.0.1:43112/auth/callback")
    assert flow.authorization.client_id == "issued-client"
    assert flow.authorization.subject == "test-subject"
    assert flow.authorization.ext_agent_host_id == (await store.load()).ext_agent_host_id
    with pytest.raises(UserError):
        await store.complete("returning", callback(flow).replace(flow.authorization.state, "wrong-state"))
    assert json.loads(path.read_text())["openai_chatgpt"]["pending"] is not None
    await store.cancel("returning")
    assert await keys.load("key-test") == "private-api-key"


async def test_chatgpt_concurrent_rotation_publishes_once_and_preserves_api_key(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    store = await signed_in(path, monkeypatch)
    expected = await store.load()
    calls = 0

    async def exchange(current):
        nonlocal calls
        calls += 1
        await sleep(0.03)
        return replace(current, access_token="replacement-access", refresh_token="replacement-refresh")

    results = []

    async def rotate():
        results.append(await ChatGPTAccountStore(path).rotate(expected, exchange))

    async with create_task_group() as tasks:
        tasks.start_soon(rotate)
        tasks.start_soon(rotate)
        tasks.start_soon(ApiKeyStore(path).put, ApiKeyInput(credential_ref="key-test", key=SecretStr("parallel-key")))
    assert calls == 1 and len(results) == 2
    assert results[0] == results[1] == await store.load()
    assert await ApiKeyStore(path).load("key-test") == "parallel-key"

    async def uncertain(current):
        raise OSError("lost response")

    with pytest.raises(OSError):
        await store.rotate(results[0], uncertain)
    with pytest.raises(ModelAuthenticationError, match="unknown"):
        await ChatGPTAccountStore(path).load()


async def test_chatgpt_logout_retains_registration_and_never_touches_codex(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    store = await signed_in(path, monkeypatch)
    old = await store.load()

    async def revoke(current):
        assert current == old
        assert json.loads(path.read_text())["openai_chatgpt"]["registrations"]["issued-client"]["credentials"] is None
        raise OSError("not confirmed")

    monkeypatch.setattr("a13n_harness_ui.model_accounts.chatgpt.revoke_chatgpt_credentials", revoke)
    assert await store.logout()
    status = await store.inspect()
    assert not status.usable and "not confirmed" in status.message
    assert len(await store.candidates()) == 1
    flow = await ChatGPTAccountStore(path).begin("again", "http://127.0.0.1:43111/auth/callback")
    assert flow.authorization.client_id == "issued-client"
    assert flow.authorization.subject == "test-subject"
    assert not (tmp_path / ".codex").exists()


async def test_app_chatgpt_paste_session_validates_and_clears_input(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("GROK_AUTH_PATH", str(tmp_path / "grok.json"))
    path = tmp_path / "state" / "auth.json"

    async def exchange(self, url):
        self.validate_callback(url)
        return credentials(self.authorization.ext_agent_host_id)

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)

    async def no_listener(**kwargs):
        raise AssertionError("Manual callback paste must not bind a local socket")

    monkeypatch.setattr("a13n_harness_ui.model_accounts.login.create_tcp_listener", no_listener)
    async with open_harness_ui_app(_settings(path.parent)) as app:
        status = await app.start_login(LoginRequest(provider="chatgpt", method="manual_callback"))
        with fail_after(3):
            while status.state == "starting":
                await sleep(0.01)
                status = await app.login_status(status.session_id)
        assert status.state == "waiting", status
        with pytest.raises(HarnessUiError, match="does not match"):
            await app.submit_login_callback(status.session_id, "https://evil.test?code=private-code")
        pending = (await ChatGPTAccountStore(path)._read()).pending
        assert pending is not None
        await app.submit_login_callback(status.session_id, callback(OpenAIChatGPTOAuthFlow(pending)))
        with fail_after(3):
            while status.state == "waiting":
                await sleep(0.01)
                status = await app.login_status(status.session_id)
        assert status.state == "succeeded", status
        assert status.verification_url is None and "private-" not in status.model_dump_json()
        assert (await app.inspect_model_account("chatgpt")).usable
        assert len(await app.model_account_candidates("chatgpt")) == 1


async def test_automatic_callback_uses_the_same_exchange_and_cancel_discards_pending(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    import httpx2

    async def exchange(self, url):
        self.validate_callback(url)
        return credentials(self.authorization.ext_agent_host_id)

    monkeypatch.setattr(OpenAIChatGPTOAuthFlow, "exchange_callback", exchange)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("GROK_AUTH_PATH", str(tmp_path / "grok.json"))
    path = tmp_path / "state" / "auth.json"
    async with open_harness_ui_app(_settings(path.parent)) as app:
        status = await app.start_login(LoginRequest(provider="chatgpt", method="browser"))
        with fail_after(3):
            while status.state == "starting":
                await sleep(0.01)
                status = await app.login_status(status.session_id)
        pending = (await ChatGPTAccountStore(path)._read()).pending
        assert pending
        async with httpx2.AsyncClient(trust_env=False) as client:
            result = await client.get(callback(OpenAIChatGPTOAuthFlow(pending)))
        assert result.status_code == 200 and "private-code" not in result.text
        with fail_after(3):
            while status.state == "waiting":
                await sleep(0.01)
                status = await app.login_status(status.session_id)
        assert status.state == "succeeded"
        status = await app.start_login(LoginRequest(provider="chatgpt", method="manual_callback"))
        with fail_after(3):
            while status.state == "starting":
                await sleep(0.01)
                status = await app.login_status(status.session_id)
        assert parse_qs(urlsplit(status.verification_url).query)["client_id"] == ["issued-client"]
        assert (await app.cancel_login(status.session_id)).state == "cancelled"
        assert (await ChatGPTAccountStore(path)._read()).pending is None
        assert (await app.inspect_model_account("chatgpt")).usable
