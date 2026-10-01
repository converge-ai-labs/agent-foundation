"""The models.dev model catalog: reading its document, refreshing it from a real HTTP server, and its route."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import anyio
import httpx2
import pytest
from a13n_harness import ModelCapability, RunConfiguration
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_service.resources.models import catalog as catalog_module
from a13n_service.resources.models.catalog import (
    MAX_BYTES,
    REFRESH_SECONDS,
    RETRY_SECONDS,
    ModelsDevCatalog,
    catalog_channels,
)
from a13n_service.resources.models.models_dev import (
    MAX_MODELS,
    MAX_PROVIDERS,
    UNSUPPORTED_PRICING,
    parse_catalog,
)
from a13n_service.resources.models.schemas import CatalogModel, CatalogRef, ModelCatalog
from fastapi import FastAPI, Response

CHANNELS = frozenset({"openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter"})
LOOPBACK = EndpointPolicy()


def model(**changes: Any) -> dict[str, Any]:
    return {
        "name": "Future",
        "release_date": "2026-04-23",
        "last_updated": "2026-05-01",
        "modalities": {"input": ["text", "image"], "output": ["text"]},
        "limit": {"context": 100000},
        "cost": {"input": 5, "output": 30},
        **changes,
    }


def document(providers: dict[str, Any], models: dict[str, Any] | None = None) -> bytes:
    return json.dumps({"providers": providers, "models": models or {}}).encode()


def one(cost: object) -> CatalogModel:
    [item] = parse_catalog(document({"openai": {"models": {"future": model(cost=cost)}}}), CHANNELS)
    return item


def test_the_catalog_offers_recent_text_models_of_served_channels() -> None:
    items = parse_catalog(
        document(
            {
                "openai": {
                    "name": "OpenAI",
                    "models": {
                        "future": model(id="future-model"),
                        "old": model(release_date="2026-04-22"),
                        "undated": model(release_date=""),
                        "images": model(modalities={"input": ["text"], "output": ["image"]}),
                        "broken": {"release_date": "2026-04-23", "modalities": None},
                    },
                },
                "heroku": {"name": "Heroku", "models": {"elsewhere": model()}},
            }
        ),
        CHANNELS,
    )
    [item] = items
    assert item.ref == CatalogRef(provider="openai", model="future-model")
    assert (item.identity, item.name, item.provider_name) == ("openai/future-model", "Future", "OpenAI")
    assert item.characteristics.context_window_tokens == 100000
    assert item.characteristics.capabilities == {ModelCapability.IMAGE_UNDERSTANDING}


def test_input_modalities_declare_the_understanding_capabilities() -> None:
    inputs = {"input": ["text", "image", "audio", "video", "pdf"], "output": ["text"]}
    [item] = parse_catalog(document({"openai": {"models": {"omni": model(modalities=inputs)}}}), CHANNELS)
    assert item.characteristics.capabilities == {
        ModelCapability.IMAGE_UNDERSTANDING,
        ModelCapability.AUDIO_UNDERSTANDING,
        ModelCapability.VIDEO_UNDERSTANDING,
        ModelCapability.DOCUMENT_UNDERSTANDING,
    }


def test_the_catalog_channels_are_those_the_registered_types_serve() -> None:
    channels = catalog_channels(BUILT_IN_MODEL_PROVIDERS)
    assert {
        "openai",
        "openrouter",
        "amazon-bedrock",
        "google-vertex",
        "azure",
        "fireworks-ai",
        "togetherai",
    } <= channels
    assert "volcengine" not in channels


def test_a_document_beyond_its_bounds_is_refused() -> None:
    with pytest.raises(ValueError, match="provider limit"):
        parse_catalog(document({f"p{index}": {} for index in range(MAX_PROVIDERS + 1)}), CHANNELS)
    with pytest.raises(ValueError, match="model limit"):
        parse_catalog(document({"openai": {"models": {f"m{index}": {} for index in range(MAX_MODELS + 1)}}}), CHANNELS)
    with pytest.raises(ValueError, match="no providers"):
        parse_catalog(b"[]", CHANNELS)


def test_copies_of_one_model_share_its_identity_without_collapsing_versions() -> None:
    directory = {
        "anthropic/claude-opus-4-8": {"name": "Claude Opus 4.8"},
        "anthropic/claude-opus-5": {"name": "Claude Opus 5"},
        "a/model-1": {"name": "Same"},
        "b/model-1": {"name": "Same"},
    }
    offered = {
        "anthropic": ["claude-opus-4-8", "claude-opus-4-8-20260101", "claude-opus-5"],
        "amazon-bedrock": ["au.anthropic.claude-opus-4-8", "global.anthropic.claude-opus-4-8"],
        "openrouter": ["anthropic/claude-opus-4.8", "model-1"],
        "google-vertex": ["claude-opus-4-8@default"],
    }
    providers = {
        channel: {"models": {name: model(id=name, name="Channel name") for name in names}}
        for channel, names in offered.items()
    }
    identities = {
        f"{item.ref.provider}:{item.ref.model}": (item.identity, item.name)
        for item in parse_catalog(document(providers, directory), CHANNELS)
    }
    opus = ("anthropic/claude-opus-4-8", "Claude Opus 4.8")
    for key in (
        "anthropic:claude-opus-4-8",
        "amazon-bedrock:au.anthropic.claude-opus-4-8",
        "amazon-bedrock:global.anthropic.claude-opus-4-8",
        "openrouter:anthropic/claude-opus-4.8",
        "google-vertex:claude-opus-4-8@default",
    ):
        assert identities[key] == opus
    assert identities["anthropic:claude-opus-5"] == ("anthropic/claude-opus-5", "Claude Opus 5")
    assert identities["anthropic:claude-opus-4-8-20260101"][0] == "anthropic/claude-opus-4-8-20260101"
    # A model ID two labs publish is ambiguous, so it keeps its own identity rather than a guessed one.
    assert identities["openrouter:model-1"] == ("openrouter/model-1", "Channel name")


def test_context_tiers_become_complete_price_tiers() -> None:
    item = one(
        {
            "input": 5,
            "output": 30,
            "cache_read": 0.5,
            "tiers": [{"tier": {"type": "context", "size": 200000}, "input": 10, "output": 45}],
            "context_over_200k": {"input": 10, "output": 45},
        }
    )
    assert item.pricing is not None and item.pricing_warning is None
    assert (item.pricing.provider, item.pricing.model) == ("openai", "future")
    assert (item.pricing.source, item.pricing.source_revision) == ("models.dev", "2026-05-01")
    [rule] = item.pricing.rules
    prices = {
        price.price_key: (price.price, [(tier.start, tier.price) for tier in price.tiers]) for price in rule.prices
    }
    assert prices == {
        "input_mtok": (Decimal(5), [(200000, Decimal(10))]),
        "output_mtok": (Decimal(30), [(200000, Decimal(45))]),
        # A tier that leaves a price unchanged still states it.
        "cache_read_mtok": (Decimal("0.5"), [(200000, Decimal("0.5"))]),
    }


def test_audio_prices_are_kept_and_a_reasoning_price_equal_to_output_is_already_charged() -> None:
    item = one({"input": 1, "output": 2, "reasoning": 2, "input_audio": 3, "output_audio": 4})
    assert item.pricing is not None
    [rule] = item.pricing.rules
    assert {price.price_key for price in rule.prices} == {
        "input_mtok",
        "output_mtok",
        "input_audio_mtok",
        "output_audio_mtok",
    }
    assert (one(None).pricing, one(None).pricing_warning) == (None, None)


@pytest.mark.parametrize(
    "cost",
    [
        {"input": -1},
        {"input": float("nan")},
        {"input": "free"},
        {"input": 1, "output": 2, "reasoning": 3},
        {"input": 1, "context_over_200k": {"input": 2}},
        {"input": 1, "tiers": [{"tier": {"type": "time", "size": 200}, "input": 2}]},
        {"input": 1, "tiers": [{"tier": {"type": "context", "size": 200}, "cache_read": 2}]},
        [1, 2],
    ],
)
def test_prices_a_pricing_entry_cannot_express_are_withheld_with_a_warning(cost: object) -> None:
    item = one(cost)
    assert (item.pricing, item.pricing_warning) == (None, UNSUPPORTED_PRICING)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ModelsDev:
    """A stand-in for models.dev answering with `answer`, once `gate` opens; `arrived` is set by each request."""

    def __init__(self) -> None:
        self.url = ""
        self.requests = 0
        self.answer = Response(document({"openai": {"name": "OpenAI", "models": {"future": model()}}}))
        self.gate = asyncio.Event()
        self.gate.set()
        self.arrived = asyncio.Event()
        self.app = FastAPI()
        self.app.get("/catalog.json")(self.serve)

    async def serve(self) -> Response:
        self.requests += 1
        self.arrived.set()
        await self.gate.wait()
        return self.answer

    def hold(self) -> None:
        self.gate.clear()
        self.arrived.clear()


@pytest.fixture
async def models_dev(listen: Any) -> AsyncIterator[ModelsDev]:
    server = ModelsDev()
    async with listen(server.app) as url:
        server.url = f"{url}/catalog.json"
        yield server
        server.gate.set()


@asynccontextmanager
async def running(catalog: ModelsDevCatalog) -> AsyncIterator[ModelsDevCatalog]:
    """The catalog with its refresh task running, as the app lifespan runs it."""
    async with anyio.create_task_group() as group:
        group.start_soon(catalog.run)
        yield catalog
        group.cancel_scope.cancel()


async def revalidated(catalog: ModelsDevCatalog) -> ModelCatalog:
    """Read the catalog, which never waits once there is one, then wait for the refresh the read asked for."""
    ended = catalog.refreshed
    served = await asyncio.wait_for(catalog.read(), 1)
    await asyncio.wait_for(ended.wait(), 5)
    return served


@pytest.mark.anyio
async def test_a_failed_refresh_serves_the_last_catalog_as_stale_until_one_succeeds(models_dev: ModelsDev) -> None:
    clock = Clock()
    async with running(ModelsDevCatalog(CHANNELS, LOOPBACK, url=models_dev.url, clock=clock)) as catalog:
        ready = await catalog.read()
        assert ready.status == "ready" and [item.ref.model for item in ready.items] == ["future"]
        assert await catalog.read() is ready and models_dev.requests == 1

        clock.now = REFRESH_SECONDS
        models_dev.answer = Response(status_code=503)
        # The old catalog is served at once while it is revalidated.
        assert await revalidated(catalog) is ready
        stale = await catalog.read()
        assert stale.status == "stale" and stale.items == ready.items
        # A failure is retried only after its delay.
        clock.now += RETRY_SECONDS - 1
        assert (await catalog.read()).status == "stale" and models_dev.requests == 2

        clock.now += 1
        models_dev.answer = Response(document({"openai": {"models": {"newer": model()}}}))
        assert await revalidated(catalog) is stale
        refreshed = await catalog.read()
        assert refreshed.status == "ready" and [item.ref.model for item in refreshed.items] == ["newer"]


@pytest.mark.anyio
async def test_the_catalog_is_unavailable_until_a_refresh_first_succeeds(models_dev: ModelsDev) -> None:
    models_dev.answer = Response(b"not json")
    async with running(ModelsDevCatalog(CHANNELS, LOOPBACK, url=models_dev.url)) as catalog:
        unavailable = await catalog.read()
        assert (unavailable.status, unavailable.items) == ("unavailable", [])
    # The document is fetched under the supplied endpoint policy, including an explicit deny-all host set.
    refused = ModelsDevCatalog(
        CHANNELS, EndpointPolicy(configuration=RunConfiguration(allowed_hosts=set())), url=models_dev.url
    )
    async with running(refused):
        assert (await refused.read()).status == "unavailable"
    assert models_dev.requests == 1


@pytest.mark.anyio
async def test_one_refresh_runs_at_a_time_and_only_the_first_is_waited_for(models_dev: ModelsDev) -> None:
    clock = Clock()
    async with running(ModelsDevCatalog(CHANNELS, LOOPBACK, url=models_dev.url, clock=clock)) as catalog:
        models_dev.hold()
        # With no catalog yet, concurrent reads wait for the one refresh; a reader that gives up aborts nothing.
        abandoned = asyncio.create_task(catalog.read())
        first = [asyncio.create_task(catalog.read()) for _ in range(5)]
        await models_dev.arrived.wait()
        abandoned.cancel()
        models_dev.gate.set()
        results = await asyncio.gather(*first)
        assert models_dev.requests == 1 and all(result.status == "ready" for result in results)

        clock.now = REFRESH_SECONDS
        models_dev.hold()
        ended = catalog.refreshed
        assert await asyncio.wait_for(catalog.read(), 1) is results[0]
        await models_dev.arrived.wait()
        assert await asyncio.wait_for(catalog.read(), 1) is results[0]
        models_dev.gate.set()
        await asyncio.wait_for(ended.wait(), 5)
        assert (await catalog.read()).status == "ready" and models_dev.requests == 2


@pytest.mark.anyio
async def test_oversize_and_slow_responses_fail_the_refresh(
    models_dev: ModelsDev, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    async with running(ModelsDevCatalog(CHANNELS, LOOPBACK, url=models_dev.url, clock=clock)) as catalog:
        ready = await catalog.read()

        clock.now = REFRESH_SECONDS
        models_dev.answer = Response(b" " * (MAX_BYTES + 1))
        await revalidated(catalog)
        assert (await catalog.read()).status == "stale"

        monkeypatch.setattr(catalog_module, "FETCH_SECONDS", 0.2)
        clock.now += RETRY_SECONDS
        models_dev.hold()
        await revalidated(catalog)
        stale = await catalog.read()
        assert stale.status == "stale" and stale.items == ready.items and models_dev.requests == 3


@pytest.mark.anyio
async def test_the_route_serves_the_catalog_to_signed_in_principals(service: Any, models_dev: ModelsDev) -> None:
    channels = catalog_channels(service.runtime.registry.models.values())
    catalog = ModelsDevCatalog(channels, service.runtime.endpoint_policy, url=models_dev.url)
    service.app.state.model_catalog = catalog
    async with running(catalog):
        response = await service.client.get("/api/v1/model-catalog")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert [(item["ref"], item["identity"]) for item in body["items"]] == [
        ({"provider": "openai", "model": "future"}, "openai/future")
    ]
    assert body["items"][0]["pricing"]["model"] == "future"

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as anonymous:
        assert (await anonymous.get("/api/v1/model-catalog")).status_code == 401
    assert models_dev.requests == 1

    # Each model type names the channels listing its own model IDs, which clients filter the catalog by.
    types = (await service.client.get("/api/v1/provider-types/model")).json()["items"]
    served = {described["type"]: described["catalog_providers"] for described in types}
    assert served["openrouter"] == ["openrouter"] and served["ollama"] == []
    assert set().union(*served.values()) == channels


@pytest.mark.parametrize(
    ("provider_type", "channel", "model_id"),
    [
        ("fireworks", "fireworks-ai", "accounts/fireworks/models/llama-v3p3-70b-instruct"),
        ("together", "togetherai", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    ],
)
def test_hosted_open_model_providers_offer_their_catalog_channels(provider_type, channel, model_id):
    definition = next(item for item in BUILT_IN_MODEL_PROVIDERS if item.type == provider_type)
    [item] = parse_catalog(
        document({channel: {"models": {model_id: model()}}}),
        catalog_channels([definition]),
    )
    assert item.ref == CatalogRef(provider=channel, model=model_id)
