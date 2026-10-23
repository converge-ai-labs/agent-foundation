from __future__ import annotations

import json
import stat
from datetime import timedelta
from pathlib import Path

import pytest
from a13n_harness_ui.configuration.loader import load_harness_ui_configuration
from a13n_harness_ui.configuration.models import CodexSubscriptionAuthentication, SubscriptionGroupResource
from a13n_harness_ui.model_accounts import AccountStoreError, CodexAccountStore, GrokAccountStore
from a13n_harness_ui.model_accounts.managed import SubscriptionAccounts
from anyio import create_task_group
from pydantic import ValidationError

from .test_model_accounts import _GROK_SCOPE, _NOW, _codex_tokens, _grok_entry

pytestmark = pytest.mark.anyio


def _external(home: Path) -> tuple[Path, Path]:
    codex = home / ".codex" / "auth.json"
    grok = home / ".grok" / "auth.json"
    codex.parent.mkdir(parents=True)
    grok.parent.mkdir(parents=True)
    codex.write_text(
        json.dumps(
            {"tokens": _codex_tokens(account_id="upstream-codex", expires_at=_NOW + timedelta(hours=1), marker="one")}
        )
    )
    grok.write_text(
        json.dumps(
            {
                _GROK_SCOPE: _grok_entry(
                    account_id="upstream-grok", expires_at=_NOW + timedelta(hours=1), marker="one"
                ),
                "unrelated": {"secret": "do-not-copy"},
            }
        )
    )
    return codex, grok


async def test_adoption_is_private_once_and_never_changes_cli_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    codex, grok = _external(home)
    originals = codex.read_bytes(), grok.read_bytes()
    accounts = SubscriptionAccounts(tmp_path / "data")
    await accounts.adopt_once(tmp_path / "config", environ={}, user_home=home, grok_scope=_GROK_SCOPE)
    native_codex = accounts.store(await accounts.get("acct-codex-default"))
    native_grok = accounts.store(await accounts.get("acct-grok-default"))
    assert isinstance(native_codex, CodexAccountStore)
    assert isinstance(native_grok, GrokAccountStore)
    assert (await native_codex.load()).account_id == "upstream-codex"
    assert (await native_grok.load()).account_id == "upstream-grok"
    assert not (await native_codex.inspect()).shared_with_cli
    assert not (await native_grok.inspect()).shared_with_cli
    assert "do-not-copy" not in native_grok.policy.path.read_text()
    assert stat.S_IMODE(native_codex.policy.path.stat().st_mode) == 0o600
    assert "refresh-one" not in accounts.path.read_text()

    await native_codex.logout()
    await accounts.delete("acct-grok-default")
    assert (codex.read_bytes(), grok.read_bytes()) == originals

    async def forbidden(*args, **kwargs):
        pytest.fail("External store must not be discovered after adoption")

    monkeypatch.setattr("a13n_harness_ui.model_accounts.managed.resolve_codex_policy", forbidden)
    await SubscriptionAccounts(tmp_path / "data").adopt_once(tmp_path / "config", environ={}, user_home=home)
    with pytest.raises(AccountStoreError, match="login"):
        await native_codex.load()
    with pytest.raises(AccountStoreError, match="unavailable"):
        await accounts.get("acct-grok-default")


@pytest.mark.parametrize("provider", ["codex", "grok"])
async def test_adoption_binds_exported_identity_after_external_rotation(tmp_path, monkeypatch, provider):
    home = tmp_path / "home"
    paths = dict(zip(("codex", "grok"), _external(home), strict=True))
    store_type = CodexAccountStore if provider == "codex" else GrokAccountStore
    original_export = store_type.export_login

    async def rotate_after_export(store):
        document = await original_export(store)
        path = paths[provider]
        path.write_text(path.read_text().replace(f"upstream-{provider}", "different-account"))
        return document

    monkeypatch.setattr(store_type, "export_login", rotate_after_export)
    catalog = SubscriptionAccounts(tmp_path / "data")
    await catalog.adopt_once(None, environ={}, user_home=home, grok_scope=_GROK_SCOPE)
    account = await catalog.get(f"acct-{provider}-default")
    assert account.upstream_account_id == f"upstream-{provider}"
    assert (await catalog.store(account).load()).account_id == account.upstream_account_id
    assert "different-account" in paths[provider].read_text()


@pytest.mark.parametrize("provider", ["codex", "grok"])
@pytest.mark.parametrize("external_state", ["rotated", "absent"])
async def test_adoption_recovers_published_identity_before_catalog(tmp_path, monkeypatch, provider, external_state):
    from a13n_harness_ui.model_accounts import managed

    home = tmp_path / "home"
    paths = dict(zip(("codex", "grok"), _external(home), strict=True))
    catalog = SubscriptionAccounts(tmp_path / "data")
    original_write = managed.write_json_if_unchanged

    async def crash_before_catalog(path, document, *args, **kwargs):
        if path == catalog.path and provider in document["adoption"]:
            raise RuntimeError("Simulated crash after credential publication")
        await original_write(path, document, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(managed, "write_json_if_unchanged", crash_before_catalog)
        with pytest.raises(RuntimeError, match="Simulated crash"):
            await catalog.adopt_once(None, environ={}, user_home=home, grok_scope=_GROK_SCOPE)
    native_path = catalog.root / "accounts" / f"acct-{provider}-default.json"
    native_bytes = native_path.read_bytes()
    if external_state == "absent":
        paths[provider].unlink()
    else:
        paths[provider].write_text(paths[provider].read_text().replace(f"upstream-{provider}", "different-account"))
    # Recovery must retain the original Grok scope even when bootstrap input changes.
    await catalog.adopt_once(None, environ={}, user_home=home, grok_scope="https://other.test::other-client")
    account = await catalog.get(f"acct-{provider}-default")
    assert account.upstream_account_id == f"upstream-{provider}"
    assert (await catalog.store(account).load()).account_id == account.upstream_account_id
    assert native_path.read_bytes() == native_bytes
    assert (await catalog.adoption())[provider].status == "adopted"
    if provider == "grok":
        assert account.scope == _GROK_SCOPE


async def test_absent_and_unsupported_are_terminal_adoption_results(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('cli_auth_credentials_store = "keyring"')
    accounts = SubscriptionAccounts(tmp_path / "data")
    await accounts.adopt_once(None, environ={}, user_home=home)
    results = await accounts.adoption()
    assert results["codex"].status == "unsupported"
    assert results["grok"].status == "absent"
    (home / ".codex" / "config.toml").unlink()
    (home / ".codex" / "auth.json").write_text(
        json.dumps({"tokens": _codex_tokens(account_id="later", expires_at=_NOW, marker="later")})
    )
    await accounts.adopt_once(None, environ={}, user_home=home)
    assert (await accounts.adoption())["codex"].status == "unsupported"
    with pytest.raises(AccountStoreError):
        await accounts.store(await accounts.get("acct-codex-default")).load()


async def test_concurrent_adoption_and_group_publication_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _external(home)
    data = tmp_path / "data"

    async def adopt():
        await SubscriptionAccounts(data).adopt_once(None, environ={}, user_home=home, grok_scope=_GROK_SCOPE)

    async with create_task_group() as group:
        group.start_soon(adopt)
        group.start_soon(adopt)
    accounts = SubscriptionAccounts(data)
    assert len(await accounts.list()) == 2

    async def forbidden(*args, **kwargs):
        pytest.fail("Finishing group publication must not reimport credentials")

    monkeypatch.setattr("a13n_harness_ui.model_accounts.managed.resolve_codex_policy", forbidden)
    config = tmp_path / "config"
    await accounts.adopt_once(config)
    path = config / "subscription_groups" / "codex-default.yaml"
    document = json.loads(path.read_text())
    assert document["accounts"] == [{"account": "acct-codex-default", "weight": 1}]
    path.unlink()
    await accounts.adopt_once(config)
    assert not path.exists(), "Deleting a group must not recreate it on startup"


async def test_existing_default_group_is_not_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "config"
    target = root / "subscription_groups" / "codex-default.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("user-authored")
    await SubscriptionAccounts(tmp_path / "data").adopt_once(root, environ={}, user_home=tmp_path / "home")
    assert target.read_text() == "user-authored"


async def test_account_updates_are_visible_between_instances(tmp_path: Path) -> None:
    first = SubscriptionAccounts(tmp_path)
    second = SubscriptionAccounts(tmp_path)
    account = await first.create("codex", "Work")
    await second.update(account.id, enabled=False, name="Paused")
    assert (await first.get(account.id)).name == "Paused"
    with pytest.raises(AccountStoreError):
        await first.get(account.id, available=True)
    await second.update(account.id, enabled=True)
    assert (await first.get(account.id, available=True)).enabled
    await first.delete(account.id)
    assert not await second.list()


async def test_group_configuration_is_pure_and_validates_provider(tmp_path: Path) -> None:
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "1"\n')
    (tmp_path / "models").mkdir()
    (tmp_path / "subscription_groups").mkdir()
    group = SubscriptionGroupResource(
        schema_version="1", kind="subscription_group", id="codex-daily", name="Daily", provider="codex"
    )
    (tmp_path / "subscription_groups" / "daily.yaml").write_text(group.model_dump_json())
    model = {
        "schema_version": "1",
        "kind": "model",
        "id": "model-codex",
        "name": "Codex",
        "route": "openai-codex:gpt-5.4",
        "authentication": {"kind": "codex_subscription", "group": "codex-daily"},
    }
    (tmp_path / "models" / "codex.yaml").write_text(json.dumps(model))
    loaded = await load_harness_ui_configuration(root)
    assert loaded.subscription_groups["codex-daily"].accounts == ()
    assert not (tmp_path / "oauth").exists()
    model["authentication"]["kind"] = "grok_subscription"
    (tmp_path / "models" / "codex.yaml").write_text(json.dumps(model))
    with pytest.raises(Exception, match="references are invalid"):
        await load_harness_ui_configuration(root)


@pytest.mark.parametrize("provider", ["codex", "grok"])
async def test_implicit_default_group_validates_provider_and_allows_bootstrap(tmp_path, provider):
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "1"\n')
    (tmp_path / "models").mkdir()
    model = {
        "schema_version": "1",
        "kind": "model",
        "id": "model-test",
        "name": "Test",
        "route": "openai-codex:gpt-5.4" if provider == "codex" else "grok:grok-code-fast-1",
        "authentication": {"kind": f"{provider}_subscription"},
    }
    model_path = tmp_path / "models" / "test.yaml"
    model_path.write_text(json.dumps(model))
    loaded = await load_harness_ui_configuration(root)
    assert not loaded.subscription_groups
    assert not (tmp_path / "oauth").exists()
    group_path = tmp_path / "subscription_groups" / "default.yaml"
    group_path.parent.mkdir()
    group = SubscriptionGroupResource(
        schema_version="1", kind="subscription_group", id=f"{provider}-default", name="Default", provider=provider
    )
    group_path.write_text(group.model_dump_json())
    await load_harness_ui_configuration(root)
    group_path.write_text(
        group.model_copy(update={"provider": "grok" if provider == "codex" else "codex"}).model_dump_json()
    )
    with pytest.raises(Exception, match="references are invalid"):
        await load_harness_ui_configuration(root)
    group_path.unlink()
    model["authentication"]["group"] = f"{provider}-default"
    model_path.write_text(json.dumps(model))
    with pytest.raises(Exception, match="references are invalid"):
        await load_harness_ui_configuration(root)


async def test_legacy_auth_serialization_and_group_member_constraints() -> None:
    auth = CodexSubscriptionAuthentication(kind="codex_subscription")
    assert auth.model_dump(mode="json") == {"kind": "codex_subscription"}
    with pytest.raises(ValidationError, match="unique"):
        SubscriptionGroupResource.model_validate(
            {
                "schema_version": "1",
                "kind": "subscription_group",
                "id": "codex-daily",
                "name": "Daily",
                "provider": "codex",
                "accounts": [{"account": "acct-one"}, {"account": "acct-one"}],
            }
        )


async def test_managed_login_binds_identity_across_logout(tmp_path: Path) -> None:
    from a13n_harness.providers.model.oauth import CodexLoginResult

    from .test_model_accounts import _codex_credential, _jwt

    catalog = SubscriptionAccounts(tmp_path)
    account = await catalog.create("codex", "Personal")
    identity = ["first"]

    async def login(request):
        return CodexLoginResult(
            credentials=_codex_credential(
                account_id=identity[0], expires_at=_NOW + timedelta(days=3650), marker="login"
            ),
            id_token=_jwt(expires_at=_NOW + timedelta(days=3650)),
        )

    await catalog.login_codex(account.id, login)
    assert (await catalog.get(account.id)).upstream_account_id == "first"
    await catalog.logout(account.id)
    identity[0] = "second"
    with pytest.raises(AccountStoreError, match="same account"):
        await catalog.login_codex(account.id, login)
    identity[0] = "first"
    await catalog.login_codex(account.id, login)
    assert (await catalog.get(account.id)).login_revision == 3


@pytest.mark.parametrize("mutation", ["logout", "delete"])
async def test_late_login_cannot_restore_removed_credentials(tmp_path: Path, mutation: str) -> None:
    from a13n_harness.providers.model.oauth import CodexLoginResult

    from .test_model_accounts import _codex_credential, _jwt

    catalog = SubscriptionAccounts(tmp_path)
    account = await catalog.create("codex", "Personal")

    async def login(request):
        if mutation == "delete":
            await catalog.delete(account.id)
        else:
            await catalog.logout(account.id)
        return CodexLoginResult(
            credentials=_codex_credential(account_id="first", expires_at=_NOW + timedelta(days=3650), marker="login"),
            id_token=_jwt(expires_at=_NOW + timedelta(days=3650)),
        )

    with pytest.raises(AccountStoreError):
        await catalog.login_codex(account.id, login)
    assert (await catalog.store(account).inspect()).account_id is None
    if mutation == "delete":
        await catalog.delete(account.id)  # Cleanup is idempotent after a tombstone.
