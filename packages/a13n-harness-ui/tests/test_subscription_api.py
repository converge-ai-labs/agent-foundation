"""Native subscription actions share the App boundary and never discover CLI stores."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from a13n_harness.providers.model.oauth import CodexLoginResult
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_accounts.usage import CodexUsage, ResetResult
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.webui import create_webui

from .test_model_accounts import _codex_credential, _jwt

pytestmark = pytest.mark.anyio


async def test_native_account_http_lifecycle_and_explicit_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    apps = []
    reads = []
    resets = []

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(settings, configuration_path=tmp_path / "config.yaml") as app:

            async def read_usage(account):
                reads.append(account.id)
                return CodexUsage(account_id=account.upstream_account_id or "unset", plan_type="plus")

            app.subscription_usage._reader = read_usage
            apps.append(app)
            yield app

    server = create_webui(factory, api_key="subscriptions-test")
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server),
            base_url="http://localhost",
            headers={"Authorization": "Bearer subscriptions-test"},
        ) as client,
    ):
        app = apps[0]
        initial = await client.get("/api/subscriptions")
        assert initial.status_code == 200
        assert len(initial.json()["accounts"]) == 2
        assert reads == [], "Listing must only read cached usage"
        created = await client.post("/api/subscriptions/accounts", json={"provider": "codex", "name": "Work"})
        assert created.status_code == 200, created.text
        identity = created.json()["id"]
        path = f"/api/subscriptions/accounts/{identity}"

        async def login(request):
            return CodexLoginResult(
                credentials=_codex_credential(
                    account_id="upstream-work", marker="fresh", expires_at=datetime.now(UTC) + timedelta(hours=1)
                ),
                id_token=_jwt(account_id="upstream-work", expires_at=datetime.now(UTC) + timedelta(hours=1)),
            )

        await app.subscription_accounts.login_codex(identity, login)
        paused = await client.patch(path, json={"name": "Work paused", "enabled": False})
        assert paused.status_code == 200 and not paused.json()["enabled"]
        refreshed = await client.post(path + "/refresh")
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["usage"]["account_id"] == "upstream-work"
        assert reads == [identity], "Manual refresh also works for a paused account"
        catalog = (await client.get("/api/subscriptions")).json()
        view = next(item for item in catalog["accounts"] if item["account"]["id"] == identity)
        assert not view["authentication"]["shared_with_cli"]
        assert "refresh-fresh" not in str(catalog)

        async def redeem(self, request):
            resets.append(request)
            return ResetResult(code="reset", windows_reset=1)

        monkeypatch.setattr("a13n_harness_ui.app.CodexUsageClient.redeem", redeem)
        request = {"account_id": "wrong-account", "credit_id": "voucher-one", "redeem_request_id": str(uuid4())}
        rejected = await client.post(path + "/reset", json=request)
        assert rejected.status_code == 400
        assert resets == []
        request["account_id"] = "upstream-work"
        confirmed = await client.post(path + "/reset", json=request)
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["code"] == "reset" and len(resets) == 1
        assert len(reads) == 2
        assert (await client.get(path)).json()["account_id"] == "upstream-work"

        async def unavailable_read(self, **kwargs):
            raise ValueError("Codex account API could not confirm the outcome.")

        async def unavailable_reset(self, request):
            raise ValueError("Reset outcome is unconfirmed; preserve the redemption ID.")

        monkeypatch.setattr("a13n_harness_ui.app.CodexUsageClient.read", unavailable_read)
        monkeypatch.setattr("a13n_harness_ui.app.CodexUsageClient.redeem", unavailable_reset)
        assert (await client.get(path + "/reset-vouchers")).status_code == 400
        failed_reset = await client.post(path + "/reset", json=request)
        assert failed_reset.status_code == 400
        assert "unconfirmed" in failed_reset.text
        assert len(reads) == 2
        logged_out = await client.post(path + "/logout")
        assert logged_out.status_code == 200
        assert (await client.delete(path)).status_code == 200
        assert (await client.delete(path)).status_code == 200
        assert all(
            item["account"]["id"] != identity for item in (await client.get("/api/subscriptions")).json()["accounts"]
        )
        assert (await client.post(path + "/refresh")).status_code >= 400


async def test_deleted_default_group_is_captured_as_empty_not_historical(tmp_path: Path):
    import json

    from a13n_harness_ui.composition.resolver import AgentCompositionResolver
    from a13n_harness_ui.configuration.loader import load_harness_ui_configuration

    config = tmp_path / "config.yaml"
    config.write_text('schema_version: "1"\n')
    models = tmp_path / "models"
    models.mkdir()
    (models / "codex.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "kind": "model",
                "id": "model-codex",
                "name": "Codex",
                "route": "openai-codex:gpt-5.4",
                "authentication": {"kind": "codex_subscription"},
            }
        )
    )
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        assert (await app.subscriptions()).groups[0].id == "codex-default"
    (tmp_path / "subscription_groups" / "codex-default.yaml").unlink()
    async with open_harness_ui_app(settings, configuration_path=config):
        source = await load_harness_ui_configuration(config)
        recipe = AgentCompositionResolver().model_recipe(source.models["model-codex"], source)
        assert recipe.subscription_group is not None
        assert recipe.subscription_group.accounts == ()
        assert not (tmp_path / "subscription_groups" / "codex-default.yaml").exists()


@pytest.mark.parametrize("via_mutation", [False, True])
async def test_first_subscription_model_publishes_native_default_without_restart(tmp_path: Path, via_mutation: bool):
    config = tmp_path / "config.yaml"
    config.write_text('schema_version: "1"\n')
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        assert not (await app.subscriptions()).groups
        models = tmp_path / "models"
        models.mkdir()
        (models / "codex.yaml").write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "kind": "model",
                    "id": "model-codex",
                    "name": "Codex",
                    "route": "openai-codex:gpt-5.4",
                    "authentication": {"kind": "codex_subscription"},
                }
            )
        )
        if via_mutation:
            from a13n_harness_ui.configuration.mutation import ResourceMutationRequest

            content = (models / "codex.yaml").read_text()
            (models / "codex.yaml").unlink()
            result = await app.mutate_configuration(
                relative_path="models/codex.yaml", request=ResourceMutationRequest(content=content)
            )
            assert "codex-default" in result.configuration.subscription_groups
        else:
            await app.reload_configuration()
        assert (await app.subscriptions()).groups[0].accounts[0].account == "acct-codex-default"
        (tmp_path / "subscription_groups" / "codex-default.yaml").unlink()
        await app.reload_configuration()
        assert not (await app.subscriptions()).groups


async def test_main_account_survives_real_run_and_app_restart(tmp_path, monkeypatch):
    import yaml
    from a13n_harness_ui.model_accounts.routing import SubscriptionGroupModel
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.models.function import FunctionModel

    from .test_app import _write_configuration
    from .test_subscription_routing import _accounts

    root = _write_configuration(tmp_path)
    model_path = tmp_path / "models/primary.yaml"
    model_value = yaml.safe_load(model_path.read_text())
    model_value.update(
        route="openai-codex:gpt-5.4", authentication={"kind": "codex_subscription", "group": "codex-test"}
    )
    model_path.write_text(yaml.safe_dump(model_value))
    accounts, identities, group = await _accounts(tmp_path / "data")
    directory = tmp_path / "subscription_groups"
    directory.mkdir()
    (directory / "pool.yaml").write_text(group.model_dump_json())

    async def stream(messages, info):
        yield "Done"

    async def resolve(self, context, model_id):
        recipe = self._recipes[model_id]
        return SubscriptionGroupModel(
            recipe.subscription_group, accounts, usage, lambda account: FunctionModel(stream_function=stream)
        )

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        usage = app.subscription_usage
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed, operation
        assert operation.main_account.account_id == identities[0].id
        assert operation.main_account.model_id == "model-primary"
        assert (await app.get_thread(thread.thread_id)).thread.last_execution.main_account == operation.main_account
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        saved = (await app.get_thread(thread.thread_id)).thread.last_execution.main_account
        assert saved == operation.main_account


async def test_live_selection_observes_pause_exhaustion_and_deleted_default(tmp_path):
    from a13n_harness.errors import ModelResolutionError
    from a13n_harness_ui.configuration import load_harness_ui_configuration

    from .test_live_mode import live_configuration
    from .test_subscription_routing import _accounts

    root = live_configuration(tmp_path)
    accounts, identities, group = await _accounts(tmp_path / "data")
    group = group.model_copy(update={"id": "codex-default"})
    directory = tmp_path / "subscription_groups"
    directory.mkdir()
    group_path = directory / "custom-default.yaml"
    original = group.model_dump_json()
    group_path.write_text(original)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert group_path.read_text() == original
        assert not (directory / "codex-default.yaml").exists()
        source = await load_harness_ui_configuration(root)
        await accounts.update(identities[0].id, enabled=False)
        _, selected = await app._voice_model(source)
        assert selected == identities[1].id
        await app.subscription_usage.reject(identities[1], "gpt-5")
        with pytest.raises(ModelResolutionError):
            await app._voice_model(source)
        group_path.unlink()
        await app.reload_configuration()
        source = await load_harness_ui_configuration(root)
        with pytest.raises(ModelResolutionError):
            await app._voice_model(source)
        assert not (directory / "codex-default.yaml").exists()
