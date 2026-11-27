"""Exercise trusted plugin admission and the Host-owned OAuth runtime."""

import sys
from collections.abc import AsyncIterator, Mapping
from importlib.metadata import EntryPoint
from types import ModuleType
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
from a13n_harness.configuration import RunConfiguration
from a13n_harness_ui.app import HarnessUiIntegrations, open_harness_ui_app
from a13n_harness_ui.model_accounts.login import LoginRequest
from a13n_harness_ui.model_accounts.models import AccountStoreConflictError
from a13n_harness_ui.model_authoring import ModelOptionsRequest, ModelRecipeRequest
from a13n_harness_ui.model_providers import (
    AccountContext,
    AuthorizedAccount,
    BuildModelContext,
    CallbackChallenge,
    DeviceChallenge,
    LoopbackCallbackSpec,
    ModelProviderDeclaration,
    ModelProviderPlugin,
    OAuthContext,
    PluginModelSuggestion,
)
from a13n_harness_ui.model_providers.catalog import ModelProviderCatalog
from a13n_harness_ui.model_providers.credentials import PluginAccountStore, PluginCredentialSource
from a13n_harness_ui.model_providers.transport import provider_http_client
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from anyio import create_task_group, fail_after, sleep
from pydantic import BaseModel, Field
from pydantic_ai.models.test import TestModel

pytestmark = pytest.mark.anyio


class ExampleSettings(BaseModel):
    workspace: str = Field(default="team", title="Workspace")


class ExampleProvider(ModelProviderPlugin):
    configuration_model = ExampleSettings
    refreshes = 0

    def declaration(self) -> ModelProviderDeclaration:
        return ModelProviderDeclaration(
            provider_key="example-oauth",
            display_name="Example Cloud",
            allowed_origins=("https://model.example.test",),
            login_methods=("device", "browser", "manual_callback"),
            loopback_callback=LoopbackCallbackSpec(),
            supports_model_discovery=True,
            models=(PluginModelSuggestion(model_id="example-text", label="Example Text"),),
            default_model="example-text",
        )

    async def start_device_login(self, ctx: OAuthContext) -> DeviceChallenge:
        return DeviceChallenge(
            verification_url="https://model.example.test/activate",
            user_code="TEST-0000",
            expires_in=30,
            poll_interval=1,
            state={"internal": "not projected"},
        )

    async def poll_device_login(self, ctx: OAuthContext, challenge: DeviceChallenge) -> AuthorizedAccount:
        assert challenge.state == {"internal": "not projected"}
        return AuthorizedAccount(upstream_account_id="team", credentials={"access_token": "example-initial"})

    async def begin_callback_login(self, ctx: OAuthContext) -> CallbackChallenge:
        assert ctx.pkce and ctx.redirect_uri
        query = urlencode(
            {
                "state": ctx.pkce.state,
                "redirect_uri": ctx.redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": ctx.pkce.challenge_s256,
            }
        )
        return CallbackChallenge(
            authorization_url=f"https://model.example.test/authorize?{query}", expires_in=30, state={}
        )

    async def complete_callback_login(
        self, ctx: OAuthContext, challenge: CallbackChallenge, callback_params: Mapping[str, str]
    ) -> AuthorizedAccount:
        assert ctx.pkce and callback_params["code"] == "example-grant"
        return AuthorizedAccount(upstream_account_id="team", credentials={"access_token": "example-initial"})

    async def refresh_credentials(self, ctx: OAuthContext, current: AuthorizedAccount) -> AuthorizedAccount:
        self.refreshes += 1
        await sleep(0.02)
        return current.model_copy(update={"credentials": {"access_token": "example-renewed"}})

    def build_model(self, ctx: BuildModelContext) -> TestModel:
        return TestModel(custom_output_text="Response from Example Cloud")

    async def discover_models(self, ctx: AccountContext) -> tuple[PluginModelSuggestion, ...]:
        return (PluginModelSuggestion(model_id="discovered", label="Discovered Model", base_model="openai:gpt-5.4"),)


def test_entry_points_and_explicit_plugins_share_registration(monkeypatch):
    module = ModuleType("example_oauth_test_plugin")
    module.ExampleProvider = ExampleProvider
    monkeypatch.setitem(sys.modules, module.__name__, module)
    group = "a13n_harness_ui.model_providers"
    point = EntryPoint(name="example-oauth", value=f"{module.__name__}:ExampleProvider", group=group)
    broken = EntryPoint(name="broken", value=f"{module.__name__}:MissingProvider", group=group)
    installed = ModelProviderCatalog(installed=(point, broken))
    embedded = ModelProviderCatalog((ExampleProvider(),), installed=())
    assert installed.registrations[point.name].declaration == embedded.registrations[point.name].declaration
    assert (
        installed.registrations[point.name].configuration_schema
        == embedded.registrations[point.name].configuration_schema
    )
    assert len(installed.diagnostics) == 1
    conflicts = ModelProviderCatalog((ExampleProvider(),), installed=(point, broken))
    assert not conflicts.registrations
    assert len(conflicts.diagnostics) == 2


@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize("method", ["device", "browser", "manual_callback"])
async def test_plugin_login_and_discovery(tmp_path, method):
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(
        settings,
        configuration_path=tmp_path / "config.yaml",
        integrations=HarnessUiIntegrations(model_providers=(ExampleProvider(),)),
    ) as app:
        choices = await app.model_choices()
        connection = next(item for item in choices.connections if item.id == "example-oauth")
        assert connection.account and connection.account.managed_accounts
        request = LoginRequest.model_validate({"provider": connection.id, "method": method})
        status = await app.start_login(request)
        with fail_after(8):
            while status.state == "starting":
                await sleep(0.02)
                status = await app.login_status(status.session_id)
            assert status.state == "waiting", status
            assert status.verification_url
            if method != "device":
                values = parse_qs(urlsplit(status.verification_url).query)
                redirect = values["redirect_uri"][0]
                state = values["state"][0]
                with pytest.raises(Exception, match="callback"):
                    await app.submit_login_callback(status.session_id, f"{redirect}?code=wrong&state=wrong")
                callback = f"{redirect}?code=example-grant&state={state}"
                if method == "browser":
                    async with httpx2.AsyncClient() as client:
                        response = await client.get(callback)
                        assert response.status_code == 200
                else:
                    await app.submit_login_callback(status.session_id, callback)
            while status.state == "waiting":
                await sleep(0.02)
                status = await app.login_status(status.session_id)
            assert status.state == "succeeded", status
        assert (tmp_path / "data/oauth/accounts/acct-example-oauth-default.json").stat().st_mode & 0o777 == 0o600
        models = await app.discover_account_models(connection.id)
        assert models[0].base_model == "openai:gpt-5.4"
        options = await app.model_options(ModelOptionsRequest(connection=connection.id, model_id="discovered"))
        assert options.base_model == "openai:gpt-5.4"
        assert options.known_context_window
        assert options.characteristics.capabilities
        assert options.pricing is not None and options.pricing.key == "openai:gpt-5.4"
        recipe = await app.prepare_model(ModelRecipeRequest(connection=connection.id, model_id="discovered"))
        assert recipe.base_model == "openai:gpt-5.4"
        assert recipe.authentication.kind == "plugin_subscription"


class StreamingResponse(httpx2.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"upstream response"


async def test_exact_origin_and_concurrent_401_refresh(tmp_path):
    plugin = ExampleProvider()
    registration = ModelProviderCatalog((plugin,), installed=()).registrations["example-oauth"]
    store = PluginAccountStore(tmp_path, provider="example-oauth", account_id="acct-example-oauth-default")
    await store.write(
        AuthorizedAccount(upstream_account_id="team", credentials={"access_token": "example-initial"}), None
    )
    snapshot = await store.read()
    source = PluginCredentialSource(store, registration, expected_generation=snapshot.generation)
    received = []

    async def upstream(request):
        token = request.headers["authorization"]
        received.append(token)
        await sleep(0.03)
        return httpx2.Response(401 if token.endswith("initial") else 200, stream=StreamingResponse())

    async with provider_http_client(registration.declaration, transport=httpx2.MockTransport(upstream)) as client:

        async def ask():
            response = await client.post(
                "https://model.example.test/infer", json={"prompt": "hello"}, auth=source.bearer_auth({})
            )
            assert response.status_code == 200

        async with create_task_group() as tasks:
            tasks.start_soon(ask)
            tasks.start_soon(ask)
        assert plugin.refreshes == 1
        assert len(received) == 4
        with pytest.raises(ValueError, match="exact"):
            await client.get("https://model.example.test:444/infer", auth=source.bearer_auth({}))
        with pytest.raises(ValueError, match="authority"):
            await client.get(
                "https://model.example.test/infer", headers={"Host": "other.example.test"}, auth=source.bearer_auth({})
            )
    async with provider_http_client(
        registration.declaration,
        RunConfiguration(allowed_hosts=("other.example.test",)),
        transport=httpx2.MockTransport(upstream),
    ) as client:
        with pytest.raises(Exception, match="allowed"):
            await client.get("https://model.example.test/infer", auth=source.bearer_auth({}))
    latest = await store.read()
    await store.write(
        AuthorizedAccount(upstream_account_id="switched", credentials={"access_token": "switched"}), latest.digest
    )
    with pytest.raises(AccountStoreConflictError):
        await source.current()


@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize("strategy", ["ordered", "weighted"])
async def test_plugin_group_routes_a_real_run(tmp_path, strategy):
    import yaml
    from a13n_harness_ui.model_accounts.managed import SubscriptionAccounts
    from a13n_harness_ui.surfaces import RootOperationStatus

    from .test_app import _write_configuration

    root = _write_configuration(tmp_path)
    model_path = tmp_path / "models/primary.yaml"
    model = yaml.safe_load(model_path.read_text())
    model.update(
        route="example-oauth:example-text",
        authentication={"kind": "plugin_subscription", "provider": "example-oauth", "group": "example-pool"},
        model_configuration={"workspace": "inference"},
    )
    model_path.write_text(yaml.safe_dump(model))
    accounts = SubscriptionAccounts(tmp_path / "data")
    first = await accounts.create("example-oauth", "Paused")
    second = await accounts.create("example-oauth", "Active")
    for account in (first, second):
        await accounts.publish_plugin_login(
            account,
            None,
            AuthorizedAccount(upstream_account_id=account.name, credentials={"access_token": "example-token"}),
            allow_account_switch=False,
        )
    await accounts.update(first.id, enabled=False)
    directory = tmp_path / "subscription_groups"
    directory.mkdir()
    (directory / "example.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "kind": "subscription_group",
                "id": "example-pool",
                "name": "Example pool",
                "strategy": strategy,
                "provider": "example-oauth",
                "accounts": [{"account": first.id, "weight": 100}, {"account": second.id, "weight": 1}],
            }
        )
    )
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(
        settings, configuration_path=root, integrations=HarnessUiIntegrations(model_providers=(ExampleProvider(),))
    ) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed, operation
        assert operation.main_account is not None
        assert operation.main_account.account_id == second.id


@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize("method", ["browser", "manual_callback"])
async def test_google_ai_plan_login_and_private_refresh(tmp_path, monkeypatch, method):
    from datetime import UTC, datetime, timedelta

    from a13n_harness.providers.model.oauth.google_ai_plan import (
        DPoPKey,
        GoogleAIPlanClient,
        GoogleAIPlanCredentials,
        GoogleAIPlanFlow,
    )
    from a13n_harness_ui.model_providers import google_ai_plan

    registration = GoogleAIPlanClient.configured("fixture-client", "fixture-secret")
    initial = GoogleAIPlanCredentials(
        account_id="fixture-subject",
        client_id=registration.client_id,
        access_token="fixture-initial-access",
        refresh_token="fixture-initial-refresh",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        dpop_jwk=DPoPKey.generate(),
        dpop_nonce="fixture-initial-nonce",
    )
    exchanges = []

    async def exchange(flow, code):
        assert flow.registration == registration
        assert flow.code_verifier and flow.nonce
        exchanges.append(code)
        return initial

    monkeypatch.setattr(GoogleAIPlanFlow, "exchange_code", exchange)
    monkeypatch.setenv("A13N_GOOGLE_AI_PLAN_CLIENT_ID", "fixture-client")
    monkeypatch.setenv("A13N_GOOGLE_AI_PLAN_CLIENT_SECRET", "fixture-secret")
    root = tmp_path / "data"
    settings = HarnessUiSettings(storage=StorageSettings(data_root=root), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=tmp_path / "config.yaml") as app:
        connection = next(item for item in (await app.model_choices()).connections if item.id == "google-ai-plan")
        assert connection.account and connection.account.managed_accounts
        request = LoginRequest.model_validate({"provider": connection.id, "method": method})
        status = await app.start_login(request)
        with fail_after(8):
            while status.state == "starting":
                await sleep(0.02)
                status = await app.login_status(status.session_id)
            assert status.state == "waiting" and status.verification_url
            fields = parse_qs(urlsplit(status.verification_url).query)
            assert fields["code_challenge_method"] == ["S256"]
            callback = (
                fields["redirect_uri"][0] + "?" + urlencode({"code": "fixture-code", "state": fields["state"][0]})
            )
            with pytest.raises(Exception, match="callback"):
                await app.submit_login_callback(status.session_id, callback + "&code=duplicate")
            if method == "browser":
                async with httpx2.AsyncClient(trust_env=False) as client:
                    assert (await client.get(callback)).status_code == 200
            else:
                await app.submit_login_callback(status.session_id, callback)
            while status.state == "waiting":
                await sleep(0.02)
                status = await app.login_status(status.session_id)
            assert status.state == "succeeded", status
        assert exchanges == ["fixture-code"]
        assert "fixture-initial" not in status.model_dump_json()
    oauth_root = root / "oauth"
    account_id = "acct-google-ai-plan-default"
    store = PluginAccountStore(oauth_root, provider="google-ai-plan", account_id=account_id)
    snapshot = await store.read()
    assert snapshot.account is not None
    assert GoogleAIPlanCredentials.model_validate(snapshot.account.credentials) == initial
    assert (oauth_root / "accounts" / f"{account_id}.json").stat().st_mode & 0o777 == 0o600
    plugin = google_ai_plan.GoogleAIPlanPlugin(registration)
    admitted = ModelProviderCatalog((plugin,), installed=()).registrations["google-ai-plan"]
    source = PluginCredentialSource(store, admitted, expected_generation=snapshot.generation)
    updates = {"refresh_token": "fixture-rotated", "dpop_nonce": "fixture-renewed-nonce"}
    refreshes = []

    async def refresh(client_registration, http, current):
        assert client_registration == registration and current == initial
        refreshes.append(current)
        await sleep(0.03)
        return current.model_copy(update=updates)

    monkeypatch.setattr(google_ai_plan, "refresh_google_ai_plan", refresh)
    _, digest = await source.current()
    async with create_task_group() as tasks:
        tasks.start_soon(source.refresh, digest)
        tasks.start_soon(source.refresh, digest)
    latest = await store.read()
    assert len(refreshes) == 1 and latest.generation == snapshot.generation and latest.account is not None
    assert GoogleAIPlanCredentials.model_validate(latest.account.credentials) == initial.model_copy(update=updates)


def test_google_ai_plan_requires_explicit_client_registration(monkeypatch):
    from a13n_harness_ui.model_providers.google_ai_plan import GoogleAIPlanPlugin
    from a13n_harness_ui.model_providers.sdk import OAuthClientUnconfigured

    monkeypatch.delenv("A13N_GOOGLE_AI_PLAN_CLIENT_ID", raising=False)
    monkeypatch.delenv("A13N_GOOGLE_AI_PLAN_CLIENT_SECRET", raising=False)
    with pytest.raises(OAuthClientUnconfigured):
        GoogleAIPlanPlugin()._client()
