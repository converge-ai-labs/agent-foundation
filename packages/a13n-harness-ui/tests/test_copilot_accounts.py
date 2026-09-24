"""Native Copilot account lifecycle with synthetic grants and disposable stores."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from a13n_harness.providers.model.oauth import CopilotCredentials, ModelAuthenticationError
from a13n_harness_ui.model_accounts.copilot import DEFAULT_COPILOT_CLIENT_ID, CopilotAccountStore
from a13n_harness_ui.model_accounts.models import AccountStoreConflictError, Availability
from pydantic_ai.providers.github_copilot import GitHubCopilotCredentials

pytestmark = pytest.mark.anyio


def grant(marker="first", *, account="test-user", now=None):
    return CopilotCredentials.issued(
        GitHubCopilotCredentials(
            access_token=f"synthetic-access-{marker}",
            refresh_token=f"synthetic-refresh-{marker}",
            token_type="bearer",
            scope="read:user",
            expires_in=3600,
            refresh_token_expires_in=86400,
        ),
        account_id=account,
        client_id=DEFAULT_COPILOT_CLIENT_ID,
        now=now,
    )


async def login(store, credential):
    async def authorize(request):
        assert request.client_id == DEFAULT_COPILOT_CLIENT_ID
        return credential

    return await store.login(authorize)


async def test_host_grants_normalize_expiry_once_and_preserve_accounts_on_logout(tmp_path):
    path = tmp_path / "oauth" / "copilot.json"
    store = CopilotAccountStore(path)
    assert (await store.inspect()).availability is Availability.ABSENT
    assert not path.exists()
    original = grant()
    assert (await login(store, original)).usable
    reloaded = CopilotAccountStore(path)
    assert await reloaded.load() == replace(original, source_id=store.native_source_id)
    assert path.stat().st_mode & 0o777 == 0o600
    document = json.loads(path.read_text())
    document["accounts"]["other-user"] = {"unrelated": "preserved"}
    document["future_metadata"] = True
    path.write_text(json.dumps(document))
    assert await reloaded.logout()
    remaining = json.loads(path.read_text())
    assert remaining["selected"] == "test-user"
    assert remaining["accounts"] == {"other-user": {"unrelated": "preserved"}}
    assert remaining["future_metadata"] is True
    assert (await reloaded.inspect()).availability is Availability.ABSENT
    assert not await reloaded.logout()


async def test_two_store_instances_refresh_once_and_publish_complete_pair(tmp_path):
    path = tmp_path / "copilot.json"
    first, second = CopilotAccountStore(path), CopilotAccountStore(path)
    original, rotated = grant(), grant("rotated")
    await login(first, original)
    original = await first.load()
    rotated = replace(rotated, source_id=first.native_source_id)
    calls = []
    values = []

    async def exchange(current):
        calls.append(current)
        await anyio.sleep(0.02)
        return rotated

    async def run(store):
        values.append(await store.rotate(original, exchange))
        assert await CopilotAccountStore(path).load() == rotated

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run, first)
        tasks.start_soon(run, second)
    assert calls == [original]
    assert values == [rotated, rotated]


async def test_uncertain_exchange_stays_blocked_after_reopen(tmp_path):
    path = tmp_path / "copilot.json"
    store = CopilotAccountStore(path)
    original = grant()
    await login(store, original)
    original = await store.load()

    async def interrupted(_):
        raise OSError("synthetic lost exchange acknowledgement")

    with pytest.raises(OSError):
        await store.rotate(original, interrupted)
    reopened = CopilotAccountStore(path)
    assert not (await reopened.inspect()).usable
    with pytest.raises(ModelAuthenticationError, match="unknown"):
        await reopened.load()
    assert (await login(reopened, grant("new-login"))).usable


async def test_login_does_not_overwrite_concurrent_logout(tmp_path):
    store = CopilotAccountStore(tmp_path / "copilot.json")
    await login(store, grant())

    async def authorize(_):
        await store.logout()
        return grant("new")

    with pytest.raises(AccountStoreConflictError):
        await store.login(authorize)
    assert (await store.inspect()).availability is Availability.ABSENT


async def test_native_refresh_revalidates_dataclass_before_publication(tmp_path):
    store = CopilotAccountStore(tmp_path / "copilot.json")
    original = grant()
    await login(store, original)
    original = await store.load()

    async def malformed(_):
        return replace(original, credentials=replace(original.credentials, access_token=""))

    with pytest.raises(ValueError):
        await store.rotate(original, malformed)
    document = json.loads(store.path.read_text())
    assert document["accounts"]["test-user"]["credentials"]["access_token"] == original.access_token
    with pytest.raises(ModelAuthenticationError, match="unknown"):
        await CopilotAccountStore(store.path).load()


async def test_expired_refreshable_grant_is_ready_without_network_or_rolling_expiry(tmp_path):
    store = CopilotAccountStore(tmp_path / "copilot.json")
    past = datetime.now(UTC) - timedelta(hours=2)
    original = grant(now=past)
    assert (await login(store, original)).usable
    assert (await store.load()).expires_at == original.expires_at
    assert not (await store.inspect(now=past + timedelta(days=2))).usable


def cli_file(tmp_path, *, snake=False, plaintext=True):
    path = tmp_path / "cli" / "config.json"
    path.parent.mkdir()

    def user(login):
        return {"host": "https://github.com", "login": login}

    document = {
        "lastLoggedInUser": user("test-user"),
        "loggedInUsers": [user("test-user"), user("other-user")],
        "copilotTokens": {
            "https://github.com:test-user": "synthetic-legacy",
            "https://github.com:other-user": "synthetic-other",
        },
        "authTokens": {"https://github.com:test-user": {"token": "synthetic-modern"}},
        "unrelated": {
            "url": "https://example.test/a//b",
            "quoted": 'say "https://example.test/x"',
            "comment": 'value "/* not a comment */"',
            "newline": "a\n// inside string",
        },
    }
    if snake:
        aliases = {
            "lastLoggedInUser": "last_logged_in_user",
            "loggedInUsers": "logged_in_users",
            "copilotTokens": "copilot_tokens",
            "authTokens": "auth_tokens",
        }
        document = {aliases.get(key, key): value for key, value in document.items()}
    path.write_text("// Automatically managed\n/* synthetic fixture */\n" + json.dumps(document))
    path.with_name("settings.json").write_text(json.dumps({"store_token_plaintext": plaintext}))
    return path


@pytest.mark.parametrize("snake", [False, True])
async def test_cli_reuse_pins_account_and_source_without_copying_secrets(tmp_path, snake):
    from a13n_harness_ui.model_accounts.models import AccountSelection

    cli = cli_file(tmp_path, snake=snake)
    store = CopilotAccountStore(tmp_path / "host" / "copilot.json", cli_path=cli)
    status = await store.inspect()
    assert status.usable and status.shared_with_cli and status.account_id == "test-user"
    assert (await store.load()).access_token == "synthetic-legacy"
    assert (await store.load()).expires_at is None
    assert (await store.load()).refresh_token is None
    assert "synthetic-" not in store.path.read_text()
    assert json.loads(store.path.read_text())["accounts"] == {}
    document = (await store.cli.snapshot()).document
    selected_field = "last_logged_in_user" if snake else "lastLoggedInUser"
    token_field = "copilot_tokens" if snake else "copilotTokens"
    document[selected_field]["login"] = "other-user"
    cli.write_text(json.dumps(document))
    assert (await store.load()).account_id == "test-user"
    document[token_field].pop("https://github.com:test-user")
    document.pop("auth_tokens" if snake else "authTokens")
    cli.write_text(json.dumps(document))
    assert not (await store.inspect()).usable
    assert (await store.inspect()).account_id == "test-user"
    # A different discovery path must not replace the durable binding.
    reopened = CopilotAccountStore(store.path, cli_path=tmp_path / "elsewhere" / "config.json")
    assert not (await reopened.inspect()).usable
    await store.select(AccountSelection(source="copilot_cli_file", account_id="other-user"))
    assert (await store.load()).access_token == "synthetic-other"


async def test_cli_logout_deletes_both_token_shapes_and_preserves_other_data(tmp_path):
    cli = cli_file(tmp_path)
    store = CopilotAccountStore(tmp_path / "host.json", cli_path=cli)
    await store.inspect()
    original = (await store.cli.snapshot()).document
    settings = cli.with_name("settings.json").read_bytes()
    assert await store.logout()
    remaining = json.loads(cli.read_text())
    assert remaining["unrelated"] == original["unrelated"]
    assert remaining["loggedInUsers"] == original["loggedInUsers"]
    assert remaining["copilotTokens"] == {"https://github.com:other-user": "synthetic-other"}
    assert remaining["authTokens"] == {}
    assert cli.with_name("settings.json").read_bytes() == settings
    assert (await store.inspect()).account_id == "test-user"
    assert not (await store.inspect()).usable
    assert not await store.logout()


async def test_keychain_policy_never_uses_stale_file_and_native_login_is_available(tmp_path):
    cli = cli_file(tmp_path, plaintext=False)
    store = CopilotAccountStore(tmp_path / "host.json", cli_path=cli)
    status = await store.inspect()
    assert status.availability is Availability.UNSUPPORTED
    assert "keychain" in status.message
    assert not store.path.exists()
    assert await store.candidates() == ()
    await login(store, grant())
    assert not (await store.inspect()).shared_with_cli
    assert (await store.load()).source_id == store.native_source_id
    await store.logout()
    cli.with_name("settings.json").write_text('{"storeTokenPlaintext": true}')
    assert not (await store.inspect()).usable  # Native binding survives logout.


async def test_selected_cli_source_becoming_keychain_is_not_a_file_fallback(tmp_path):
    cli = cli_file(tmp_path)
    store = CopilotAccountStore(tmp_path / "host.json", cli_path=cli)
    assert (await store.inspect()).usable
    cli.with_name("settings.json").write_text("{}")
    assert (await store.inspect()).availability is Availability.UNSUPPORTED
    with pytest.raises(Exception, match="keychain"):
        await store.load()


@pytest.mark.parametrize(
    "envelope", ["not-an-object", {"token": ""}, {"token": "synthetic", "refreshToken": "unsupported"}]
)
async def test_unknown_cli_envelope_never_invents_refresh_or_uses_legacy(tmp_path, envelope):
    cli = cli_file(tmp_path)
    store = CopilotAccountStore(tmp_path / "host.json", cli_path=cli)
    document = (await store.cli.snapshot()).document
    document["authTokens"]["https://github.com:test-user"] = envelope
    cli.write_text(json.dumps(document))
    assert (await store.inspect()).availability is Availability.INCOMPATIBLE
    assert not (await store.inspect()).usable


async def test_cli_write_conflict_preserves_external_update(tmp_path, monkeypatch):
    from a13n_harness_ui.model_accounts import copilot_cli

    cli = cli_file(tmp_path)
    store = CopilotAccountStore(tmp_path / "host.json", cli_path=cli)
    await store.inspect()
    original_write = copilot_cli.write_json_if_unchanged

    async def changed(path, document, digest, **kwargs):
        latest = (await store.cli.snapshot()).document
        latest["external-write"] = True
        path.write_text(json.dumps(latest))
        await original_write(path, document, digest, **kwargs)

    monkeypatch.setattr(copilot_cli, "write_json_if_unchanged", changed)
    with pytest.raises(AccountStoreConflictError):
        await store.logout()
    assert json.loads(cli.read_text())["external-write"] is True
    assert (await store.load()).access_token == "synthetic-legacy"


async def test_separate_processes_spend_rotating_grant_once(tmp_path):
    import sys

    store = CopilotAccountStore(tmp_path / "host.json")
    await login(store, grant())
    code = """
import anyio, sys
from pathlib import Path
from dataclasses import replace
from a13n_harness_ui.model_accounts.copilot import CopilotAccountStore
async def main():
    root = Path(sys.argv[1])
    store = CopilotAccountStore(root / "host.json")
    expected = await store.load()
    (root / (sys.argv[2] + ".ready")).touch()
    while not (root / "release").exists():
        await anyio.sleep(.01)
    async def exchange(current):
        with (root / "exchanges").open("a") as target:
            target.write("exchange\\n")
        await anyio.sleep(.05)
        return replace(current, credentials=replace(current.credentials, access_token="synthetic-rotated", refresh_token="synthetic-new-refresh"))
    result = await store.rotate(expected, exchange)
    assert result.access_token == "synthetic-rotated"
    assert (await CopilotAccountStore(store.path).load()) == result
anyio.run(main)
"""
    async with await anyio.open_process([sys.executable, "-c", code, str(tmp_path), "one"]) as first:
        async with await anyio.open_process([sys.executable, "-c", code, str(tmp_path), "two"]) as second:
            with anyio.fail_after(30):
                while not all((tmp_path / f"{name}.ready").exists() for name in ("one", "two")):
                    await anyio.sleep(0.02)
                (tmp_path / "release").touch()
                assert await first.wait() == 0
                assert await second.wait() == 0
    assert (tmp_path / "exchanges").read_text().splitlines() == ["exchange"]
    assert (await store.load()).access_token == "synthetic-rotated"


async def test_native_login_cannot_undo_concurrent_shared_logout(tmp_path):
    cli = cli_file(tmp_path)
    store = CopilotAccountStore(tmp_path / "host.json", cli_path=cli)
    assert (await store.inspect()).usable

    async def authorize(_):
        await store.logout()
        return grant("new")

    with pytest.raises(AccountStoreConflictError):
        await store.login(authorize)
    assert not (await store.inspect()).usable
    assert (await store.inspect()).shared_with_cli


async def test_copilot_http_source_actions_and_catalog_are_explicit(tmp_path, monkeypatch):
    import httpx
    from a13n_harness.providers.model import oauth
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.model_accounts.models import AccountSelection
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.webui import create_webui

    cli = cli_file(tmp_path)
    monkeypatch.setenv("COPILOT_HOME", str(cli.parent))
    root = tmp_path / "data"
    store = CopilotAccountStore(root / "oauth" / "copilot.json")
    await login(store, grant())
    await store.select(AccountSelection(source="copilot_cli_file", account_id="test-user"))
    discoveries = []

    async def discover(*, credential_source):
        discoveries.append((await credential_source.load()).source_id)
        return ("synthetic-chat-model",)

    monkeypatch.setattr(oauth, "discover_copilot_models", discover)
    config = tmp_path / "config.yaml"
    server = create_webui(
        lambda: open_harness_ui_app(
            HarnessUiSettings(storage=StorageSettings(data_root=root), pricing_auto_update=False),
            configuration_path=config,
        ),
        api_key=None,
    )
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://localhost") as client,
    ):
        choices = (await client.get("/api/models/choices")).json()
        copilot = next(item for item in choices["connections"] if item["authentication"] == "copilot_subscription")
        assert copilot["account"]["source_selection"] and copilot["models"] == []
        account = (await client.get("/api/auth/accounts/copilot")).json()
        assert account["account_id"] == "test-user" and account["shared_with_cli"]
        candidates = await client.get("/api/auth/accounts/copilot/sources")
        assert len(candidates.json()) == 3
        assert "synthetic-" not in candidates.text
        assert discoveries == []
        discovered = await client.post("/api/auth/accounts/copilot/models")
        assert discovered.status_code == 200 and discovered.json()[0]["value"] == "synthetic-chat-model"
        assert len(discoveries) == 1
        selected = await client.put(
            "/api/auth/accounts/copilot/selection", json={"source": "native", "account_id": "test-user"}
        )
        assert selected.status_code == 200 and not selected.json()["shared_with_cli"]
        rejected = await client.put(
            "/api/auth/accounts/copilot/selection",
            json={"source": "native", "account_id": "test-user", "path": "/untrusted"},
        )
        assert rejected.status_code >= 400
        assert (await client.delete("/api/auth/accounts/copilot")).json() is True
        assert not (await client.get("/api/auth/accounts/copilot")).json()["usable"]
        assert not config.exists()
        assert "synthetic-legacy" in cli.read_text()
