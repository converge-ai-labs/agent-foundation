"""The real UI resolver and cooperating processes share one durable Grok grant boundary."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import anyio
import httpx2
import pytest
from a13n_harness import AgentContext
from a13n_harness.providers.model.oauth import (
    CredentialPersistenceError,
    GrokCredentials,
    ModelAuthenticationError,
    RefreshNotDispatched,
)
from a13n_harness_ui.composition.models import ResolvedModelRecipe
from a13n_harness_ui.configuration import GrokSubscriptionAuthentication
from a13n_harness_ui.model_accounts.grok import GrokAccountStore, resolve_grok_policy
from a13n_harness_ui.model_accounts.models import RequiredAction
from a13n_harness_ui.model_runtime import GrokSubscriptionSource, HarnessUiModelResolver
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters, ModelResolutionContext

pytestmark = pytest.mark.anyio
SCOPE = "https://auth.x.ai::client"


def credential(marker: str, *, expired: bool = False) -> GrokCredentials:
    now = datetime.now(UTC)
    return GrokCredentials(
        account_id="account",
        auth_mode="oidc",
        create_time=now,
        expires_at=now + timedelta(minutes=-1 if expired else 60),
        issuer="https://auth.x.ai",
        client_id="client",
        access_token=f"access-{marker}",
        refresh_token=f"refresh-{marker}",
    )


def entry(value: GrokCredentials) -> dict:
    return {
        "user_id": value.account_id,
        "auth_mode": value.auth_mode,
        "create_time": value.create_time.isoformat(),
        "expires_at": value.expires_at.isoformat(),
        "oidc_issuer": value.issuer,
        "oidc_client_id": value.client_id,
        "key": value.access_token,
        "refresh_token": value.refresh_token,
        "unknown": {"retained": True},
    }


def store(path: Path) -> GrokAccountStore:
    return GrokAccountStore(resolve_grok_policy(environ={"GROK_AUTH_PATH": str(path)}), scope=SCOPE)


def seed(path: Path, value: GrokCredentials) -> None:
    path.write_text(json.dumps({SCOPE: entry(value), "another-scope": {"unknown": True}}))


def resolver(path: Path, refresh) -> HarnessUiModelResolver:
    recipe = ResolvedModelRecipe(
        model_id="primary",
        route="grok:grok-code",
        authentication=GrokSubscriptionAuthentication(kind="grok_subscription"),
    )
    return HarnessUiModelResolver(
        {"primary": recipe}, subscription_sources={"grok_subscription": GrokSubscriptionSource(store(path), refresh)}
    )


async def call(resolved: HarnessUiModelResolver) -> None:
    context = ModelResolutionContext(agent=Mock(), deps=Mock(spec=AgentContext, thread_id="thread-current"))
    model = await resolved(context, "primary")
    async with model:
        response = await model.request([ModelRequest(parts=[UserPromptPart("hello")])], None, ModelRequestParameters())
        assert response.parts[0].content == "OK"


@pytest.fixture
def native_transport(monkeypatch):
    sent = []

    async def handle(_self, request):
        sent.append(request.headers["authorization"])
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "model": "grok-code",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "OK", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", handle)
    return sent


async def test_models_and_fresh_runs_share_rotation(tmp_path, native_transport):
    path = tmp_path / "auth.json"
    seed(path, credential("old", expired=True))
    refreshes = []

    async def refresh(old):
        refreshes.append(old.refresh_token)
        await anyio.sleep(0.03)
        return credential("new")

    first = resolver(path, refresh)
    async with anyio.create_task_group() as tasks:
        for resolved in (first, first.fresh(), resolver(path, refresh)):
            tasks.start_soon(call, resolved)
    assert refreshes == ["refresh-old"]
    assert native_transport == ["Bearer access-new"] * 3
    document = json.loads(path.read_text())
    assert document[SCOPE]["unknown"] == {"retained": True}
    assert document["another-scope"] == {"unknown": True}
    assert path.stat().st_mode & 0o777 == 0o600
    journal = Path(f"{path}.refresh.json")
    assert journal.stat().st_mode & 0o777 == 0o600
    assert "refresh-old" not in journal.read_text() and "access-old" not in journal.read_text()


@pytest.mark.parametrize("failure", ["lost-response", "save", "cancel"])
async def test_uncertain_grant_blocks_new_models_runs_and_metadata_changes(
    tmp_path, native_transport, monkeypatch, failure
):
    path = tmp_path / "auth.json"
    old = credential("old", expired=True)
    seed(path, old)
    refreshes = []
    entered = anyio.Event()

    async def refresh(value):
        refreshes.append(value.refresh_token)
        entered.set()
        if failure == "lost-response":
            raise OSError("response lost")
        if failure == "cancel":
            await anyio.sleep_forever()
        return credential("new")

    if failure == "save":

        async def fail(*args):
            raise OSError("disk full")

        monkeypatch.setattr(GrokAccountStore, "_publish", fail)
    first = resolver(path, refresh)
    if failure == "cancel":
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(call, first)
            await entered.wait()
            tasks.cancel_scope.cancel()
    else:
        with pytest.raises(ModelAPIError):
            await call(first)
    seed(path, replace(old, access_token="changed-access", expires_at=datetime.now(UTC) + timedelta(hours=2)))
    for resolved in (first.fresh(), resolver(path, refresh)):
        with pytest.raises(ModelAPIError) as failure:
            await call(resolved)
        assert isinstance(failure.value.__cause__.__cause__, ModelAuthenticationError)
    assert refreshes == ["refresh-old"]
    assert native_transport == []
    assert (await store(path).inspect()).required_action is RequiredAction.LOGIN
    seed(path, credential("reauthenticated"))
    await call(resolver(path, refresh))
    assert native_transport == ["Bearer access-reauthenticated"]


async def test_known_predispatch_failure_does_not_poison_grant(tmp_path):
    path = tmp_path / "auth.json"
    old = credential("old", expired=True)
    seed(path, old)

    async def failed(_):
        raise RefreshNotDispatched("grok", "Discovery unavailable.")

    with pytest.raises(RefreshNotDispatched):
        await store(path).rotate(old, failed)
    assert await store(path).load() == old


_PROCESS = """
import json, os, sys
from pathlib import Path
from datetime import datetime, UTC, timedelta
from dataclasses import replace
import anyio
from a13n_harness_ui.model_accounts.grok import GrokAccountStore, resolve_grok_policy, _parse_entry
path = Path(sys.argv[1]); scope = "https://auth.x.ai::client"
expected = _parse_entry(json.loads(sys.argv[2]), scope)
async def main():
    source = GrokAccountStore(resolve_grok_policy(environ={"GROK_AUTH_PATH": str(path)}), scope=scope)
    async def exchange(value):
        with Path(str(path)+".spent").open("a") as log:
            log.write("spent\\n"); log.flush(); os.fsync(log.fileno())
        if sys.argv[3] == "die": os._exit(42)
        await anyio.sleep(.1)
        return replace(value, access_token="access-process", refresh_token="refresh-process", expires_at=datetime.now(UTC)+timedelta(hours=1))
    await source.rotate(expected, exchange)
anyio.run(main)
"""


async def test_cooperating_processes_spend_once_and_publish(tmp_path):
    path = tmp_path / "auth.json"
    old = credential("old", expired=True)
    seed(path, old)
    args = [sys.executable, "-c", _PROCESS, str(path), json.dumps(entry(old)), "rotate"]
    async with anyio.create_task_group() as tasks:
        for _ in range(2):
            tasks.start_soon(anyio.run_process, args)
    assert Path(f"{path}.spent").read_text() == "spent\n"
    assert (await store(path).load()).refresh_token == "refresh-process"


async def test_process_death_releases_lock_but_not_grant_permission(tmp_path):
    path = tmp_path / "auth.json"
    old = credential("old", expired=True)
    seed(path, old)
    result = await anyio.run_process(
        [sys.executable, "-c", _PROCESS, str(path), json.dumps(entry(old)), "die"], check=False
    )
    assert result.returncode == 42
    with pytest.raises(ModelAuthenticationError, match="unknown"):
        await store(path).load()
    assert Path(f"{path}.spent").read_text() == "spent\n"
    assert json.loads(path.read_text())[SCOPE]["refresh_token"] == "refresh-old"


@pytest.mark.parametrize("mutation", ["login", "logout"])
async def test_account_mutations_wait_for_rotation_and_preserve_unknown_fields(tmp_path, mutation):
    path = tmp_path / "auth.json"
    old = credential("old", expired=True)
    seed(path, old)
    exchanging, release, mutated = anyio.Event(), anyio.Event(), anyio.Event()

    async def exchange(_):
        exchanging.set()
        await release.wait()
        return credential("rotated")

    async def login(_):
        return credential("login")

    async def change():
        if mutation == "login":
            await store(path).login(login)
        else:
            await store(path).logout()
        mutated.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(store(path).rotate, old, exchange)
        await exchanging.wait()
        tasks.start_soon(change)
        await anyio.sleep(0.03)
        assert not mutated.is_set()
        release.set()
    document = json.loads(path.read_text())
    assert document["another-scope"] == {"unknown": True}
    if mutation == "logout":
        assert SCOPE not in document
    else:
        assert document[SCOPE]["unknown"] == {"retained": True}
        assert (await store(path).load()).refresh_token in {"refresh-login", "refresh-rotated"}


async def test_external_writer_during_exchange_is_not_overwritten(tmp_path):
    path = tmp_path / "auth.json"
    old = credential("old", expired=True)
    seed(path, old)
    replacement = replace(credential("other"), account_id="different-account")

    async def exchange(_):
        seed(path, replacement)
        return credential("rotated")

    with pytest.raises(CredentialPersistenceError):
        await store(path).rotate(old, exchange)
    assert await store(path).load() == replacement
    seed(path, old)
    with pytest.raises(ModelAuthenticationError, match="unknown"):
        await store(path).load()


async def test_login_requires_a_new_grant_and_recovers_despite_newer_old_metadata(tmp_path):
    from a13n_harness_ui.model_accounts.models import AccountStoreError

    path = tmp_path / "auth.json"
    old = credential("old", expired=True)
    seed(path, old)

    async def lost(_):
        raise OSError("response lost")

    with pytest.raises(OSError):
        await store(path).rotate(old, lost)
    seed(path, replace(old, expires_at=datetime.now(UTC) + timedelta(days=10)))

    async def stale(_):
        return replace(old, access_token="different-access", expires_at=datetime.now(UTC) + timedelta(hours=1))

    with pytest.raises(AccountStoreError, match="new usable"):
        await store(path).login(stale)

    async def fresh(_):
        return credential("reauthorized")

    projection = await store(path).login(fresh)
    assert projection.usable
    assert (await store(path).load()).refresh_token == "refresh-reauthorized"
