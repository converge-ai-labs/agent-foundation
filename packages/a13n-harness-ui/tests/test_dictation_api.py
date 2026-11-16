import json
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.dictation import DictationResult
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

from .test_dictation import recording
from .test_subscription_routing import _accounts

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@pytest.mark.parametrize("selection", ["account", "group", "default"])
async def test_explicit_subscription_source_without_model_resources(tmp_path: Path, monkeypatch, selection):
    data = tmp_path / "data"
    catalog, accounts, group = await _accounts(data)
    config = tmp_path / "config.yaml"
    options = {"model": "gpt-5.6-luna"}
    if selection == "account":
        options["account"] = accounts[1].id
    elif selection == "group":
        options["group"] = group.id
        directory = tmp_path / "subscription_groups"
        directory.mkdir()
        (directory / "test.yaml").write_text(group.model_dump_json())
    config.write_text(json.dumps({"schema_version": "1", "dictation": {"backend": "codex", "codex": options}}))
    captured = []

    async def transcribe(request, selected_group, selected_accounts, usage, model_name, **kwargs):
        captured.append((tuple(member.account for member in selected_group.accounts), model_name))
        return DictationResult(text="Insert this.")

    monkeypatch.setattr("a13n_harness_ui.app.transcribe_subscription", transcribe)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=data), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        if selection == "default":
            # The default group refers to the native default login, not every account.
            native = app.subscription_accounts.store(await app.subscription_accounts.get("acct-codex-default"))
            native.policy.path.parent.mkdir(parents=True, exist_ok=True)
            native.policy.path.write_bytes(catalog.store(accounts[0]).policy.path.read_bytes())
        assert (await app.transcribe(recording())).text == "Insert this."
        assert len(captured) == 1 and captured[0][1] == "gpt-5.6-luna"
        if selection == "account":
            assert captured[0][0] == (accounts[1].id,)
        elif selection == "group":
            assert captured[0][0] == tuple(account.id for account in accounts)
        else:
            assert captured[0][0] == ("acct-codex-default",)
        assert not (tmp_path / "models").exists()
        if selection == "default":
            assert any(g.id == "codex-default" for g in (await app.subscriptions()).groups)


async def test_login_does_not_enable_dictation(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    await _accounts(data)
    config = tmp_path / "config.yaml"
    config.write_text('schema_version: "1"\n')

    async def unexpected(*args, **kwargs):
        pytest.fail("Login alone must not activate the Codex backend")

    monkeypatch.setattr("a13n_harness_ui.app.transcribe_subscription", unexpected)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=data), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        with pytest.raises(HarnessUiError) as error:
            await app.transcribe(recording())
        assert error.value.code == "dictation_unconfigured"


async def test_deleted_default_group_is_not_recreated_for_dictation(tmp_path: Path):
    await _accounts(tmp_path / "data")
    config = tmp_path / "config.yaml"
    config.write_text('schema_version: "1"\ndictation:\n  backend: codex\n')
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config):
        pass
    default = tmp_path / "subscription_groups" / "codex-default.yaml"
    default.unlink()
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        with pytest.raises(HarnessUiError) as error:
            await app.transcribe(recording())
        assert error.value.code == "dictation_unconfigured"
        assert not default.exists()


@pytest.mark.parametrize("unavailable", ["signed_out", "disabled", "exhausted"])
async def test_unavailable_account_has_structured_error(tmp_path: Path, monkeypatch, unavailable):
    from a13n_harness.models.codex_dictation import CodexDictationError

    data = tmp_path / "data"
    catalog, accounts, _ = await _accounts(data)
    account = accounts[0]
    if unavailable == "signed_out":
        catalog.store(account).policy.path.unlink()
    elif unavailable == "disabled":
        await catalog.update(account.id, enabled=False)
    config = tmp_path / "config.yaml"
    config.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "dictation": {"backend": "codex", "codex": {"account": account.id}},
            }
        )
    )
    calls = []

    async def rejected(*args, **kwargs):
        calls.append(True)
        raise CodexDictationError("rate limited", status_code=429)

    monkeypatch.setattr("a13n_harness.models.codex_dictation.transcribe", rejected)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=data), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        with pytest.raises(HarnessUiError) as error:
            await app.transcribe(recording())
        assert error.value.code == "subscription_group_unavailable"
        assert len(calls) == (1 if unavailable == "exhausted" else 0)
