from __future__ import annotations

import json
from datetime import timedelta

import pytest
from a13n_harness.errors import ModelResolutionError
from a13n_harness_ui.configuration.models import SubscriptionGroupMember, SubscriptionGroupResource
from a13n_harness_ui.model_accounts.managed import SubscriptionAccounts
from a13n_harness_ui.model_accounts.routing import SubscriptionGroupModel
from a13n_harness_ui.model_accounts.usage import CodexUsage
from a13n_harness_ui.model_accounts.usage_cache import SubscriptionUsage
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel

from .test_model_accounts import _NOW, _codex_tokens

pytestmark = pytest.mark.anyio
PARAMETERS = ModelRequestParameters()


async def _accounts(tmp_path):
    catalog = SubscriptionAccounts(tmp_path)
    accounts = [await catalog.create("codex", name) for name in ("First", "Second")]
    for account in accounts:
        path = catalog.store(account).policy.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "tokens": _codex_tokens(
                        account_id=account.id, expires_at=_NOW + timedelta(days=3650), marker=account.name
                    )
                }
            )
        )
    group = SubscriptionGroupResource(
        schema_version="1",
        kind="subscription_group",
        id="codex-test",
        name="Test",
        provider="codex",
        accounts=tuple(SubscriptionGroupMember(account=account.id) for account in accounts),
    )
    return catalog, accounts, group


def _quota():
    return ModelHTTPError(429, "test-model", {"error": {"code": "usage_limit_reached"}})


async def test_request_failover_is_sticky_and_observes_pause(tmp_path):
    catalog, accounts, group = await _accounts(tmp_path)
    calls = []
    events = []
    fail = {accounts[0].id}

    def factory(account):
        async def request(messages, info):
            calls.append(account.id)
            if account.id in fail:
                raise _quota()
            return ModelResponse(parts=[TextPart(account.id)])

        return FunctionModel(request, model_name="test-model")

    usage = SubscriptionUsage(catalog)
    async with SubscriptionGroupModel(group, catalog, usage, factory, selected=events.append) as model:
        response = await model.request([], None, PARAMETERS)
        assert response.parts[0].content == accounts[1].id
        await model.request([], None, PARAMETERS)
        assert calls == [accounts[0].id, accounts[1].id, accounts[1].id]
        assert events[-1].reason == "quota_exhausted"
        await catalog.update(accounts[1].id, enabled=False)
        with pytest.raises(ModelResolutionError, match="No eligible"):
            await model.request([], None, PARAMETERS)


@pytest.mark.parametrize("status", [400, 403, 429, 500])
async def test_non_account_errors_do_not_failover(tmp_path, status):
    catalog, accounts, group = await _accounts(tmp_path)
    calls = []

    def factory(account):
        async def request(messages, info):
            calls.append(account.id)
            raise ModelHTTPError(status, "test-model", {"error": {"code": "invalid_request"}})

        return FunctionModel(request, model_name="test-model")

    async with SubscriptionGroupModel(group, catalog, SubscriptionUsage(catalog), factory) as model:
        with pytest.raises(ModelHTTPError):
            await model.request([], None, PARAMETERS)
    assert calls == [accounts[0].id]


@pytest.mark.parametrize("after_delivery", [False, True])
async def test_stream_failover_stops_at_delivery_boundary(tmp_path, after_delivery):
    catalog, accounts, group = await _accounts(tmp_path)
    calls = []

    def factory(account):
        async def stream(messages, info):
            calls.append(account.id)
            if account.id == accounts[0].id:
                if after_delivery:
                    yield "Already visible"
                raise _quota()
            yield "Second account"

        return FunctionModel(stream_function=stream, model_name="test-model")

    async with SubscriptionGroupModel(group, catalog, SubscriptionUsage(catalog), factory) as model:
        if after_delivery:
            with pytest.raises(ModelHTTPError):
                async with model.request_stream([], None, PARAMETERS) as stream:
                    async for _ in stream:
                        pass
            assert calls == [accounts[0].id]
        else:
            async with model.request_stream([], None, PARAMETERS) as stream:
                async for _ in stream:
                    pass
            assert calls == [account.id for account in accounts]


async def test_usage_manual_refresh_and_cross_instance_convergence(tmp_path):
    catalog, accounts, _ = await _accounts(tmp_path)
    now = [1000.0]
    reads = []
    fail = [False]

    async def reader(account):
        reads.append(account.id)
        if fail[0]:
            raise ValueError("provider failure")
        return CodexUsage(account_id=account.id, plan_type="plus")

    first = SubscriptionUsage(catalog, reader=reader, clock=lambda: now[0])
    second = SubscriptionUsage(SubscriptionAccounts(tmp_path), reader=reader, clock=lambda: now[0])
    await first.refresh(accounts[0].id)
    await second.refresh(accounts[0].id)
    assert len(reads) == 1
    await second.refresh(accounts[0].id, force=True)
    assert len(reads) == 2
    now[0] += 180
    fail[0] = True
    snapshot = await first.refresh(accounts[0].id)
    assert snapshot.usage is not None and snapshot.error is not None
    assert snapshot.observed_at == 1000
    assert (await second.read(accounts[0])) == snapshot
    await catalog.logout(accounts[0].id)
    assert (await first.read(await catalog.get(accounts[0].id))).usage is None


async def test_usage_prefers_included_allowance_then_opted_in_credits(tmp_path):
    catalog, accounts, group = await _accounts(tmp_path)

    async def reader(account):
        return CodexUsage.model_validate(
            {
                "account_id": account.id,
                "plan_type": "plus",
                "rate_limit": {"allowed": account.id != accounts[0].id, "limit_reached": account.id == accounts[0].id},
                "credits": {"has_credits": True, "balance": "12.00"},
            }
        )

    usage = SubscriptionUsage(catalog, reader=reader)
    for account in accounts:
        await usage.refresh(account.id)
    assert await usage.tier(accounts[0], "test-model", allow_credits=False) is None
    assert await usage.tier(accounts[0], "test-model", allow_credits=True) == 1
    calls = []

    def factory(account):
        async def request(messages, info):
            calls.append(account.id)
            return ModelResponse(parts=[TextPart("ok")])

        return FunctionModel(request, model_name="test-model")

    group = group.model_copy(update={"allow_credits": True})
    async with SubscriptionGroupModel(group, catalog, usage, factory) as model:
        await model.request([], None, PARAMETERS)
        await catalog.delete(accounts[1].id)
        await model.request([], None, PARAMETERS)
    assert calls == [accounts[1].id, accounts[0].id]


async def test_weighted_order_is_sampled_once_and_credentials_reread(tmp_path):
    catalog, accounts, group = await _accounts(tmp_path)
    group = group.model_copy(
        update={
            "strategy": "weighted",
            "accounts": (
                SubscriptionGroupMember(account=accounts[0].id, weight=1),
                SubscriptionGroupMember(account=accounts[1].id, weight=10),
            ),
        }
    )
    built = []
    calls = []

    def factory(account):
        built.append(account.id)

        async def request(messages, info):
            calls.append(account.id)
            return ModelResponse(parts=[TextPart("ok")])

        return FunctionModel(request, model_name="test-model")

    async with SubscriptionGroupModel(
        group, catalog, SubscriptionUsage(catalog), factory, random_value=lambda: 0.5
    ) as model:
        await model.request([], None, PARAMETERS)
        await model.request([], None, PARAMETERS)
        count = len(built)
        path = catalog.store(accounts[1]).policy.path
        value = json.loads(path.read_text())
        value["tokens"]["refresh_token"] = "rotated-by-other-tui"
        path.write_text(json.dumps(value))
        await model.request([], None, PARAMETERS)
        assert len(built) == count + 1
    assert calls == [accounts[1].id] * 3


async def test_main_attribution_is_request_scoped_and_sink_errors_never_failover(tmp_path):
    from a13n_harness_ui.model_accounts.provenance import _MainAccountModel, main_request

    catalog, accounts, group = await _accounts(tmp_path)
    events = []
    calls = []

    def factory(account):
        async def request(messages, info):
            calls.append(account.id)
            return ModelResponse(parts=[TextPart("ok")])

        return FunctionModel(request, model_name="test-model")

    async def selected(event):
        events.append(event)

    async with SubscriptionGroupModel(group, catalog, SubscriptionUsage(catalog), factory) as model:
        await model.request([], None, PARAMETERS)  # Auxiliary inference, same Model.
        assert not events
        main = _MainAccountModel(model, "model-test", selected)
        await main.request([], None, PARAMETERS)
        assert events[-1].account_id == accounts[0].id
        assert events[-1].model_id == "model-test"
        assert main_request.get() is None
        await model.request([], None, PARAMETERS)
        assert len(events) == 1

        async def broken_sink(event):
            raise _quota()  # Even a provider-shaped persistence failure must not replay.

        main = _MainAccountModel(model, "model-test", broken_sink)
        with pytest.raises(ModelHTTPError):
            await main.request([], None, PARAMETERS)
        assert calls == [accounts[0].id] * 4
        assert main_request.get() is None


async def test_grok_provider_refresh_failure_switches_accounts(tmp_path):
    import httpx2
    from a13n_harness.providers.model.oauth import CredentialRefreshError, build_grok_model

    from .test_model_accounts import _grok_entry

    catalog = SubscriptionAccounts(tmp_path)
    accounts = [await catalog.create("grok", name) for name in ("Expired", "Ready")]
    for index, account in enumerate(accounts):
        store = catalog.store(account)
        path = store.policy.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    store.scope: {
                        **_grok_entry(
                            account_id=account.id,
                            expires_at=_NOW + timedelta(days=3650 if index else -1),
                            marker=account.name,
                        ),
                        "oidc_issuer": store.scope.split("::")[0],
                        "oidc_client_id": store.scope.split("::")[1],
                    }
                }
            )
        )
    group = SubscriptionGroupResource(
        schema_version="1",
        kind="subscription_group",
        id="grok-test",
        name="Grok test",
        provider="grok",
        accounts=tuple(SubscriptionGroupMember(account=account.id) for account in accounts),
    )
    refreshed = []
    requests = []
    events = []

    async def refresh(credentials):
        refreshed.append(credentials.account_id)
        raise CredentialRefreshError("grok", "Refresh rejected")

    async def respond(request):
        requests.append(request.headers["authorization"])
        return httpx2.Response(
            200,
            json={
                "id": "resp-test",
                "object": "response",
                "created_at": 1,
                "model": "grok-test",
                "status": "completed",
                "output": [
                    {
                        "id": "msg-test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "Ready", "annotations": []}],
                    }
                ],
            },
        )

    clients = []

    def factory(account):
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        clients.append(client)
        return build_grok_model(
            "grok-test", credential_source=catalog.store(account), refresh=refresh, http_client=client
        )

    try:
        async with SubscriptionGroupModel(
            group, catalog, SubscriptionUsage(catalog), factory, selected=events.append
        ) as model:
            response = await model.request([], None, PARAMETERS)
        assert response.parts[0].content == "Ready"
        assert refreshed == [accounts[0].id]
        assert requests == ["Bearer grok-access-Ready"]
        assert events[-1].reason == "authentication_unavailable"
        assert events[-1].account_id == accounts[1].id
    finally:
        for client in clients:
            await client.aclose()


async def test_main_stream_binding_does_not_leak_to_consumer(tmp_path):
    from a13n_harness_ui.model_accounts.provenance import _MainAccountModel, main_request

    catalog, accounts, group = await _accounts(tmp_path)
    events = []

    async def stream(messages, info):
        yield "visible"

    async def selected(event):
        events.append(event)

    async with SubscriptionGroupModel(
        group, catalog, SubscriptionUsage(catalog), lambda account: FunctionModel(stream_function=stream)
    ) as model:
        main = _MainAccountModel(model, "model-test", selected)
        async with main.request_stream([], None, PARAMETERS) as response:
            assert main_request.get() is None
            async for _ in response:
                assert main_request.get() is None
            async with model.request_stream([], None, PARAMETERS) as auxiliary:
                async for _ in auxiliary:
                    pass
        assert len(events) == 1
        assert events[0].account_id == accounts[0].id
