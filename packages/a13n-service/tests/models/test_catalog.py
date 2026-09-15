from __future__ import annotations

import asyncio

import httpx2
import pytest
from a13n_service.models.base_models import BaseModelDirectory
from a13n_service.models.catalog import ModelsDevCatalog, merge_declarations, parse_catalog
from a13n_service.models.domain import ModelDeclarations, ModelPricing


def _model(
    model_id: str,
    name: str,
    *,
    context: int = 100_000,
    output: int = 10_000,
    cost: dict[str, float] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": model_id,
        "name": name,
        "modalities": {"input": ["text", "image"]},
        "structured_output": True,
        "limit": {"context": context, "output": output},
    }
    if cost is not None:
        result["cost"] = cost
        result["reasoning_options"] = [
            {"type": "toggle"},
            {"type": "effort", "values": ["none", "low", "high", "max"]},
        ]
    return result


def _payload() -> dict[str, object]:
    return {
        "models": {
            "openai/gpt-5": _model("openai/gpt-5", "GPT-5"),
            "openai/gpt-5-mini": _model("openai/gpt-5-mini", "GPT-5 Mini", context=200_000),
        },
        "providers": {
            "openai": {
                "models": {
                    "gpt-5": _model(
                        "gpt-5",
                        "GPT-5",
                        context=400_000,
                        output=128_000,
                        cost={"input": 1.25, "output": 10, "cache_read": 0.125},
                    )
                }
            },
            "openrouter": {
                "models": {"openai/gpt-5": _model("openai/gpt-5", "GPT-5", cost={"input": 2, "output": 20})}
            },
        },
    }


@pytest.mark.anyio
async def test_catalog_enriches_selected_base_model_and_attributes_only_actual_channel_prices() -> None:
    reference = BaseModelDirectory(("openai:gpt-5",)).require("openai:gpt-5")

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=_payload(), request=request))
    ) as client:
        catalog = ModelsDevCatalog(client)
        direct = await catalog.declarations("openai", {}, reference)
        custom = await catalog.declarations("openai", {"base_url": "https://relay.example/v1"}, reference)
        relay = await catalog.declarations("openrouter", {}, reference)

    assert direct.context_window_tokens == 400_000
    assert direct.thinking_efforts == ("low", "high")
    assert direct.pricing == ModelPricing(input=1.25, output=10, cache_read=0.125)
    assert custom.context_window_tokens == 100_000
    assert custom.pricing is None
    assert relay.pricing == ModelPricing(input=2, output=20)


def test_declaration_overlay_preserves_omission_and_explicit_clears() -> None:
    suggested = ModelDeclarations(
        thinking_efforts=("low", "high"),
        capabilities=("image_understanding",),
        context_window_tokens=400_000,
        max_output_tokens=128_000,
        structured_output=True,
        pricing=ModelPricing(input=1.25, output=10, cache_read=0.125, cache_write=2.5),
    )
    explicit = ModelDeclarations(
        thinking_efforts=(),
        structured_output=False,
        context_window_tokens=None,
        pricing=ModelPricing(input=0, cache_read=None),
    )

    merged = merge_declarations(suggested, explicit)

    assert merged.thinking_efforts == ()
    assert merged.capabilities == frozenset({"image_understanding"})
    assert merged.context_window_tokens is None
    assert merged.max_output_tokens == 128_000
    assert merged.structured_output is False
    assert merged.pricing == ModelPricing(input=0, output=10, cache_read=None, cache_write=2.5)


@pytest.mark.parametrize("payload", [None, {}, {"models": [], "providers": {}}, {"models": {}, "providers": []}])
def test_malformed_catalog_is_rejected(payload) -> None:
    with pytest.raises(ValueError):
        parse_catalog(payload)


@pytest.mark.anyio
async def test_cache_refresh_is_single_flight_and_retains_last_good_snapshot() -> None:
    now = 0.0
    calls = 0
    responses: list[object] = [_payload(), {"invalid": True}, _payload()]
    reference = BaseModelDirectory(("openai:gpt-5",)).require("openai:gpt-5")

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        response = responses[min(calls, len(responses) - 1)]
        calls += 1
        return httpx2.Response(200, json=response, request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        catalog = ModelsDevCatalog(client, clock=lambda: now, refresh_seconds=10)
        first, second = await asyncio.gather(
            catalog.declarations("openai", {}, reference),
            catalog.declarations("openai", {}, reference),
        )
        assert first == second
        assert calls == 1

        now = 11
        assert (await catalog.declarations("openai", {}, reference)).pricing is not None
        assert calls == 2

        now = 70
        assert (await catalog.declarations("openai", {}, reference)).pricing is not None
        assert calls == 2

        now = 72
        assert (await catalog.declarations("openai", {}, reference)).pricing is not None
        assert calls == 3


@pytest.mark.anyio
async def test_initial_catalog_failure_returns_empty_declarations_without_forwarding_credentials() -> None:
    calls = 0
    reference = BaseModelDirectory(("openai:gpt-5",)).require("openai:gpt-5")

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx2.Response(503, request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        catalog = ModelsDevCatalog(client, clock=lambda: 0)
        assert await catalog.declarations("openai", {}, reference) == ModelDeclarations()
        assert await catalog.declarations("openai", {}, reference) == ModelDeclarations()
        assert calls == 1
