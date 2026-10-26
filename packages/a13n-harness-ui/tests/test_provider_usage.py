"""Provider allowance contracts use synthetic credentials and mocked transports."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_harness_ui.model_accounts.managed import SubscriptionAccounts
from a13n_harness_ui.model_accounts.provider_usage import GrokUsage, read_copilot_usage, read_grok_usage
from a13n_harness_ui.model_accounts.usage_cache import SubscriptionUsage, UsageSnapshot

from .test_copilot_accounts import grant
from .test_model_accounts import _grok_credential

pytestmark = pytest.mark.anyio


def grok_source():
    credential = _grok_credential(
        account_id="user-one", expires_at=datetime.now(UTC) + timedelta(hours=2), marker="usage"
    )
    return SimpleNamespace(load=AsyncMock(return_value=credential), rotate=AsyncMock())


async def test_grok_reads_actual_billing_contract_without_codex_windows():
    source = grok_source()

    def handle(request):
        assert str(request.url) == "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
        assert request.headers["x-userid"] == "user-one"
        assert request.headers["X-XAI-Token-Auth"] == "xai-grok-cli"
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx2.Response(
            200,
            json={
                "config": {
                    "creditUsagePercent": 42.5,
                    "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY", "end": "2030-02-01T00:00:00Z"},
                    "prepaidBalance": {"val": -1500},
                    "monthlyLimit": {},
                    "used": {},
                }
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        usage = await read_grok_usage(source, client, expected_account_id="user-one")
    assert usage.config.usage_percent == 42.5
    assert usage.config.prepaid_balance.val == -1500
    assert usage.config.monthly_limit.val == 0
    assert UsageSnapshot.model_validate_json(UsageSnapshot(usage=usage).model_dump_json()).usage == usage
    source.rotate.assert_not_called()


@pytest.mark.parametrize("provider", ["grok", "copilot"])
async def test_quota_redirect_does_not_forward_credentials(provider):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(302, headers={"Location": "https://example.test/steal"})

    source = (
        grok_source()
        if provider == "grok"
        else SimpleNamespace(load=AsyncMock(return_value=grant()), rotate=AsyncMock())
    )
    read = read_grok_usage if provider == "grok" else read_copilot_usage
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle), follow_redirects=True) as client:
        with pytest.raises(ValueError, match="HTTP 302"):
            await read(source, client)
    assert len(requests) == 1
    assert "authorization" not in requests[0].headers
    assert "x-userid" not in requests[0].headers


async def test_copilot_uses_selected_oauth_grant_and_preserves_missing_buckets():
    credential = grant()
    source = SimpleNamespace(load=AsyncMock(return_value=credential), rotate=AsyncMock())

    def handle(request):
        assert str(request.url) == "https://api.github.com/copilot_internal/user"
        assert request.headers["Authorization"] == f"token {credential.access_token}"
        assert request.headers["X-GitHub-Api-Version"] == "2025-04-01"
        return httpx2.Response(
            200,
            json={
                "copilot_plan": "individual",
                "quota_reset_date": "2030-02-01T00:00:00Z",
                "quota_snapshots": {
                    "chat": {"unlimited": True},
                    "premium_models": {"percent_remaining": 30, "overage_permitted": False},
                },
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        usage = await read_copilot_usage(source, client)
    assert usage.quota_snapshots["chat"].unlimited
    assert usage.quota_snapshots["premium_models"].remaining is None
    assert "completions" not in usage.quota_snapshots


async def test_grok_account_binding_blocks_before_network():
    handle = AsyncMock()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="account changed"):
            await read_grok_usage(grok_source(), client, expected_account_id="another")
    handle.assert_not_called()


async def test_grok_null_config_clears_data_but_failure_retains_snapshot(tmp_path):
    accounts = SubscriptionAccounts(tmp_path)
    account = await accounts.create("grok", "Grok")
    reader = AsyncMock(
        return_value=GrokUsage.model_validate({"config": {"monthlyLimit": {"val": 2000}, "used": {"val": 500}}})
    )
    cache = SubscriptionUsage(accounts, reader=reader)
    first = await cache.refresh(account.id)
    assert first.usage.config.usage_percent == 25
    reader.side_effect = ValueError("private-response")
    failed = await cache.refresh(account.id, force=True)
    assert failed.usage == first.usage and "private-response" not in failed.error
    reader.side_effect = None
    reader.return_value = GrokUsage(config=None)
    cleared = await cache.refresh(account.id, force=True)
    assert cleared.usage.config is None
    assert cleared.error is None


@pytest.mark.parametrize("provider", ["grok", "copilot"])
async def test_quota_401_reuses_coordinated_rotation(provider):
    source = (
        grok_source()
        if provider == "grok"
        else SimpleNamespace(load=AsyncMock(return_value=grant()), rotate=AsyncMock())
    )
    credential = await source.load()
    if provider == "grok":
        refreshed = replace(credential, access_token="rotated")
    else:
        refreshed = replace(credential, credentials=replace(credential.credentials, access_token="rotated"))

    async def rotate(expected, exchange):
        assert expected == credential
        source.load.return_value = refreshed
        return refreshed

    source.rotate.side_effect = rotate
    calls = []

    def handle(request):
        calls.append(request.headers["authorization"])
        return (
            httpx2.Response(401)
            if len(calls) == 1
            else httpx2.Response(200, json={"config": None} if provider == "grok" else {})
        )

    read = read_grok_usage if provider == "grok" else read_copilot_usage
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        await read(source, client)
    assert len(calls) == 2 and calls[1].endswith("rotated")
    source.rotate.assert_awaited_once()
