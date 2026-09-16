from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import httpx2
import pytest
from a13n_service.models.catalog import ModelsDevCatalog, catalog_pricing, parse_catalog


def payload(released="2026-04-23"):
    return {
        "providers": {
            "openai": {
                "name": "OpenAI",
                "models": {
                    "future-model": {
                        "name": "Future",
                        "release_date": released,
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                        "limit": {"context": 100000},
                        "cost": {"input": 5, "output": 30},
                    }
                },
            }
        }
    }


def test_directory_is_not_limited_to_installed_pydantic_names():
    result = parse_catalog(payload())
    assert result.items[0].ref.model == "future-model"
    assert result.items[0].declarations.context_window_tokens == 100000
    assert not parse_catalog(payload("2026-04-22")).items
    assert parse_catalog(payload("2026-04-22"), released_since=date(2026, 1, 1)).items
    assert not parse_catalog(payload("")).items


def test_directory_excludes_unsupported_channels_and_non_text_models():
    value = payload()
    value["providers"]["heroku"] = value["providers"].pop("openai")
    assert not parse_catalog(value).items
    value = payload()
    value["providers"]["openai"]["models"]["future-model"]["modalities"]["output"] = ["image"]
    assert not parse_catalog(value).items


def test_minimax_catalog_is_native_only_for_the_standard_channel():
    data = payload()
    data["providers"]["minimax"] = data["providers"].pop("openai")
    data["providers"]["minimax-coding-plan"] = data["providers"]["minimax"]
    items = parse_catalog(data).items
    assert [(item.ref.provider, item.ref.model) for item in items] == [("minimax", "future-model")]


def test_catalog_imports_complete_context_tiers_and_preserves_unknown_prices():
    pricing, warning = catalog_pricing(
        {
            "input": 5,
            "output": 30,
            "cache_read": 0,
            "tiers": [{"tier": {"type": "context", "size": 200000}, "input": 10, "output": 45}],
        }
    )
    assert warning is None and pricing is not None
    assert pricing.rates_for(200000).input == Decimal(5)
    assert pricing.rates_for(200001).output == Decimal(45)
    assert pricing.tiers[1].rates.cache_read == 0
    assert pricing.tiers[1].rates.cache_write is None


@pytest.mark.parametrize(
    "cost",
    [
        {"input": -1},
        {"input": float("nan")},
        {"tiers": [{"tier": {"type": "time", "size": 200}, "input": 1}]},
        {"context_over_200k": {"input": 10}},
    ],
)
def test_unrepresentable_prices_are_not_silently_flattened(cost):
    pricing, warning = catalog_pricing(cost)
    assert pricing is None and warning


@pytest.mark.anyio
async def test_single_flight_refresh_keeps_last_good_and_recovers():
    now = 0.0
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx2.Response(200, json={} if calls == 2 else payload())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        catalog = ModelsDevCatalog(client, clock=lambda: now, refresh_seconds=10)
        results = await asyncio.gather(*(catalog.models() for _ in range(5)))
        assert calls == 1 and all(result.status == "ready" for result in results)
        now = 11
        stale = await catalog.models()
        assert stale.status == "stale" and stale.items == results[0].items
        now = 72
        assert (await catalog.models()).status == "ready"


@pytest.mark.anyio
async def test_initial_outage_does_not_fabricate_an_empty_ready_catalog():
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(503))) as client:
        assert (await ModelsDevCatalog(client).models()).status == "unavailable"


@pytest.mark.anyio
async def test_cancelled_refresh_releases_lock_and_can_retry():
    calls = 0
    entered = asyncio.Event()

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        return httpx2.Response(200, json=payload())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        catalog = ModelsDevCatalog(client)
        pending = asyncio.create_task(catalog.models())
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert (await catalog.models()).status == "ready"
        assert calls == 2


@pytest.mark.anyio
async def test_oversize_refresh_retains_last_good():
    now = 0.0
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return (
            httpx2.Response(200, json=payload())
            if calls == 1
            else httpx2.Response(200, content=b" " * (8 * 1024 * 1024 + 1))
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        catalog = ModelsDevCatalog(client, clock=lambda: now, refresh_seconds=10)
        first = await catalog.models()
        now = 11
        second = await catalog.models()
        assert second.status == "stale" and second.items == first.items


def test_malformed_entry_does_not_hide_valid_entries():
    data = payload()
    data["providers"]["openai"]["models"]["bad"] = {"release_date": "2026-04-23", "modalities": None}
    assert len(parse_catalog(data).items) == 1
