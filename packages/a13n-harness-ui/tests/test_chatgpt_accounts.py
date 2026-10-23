from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import httpx2
import jwt
import pytest
from a13n_harness.providers.model.oauth.chatgpt import ChatGPTAuthorization
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStore
from a13n_harness_ui.model_accounts.chatgpt import ChatGPTAccountStore
from a13n_harness_ui.model_accounts.login import LoginRequest
from a13n_harness_ui.model_accounts.models import AccountStoreError
from anyio import fail_after, sleep
from pydantic import SecretStr
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials, OpenAIChatGPTProvider

from .test_app import _settings

pytestmark = pytest.mark.anyio


def credentials(host: str) -> OpenAIChatGPTCredentials:
    return OpenAIChatGPTCredentials(
        client_id="issued-client",
        scope="resource.invoke chatgpt.tokens.use.direct",
        access_token="private-access",
        refresh_token="private-refresh",
        id_token=jwt.encode({"sub": "test-subject", "email": "test@example.test"}, "", algorithm="none"),
    )


def callback(flow: ChatGPTAuthorization) -> str:
    return (
        flow.redirect_uri + "?" + urlencode({"state": flow.state, "code": "private-code", "client_id": "issued-client"})
    )


async def signed_in(path: Path, monkeypatch: pytest.MonkeyPatch) -> ChatGPTAccountStore:
    store = ChatGPTAccountStore(path)
    flow = await store.begin("first", "http://127.0.0.1:43111/auth/callback")
    assert flow.agent_name == "Agent Foundation OSS"
    host_id = flow.ext_agent_host_id
    assert UUID(host_id).version == 4 and UUID(host_id).urn == host_id

    async def exchange(self, url):
        assert self.validate_callback(url)[1] == "issued-client"
        assert json.loads(path.read_text())["openai_chatgpt"]["pending"] is None
        return credentials(self.ext_agent_host_id)

    monkeypatch.setattr(ChatGPTAuthorization, "exchange_callback", exchange)
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
    assert flow.client_id == "issued-client"
    assert flow.ext_agent_host_id == json.loads(path.read_text())["openai_chatgpt"]["host_id"]
    with pytest.raises(UserError):
        await store.complete("returning", callback(flow).replace(flow.state, "wrong-state"))
    assert json.loads(path.read_text())["openai_chatgpt"]["pending"] is not None
    await store.cancel("returning")
    assert await keys.load("key-test") == "private-api-key"


@pytest.mark.parametrize("expired", [False, True])
async def test_chatgpt_official_refresh_persists_across_store_recreation(tmp_path, monkeypatch, expired):
    path = tmp_path / "auth.json"
    store = await signed_in(path, monkeypatch)
    source = store.source()
    old = await source.load()
    original = replace(
        old,
        access_token=jwt.encode(
            {"exp": (datetime.now(UTC) + timedelta(hours=-1 if expired else 1)).timestamp()}, "", algorithm="none"
        ),
    )
    await source.save(original)
    calls = []

    def respond(request):
        calls.append(request.url.host)
        if request.url.host == "auth.openai.com":
            return httpx2.Response(
                200, json={"access_token": "replacement-access", "refresh_token": "replacement-refresh"}
            )
        if request.headers["authorization"] != "Bearer replacement-access":
            return httpx2.Response(401)
        return httpx2.Response(200, json={"models": []})

    for _ in range(2):
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            provider = OpenAIChatGPTProvider(credential_source=ChatGPTAccountStore(path).source(), http_client=client)
            await provider.client.get("/models", cast_to=dict)
    assert calls.count("auth.openai.com") == 1
    assert (await ChatGPTAccountStore(path).load()).refresh_token == "replacement-refresh"
    await ApiKeyStore(path).put(ApiKeyInput(credential_ref="key-test", key=SecretStr("parallel-key")))
    assert await ApiKeyStore(path).load("key-test") == "parallel-key"


async def test_chatgpt_save_cannot_restore_disconnected_or_replaced_credentials(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    store = await signed_in(path, monkeypatch)
    source, other = store.source(), store.source()
    old = await source.load()
    await other.load()
    await other.save(replace(old, access_token="new-access"))
    with pytest.raises(ModelAuthenticationError, match="changed"):
        await source.save(replace(old, refresh_token="stale-refresh"))
    await source.load()
    await store.logout()
    with pytest.raises(ModelAuthenticationError, match="changed"):
        await source.save(old)
    assert not (await store.inspect()).usable


async def test_chatgpt_logout_retains_registration_and_never_touches_codex(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    store = await signed_in(path, monkeypatch)
    assert await store.logout()
    status = await store.inspect()
    assert not status.usable and "Manage remote access" in status.message
    assert len(await store.candidates()) == 1
    flow = await ChatGPTAccountStore(path).begin("again", "http://127.0.0.1:43111/auth/callback")
    assert flow.client_id == "issued-client"
    assert not (tmp_path / ".codex").exists()


async def test_app_chatgpt_paste_session_validates_and_clears_input(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("GROK_AUTH_PATH", str(tmp_path / "grok.json"))
    path = tmp_path / "state" / "auth.json"

    async def exchange(self, url):
        self.validate_callback(url)
        return credentials(self.ext_agent_host_id)

    monkeypatch.setattr(ChatGPTAuthorization, "exchange_callback", exchange)

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
        await app.submit_login_callback(status.session_id, callback(pending))
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
        return credentials(self.ext_agent_host_id)

    monkeypatch.setattr(ChatGPTAuthorization, "exchange_callback", exchange)
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
            result = await client.get(callback(pending))
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


async def test_legacy_public_store_and_pending_attempt_remain_readable(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    store = await signed_in(path, monkeypatch)
    await store.begin("legacy", "http://127.0.0.1:43111/auth/callback")
    document = json.loads(path.read_text())
    state = document["openai_chatgpt"]
    state["blocked_grants"] = ["retired-fingerprint"]
    saved = state["registrations"]["issued-client"]["credentials"]
    saved["scopes"] = saved.pop("scope").split()
    saved.update(subject="test-subject", expires_at="2026-01-01T00:00:00Z", token_endpoint_auth_method="none")
    state["pending"].update(preconfigured_client=False, token_endpoint_auth_method="none", subject="test-subject")
    path.write_text(json.dumps(document))
    assert (await ChatGPTAccountStore(path).inspect()).usable
    await store.cancel("legacy")
    assert "blocked_grants" not in json.loads(path.read_text())["openai_chatgpt"]


async def test_declined_plan_usage_requests_consent_on_next_sign_in(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    store = await signed_in(tmp_path / "auth.json", monkeypatch)
    source = store.source()
    original = await source.load()
    await source.save(replace(original, scope="openid profile"))
    assert not (await store.inspect()).usable
    pending = await store.begin("consent", "http://127.0.0.1:43111/auth/callback")
    assert parse_qs(urlsplit(pending.authorization_url()).query)["prompt"] == ["consent"]
