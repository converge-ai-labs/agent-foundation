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


@pytest.mark.parametrize("change", ["account", "credentials"])
@pytest.mark.parametrize("operation", ["request", "stream", "count_tokens", "compact_messages"])
async def test_overlapping_operations_keep_stream_client_open(tmp_path, operation, change):
    from pydantic_ai.usage import RequestUsage

    catalog, accounts, group = await _accounts(tmp_path)
    instances = []

    class OwnedModel(FunctionModel):
        def __init__(self, account):
            self.account_id = account.id
            self.open = False
            self.exits = 0

            async def request(messages, info):
                assert self.open
                return ModelResponse(parts=[TextPart(account.id)])

            async def stream(messages, info):
                assert self.open
                yield "first"
                assert self.open
                yield "last"

            super().__init__(request, stream_function=stream, model_name="test-model")

        async def __aenter__(self):
            assert not self.open
            self.open = True
            return self

        async def __aexit__(self, *args):
            assert self.open
            self.open = False
            self.exits += 1

        async def count_tokens(self, *args):
            assert self.open
            return RequestUsage(input_tokens=1)

        async def compact_messages(self, *args, **kwargs):
            assert self.open
            return ModelResponse(parts=[TextPart("compact")])

    def factory(account):
        instance = OwnedModel(account)
        instances.append(instance)
        return instance

    async with SubscriptionGroupModel(group, catalog, SubscriptionUsage(catalog), factory) as model:
        async with model.request_stream([], None, PARAMETERS) as response:
            first = instances[-1]
            if change == "account":
                await catalog.update(accounts[0].id, enabled=False)
            else:
                path = catalog.store(accounts[0]).policy.path
                value = json.loads(path.read_text())
                value["tokens"]["refresh_token"] = "rotated-by-other-tui"
                path.write_text(json.dumps(value))
            if operation == "stream":
                async with model.request_stream([], None, PARAMETERS) as other:
                    async for _ in other:
                        assert first.open
            elif operation == "compact_messages":
                await model.compact_messages(None)
            else:
                await getattr(model, operation)([], None, PARAMETERS)
            assert instances[-1] is not first
            assert instances[-1].account_id == accounts[1 if change == "account" else 0].id
            assert not instances[-1].open
            assert first.open
            async for _ in response:
                assert first.open
        assert not first.open
    assert all(instance.exits == 1 for instance in instances)


async def test_cancelled_stream_closes_only_its_request_model(tmp_path):
    from anyio import CancelScope, Event, create_task_group, sleep_forever

    catalog, accounts, group = await _accounts(tmp_path)
    entered, finished = Event(), Event()
    scopes = []
    instances = []

    class OwnedModel(FunctionModel):
        def __init__(self):
            self.open = False
            self.exits = 0

            async def stream(messages, info):
                yield "first"
                assert self.open
                yield "last"

            super().__init__(stream_function=stream, model_name="test-model")

        async def __aenter__(self):
            self.open = True
            return self

        async def __aexit__(self, *args):
            from anyio import sleep

            await sleep(0)  # Real provider cleanup awaits locks and socket closure.
            self.open = False
            self.exits += 1

    def factory(account):
        instance = OwnedModel()
        instances.append(instance)
        return instance

    async with SubscriptionGroupModel(group, catalog, SubscriptionUsage(catalog), factory) as model:

        async def cancelled_request():
            with CancelScope() as scope:
                scopes.append(scope)
                async with model.request_stream([], None, PARAMETERS):
                    entered.set()
                    await sleep_forever()
            finished.set()

        async with create_task_group() as tasks:
            tasks.start_soon(cancelled_request)
            await entered.wait()
            cancelled = instances[-1]
            await catalog.update(accounts[0].id, enabled=False)
            async with model.request_stream([], None, PARAMETERS) as response:
                surviving = instances[-1]
                scopes[0].cancel()
                await finished.wait()
                assert not cancelled.open
                assert surviving.open
                async for _ in response:
                    pass
        assert not surviving.open
    assert len(instances) == 3  # Template plus two requests; cancellation never retries.
    assert all(instance.exits == 1 for instance in instances)


async def test_codex_turn_state_survives_request_scopes_and_nested_router_entries(tmp_path):
    import httpx2
    from a13n_harness.models.codex import CodexRequestModel
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    catalog, _, group = await _accounts(tmp_path)
    headers = []
    clients = []

    async def respond(request):
        headers.append(request.headers.get("x-codex-turn-state"))
        events = [
            {
                "type": f"response.{event}",
                "sequence_number": index,
                "response": {
                    "id": "resp-test",
                    "created_at": 1,
                    "model": "gpt-5",
                    "object": "response",
                    "output": [],
                    "parallel_tool_calls": True,
                    "tool_choice": "auto",
                    "tools": [],
                    "status": status,
                },
            }
            for index, (event, status) in enumerate((("created", "in_progress"), ("completed", "completed")))
        ]
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", "x-codex-turn-state": "turn-one"},
            content="".join(f"data: {json.dumps(event)}\n\n" for event in events).encode(),
        )

    def factory(account):
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        clients.append(client)
        return CodexRequestModel("gpt-5", credential_source=catalog.store(account), http_client=client)

    try:
        async with SubscriptionGroupModel(group, catalog, SubscriptionUsage(catalog), factory) as model:
            context = RunContext(deps=None, model=model, usage=RunUsage(), run_id="run-test")
            async with model.request_stream([], None, PARAMETERS, context) as first:
                async with model:
                    async with model.request_stream([], None, PARAMETERS, context) as second:
                        async for _ in second:
                            pass
                async for _ in first:
                    pass
            async with model.request_stream([], None, PARAMETERS, context) as third:
                async for _ in third:
                    pass
        assert headers == [None, "turn-one", "turn-one"]
        assert len(clients) == 2  # Template plus the account model reused across all requests.
    finally:
        for client in clients:
            await client.aclose()


async def test_exhausted_accounts_preserve_last_provider_error(tmp_path):
    catalog, accounts, group = await _accounts(tmp_path)
    errors = []

    def factory(account):
        async def request(messages, info):
            error = _quota()
            errors.append(error)
            raise error

        return FunctionModel(request, model_name="test-model")

    async with SubscriptionGroupModel(group, catalog, SubscriptionUsage(catalog), factory) as model:
        with pytest.raises(ModelResolutionError) as raised:
            await model.request([], None, PARAMETERS)
    assert len(errors) == len(accounts)
    assert raised.value.__cause__ is errors[-1]


async def test_selection_only_refreshes_best_eligible_candidate(tmp_path):
    catalog, accounts, group = await _accounts(tmp_path)
    reads = []

    async def reader(account):
        reads.append(account.id)
        return CodexUsage(account_id=account.id, plan_type="plus")

    async def request(messages, info):
        return ModelResponse(parts=[TextPart("ok")])

    usage = SubscriptionUsage(catalog, reader=reader)
    async with SubscriptionGroupModel(group, catalog, usage, lambda account: FunctionModel(request)) as model:
        await model.request([], None, PARAMETERS)
        await model.request([], None, PARAMETERS)
    assert reads == [accounts[0].id]


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
    now[0] += 600
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
        assert count == 2  # Stable template plus one reused account model.
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


async def test_grok_same_generation_overlap_keeps_owned_client_open(tmp_path, monkeypatch):
    import httpx2
    from a13n_harness.providers.model.oauth import build_grok_model
    from anyio import Event, create_task_group, fail_after

    from .test_model_accounts import _grok_entry

    catalog = SubscriptionAccounts(tmp_path)
    account = await catalog.create("grok", "Shared")
    store = catalog.store(account)
    path = store.policy.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                store.scope: {
                    **_grok_entry(account_id=account.id, expires_at=_NOW + timedelta(days=3650), marker="shared"),
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
        name="Shared",
        provider="grok",
        accounts=(SubscriptionGroupMember(account=account.id),),
    )
    entered, release = Event(), Event()
    requests = []
    clients = []
    instances = []

    async def respond(request):
        requests.append(request)
        if len(requests) == 1:
            entered.set()
            await release.wait()
            assert not clients[-1].is_closed
        return httpx2.Response(
            200,
            json={
                "id": "resp-test",
                "object": "response",
                "created_at": 1,
                "model": "grok-test",
                "status": "completed",
                "output": [],
            },
        )

    original_init = httpx2.AsyncClient.__init__

    def mock_transport(client, *args, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(respond)
        original_init(client, *args, **kwargs)
        clients.append(client)

    monkeypatch.setattr(httpx2.AsyncClient, "__init__", mock_transport)

    def factory(selected):
        # Omit http_client: exercise the real provider-owned, reentrant lifecycle.
        instance = build_grok_model("grok-test", credential_source=catalog.store(selected))
        instances.append(instance)
        return instance

    with fail_after(10):
        async with SubscriptionGroupModel(group, catalog, SubscriptionUsage(catalog), factory) as model:
            async with create_task_group() as tasks:
                tasks.start_soon(model.request, [], None, PARAMETERS)
                await entered.wait()
                shared_client = clients[-1]
                await model.request([], None, PARAMETERS)
                assert len(instances) == 2  # Stable template and one cached account generation.
                assert clients[-1] is shared_client
                assert not shared_client.is_closed
                release.set()
            assert shared_client.is_closed
            await model.request([], None, PARAMETERS)
            assert len(instances) == 2
            assert clients[-1] is not shared_client
            assert clients[-1].is_closed
    assert len(requests) == 3
    assert all(client.is_closed for client in clients)


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


async def test_request_refreshes_stale_usage_before_idle_poll(tmp_path):
    catalog, accounts, _ = await _accounts(tmp_path)
    now = [1000.0]
    reads = []

    async def reader(account):
        reads.append(account.id)
        return CodexUsage(account_id=account.id, plan_type="plus")

    usage = SubscriptionUsage(catalog, reader=reader, clock=lambda: now[0])
    assert await usage.tier(accounts[0], "test-model", allow_credits=False) == 0
    now[0] += 59
    await usage.tier(accounts[0], "test-model", allow_credits=False)
    assert len(reads) == 1
    now[0] += 1
    await usage.tier(accounts[0], "test-model", allow_credits=False)
    assert len(reads) == 2
    await usage.refresh(accounts[0].id)
    assert len(reads) == 2  # The idle fallback is independently longer.


def _usage_headers(percent="100"):
    return {
        "x-codex-primary-used-percent": percent,
        "x-codex-primary-window-minutes": "300",
        "x-codex-primary-reset-at": "9999",
    }


async def test_response_usage_is_immediate_revision_scoped_and_preserves_other_buckets(tmp_path):
    catalog, accounts, _ = await _accounts(tmp_path)
    now = [1000.0]

    async def reader(account):
        return CodexUsage.model_validate(
            {
                "account_id": account.id,
                "plan_type": "plus",
                "rate_limit": {"allowed": True, "limit_reached": False},
                "additional_rate_limits": [
                    {
                        "limit_name": "Special",
                        "metered_feature": "special-model",
                        "rate_limit": {"allowed": True, "limit_reached": False},
                    }
                ],
            }
        )

    usage = SubscriptionUsage(catalog, reader=reader, clock=lambda: now[0])
    await usage.refresh(accounts[0].id)
    now[0] += 1
    await usage.observe_headers(accounts[0], _usage_headers())
    assert await usage.tier(accounts[0], "test-model", allow_credits=False) is None
    assert await usage.tier(accounts[0], "special-model", allow_credits=False) == 0
    snapshot = await usage.read(accounts[0])
    assert snapshot.checked_at == 1000 and snapshot.observed_at == 1001
    for invalid in ({}, _usage_headers("NaN"), _usage_headers("bad")):
        await usage.observe_headers(accounts[0], invalid)
        assert await usage.read(accounts[0]) == snapshot
    await catalog.logout(accounts[0].id)
    await usage.observe_headers(accounts[0], _usage_headers("1"))
    assert (await usage.read(await catalog.get(accounts[0].id))).usage is None


async def test_slow_api_refresh_cannot_overwrite_response_usage(tmp_path):
    from anyio import Event, create_task_group

    catalog, accounts, _ = await _accounts(tmp_path)
    started, release = Event(), Event()
    now = [1000.0]

    async def reader(account):
        started.set()
        await release.wait()
        return CodexUsage(account_id=account.id, plan_type="plus")

    usage = SubscriptionUsage(catalog, reader=reader, clock=lambda: now[0])
    async with create_task_group() as tasks:
        tasks.start_soon(usage.refresh, accounts[0].id)
        await started.wait()
        now[0] += 1
        await usage.observe_headers(accounts[0], _usage_headers())
        release.set()
    snapshot = await usage.read(accounts[0])
    assert snapshot.usage.rate_limit.primary_window.used_percent == 100
    assert snapshot.observed_at == 1001
