"""The models.dev catalog document, read as the models the Service offers.

Offer recent text models and realtime models with a supported voice protocol. Skip malformed entries
and reject malformed or oversized documents.
"""

import json
import re
from collections.abc import Mapping
from datetime import date

from a13n_harness import ModelCapability
from a13n_harness.pricing import ModelPriceRule, ModelPricingEntry, PriceComponent, PriceTier
from a13n_harness.spec import HarnessModelCharacteristics

from a13n_service.resources.models.schemas import CatalogModel, CatalogRef

RELEASED_SINCE = date(2026, 4, 23)
MAX_PROVIDERS = 1000
MAX_MODELS = 50_000
UNSUPPORTED_PRICING = "Unsupported catalog pricing; configure prices manually"
# models.dev prices, in USD per million tokens, by the genai-prices key a pricing entry states them under.
_PRICE_KEYS = {
    "input": "input_mtok",
    "output": "output_mtok",
    "cache_read": "cache_read_mtok",
    "cache_write": "cache_write_mtok",
    "input_audio": "input_audio_mtok",
    "output_audio": "output_audio_mtok",
}
# models.dev input modalities, by the capability each declares.
_UNDERSTANDING = {
    "image": ModelCapability.IMAGE_UNDERSTANDING,
    "audio": ModelCapability.AUDIO_UNDERSTANDING,
    "video": ModelCapability.VIDEO_UNDERSTANDING,
    "pdf": ModelCapability.DOCUMENT_UNDERSTANDING,
}
_BEDROCK_REGION = re.compile(r"^(?:us|eu|au|jp|in|global)\.")

type Identities = Mapping[str, tuple[str, str]]


def parse_catalog(document: bytes, channels: frozenset[str]) -> list[CatalogModel]:
    """The offered models of a catalog document, ordered by channel and model ID."""
    payload = json.loads(document)
    if not isinstance(payload, dict) or not isinstance(providers := payload.get("providers"), dict):
        raise ValueError("The model catalog lists no providers")
    if len(providers) > MAX_PROVIDERS:
        raise ValueError("The model catalog exceeds its provider limit")
    identities = _identities(payload.get("models"))
    items: list[CatalogModel] = []
    count = 0
    for channel, provider in providers.items():
        if (
            channel not in channels
            or not isinstance(provider, dict)
            or not isinstance(models := provider.get("models"), dict)
        ):
            continue
        count += len(models)
        if count > MAX_MODELS:
            raise ValueError("The model catalog exceeds its model limit")
        for key, value in models.items():
            try:
                item = _catalog_model(channel, provider.get("name", channel), key, value, identities)
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if item is not None:
                items.append(item)
    return sorted(items, key=lambda item: (item.ref.provider, item.ref.model))


def _catalog_model(
    channel: str, provider_name: str, key: str, value: dict, identities: Identities
) -> CatalogModel | None:
    released = date.fromisoformat(value["release_date"])
    modalities = value["modalities"]
    if released < RELEASED_SINCE:
        return None
    model = value.get("id", key)
    api = None
    if value.get("type") == "realtime":
        if "audio" not in modalities["input"] or "audio" not in modalities["output"]:
            return None
        if channel == "openai" and model.startswith("gpt-realtime"):
            api = "openai.realtime"
        elif channel == "openai" and model.startswith("gpt-live"):
            api = "openai.live"
        elif channel == "azure" and model.startswith("gpt-realtime"):
            api = "azure.realtime"
        elif channel == "google" and ("-live-" in model or "-native-audio-" in model or model.endswith("-live")):
            api = "google.realtime"
        elif channel in {"xai", "x-ai"} and model.startswith("grok-voice"):
            api = "xai.realtime"
        if api is None:
            return None
    elif "text" not in modalities["output"]:
        return None
    identity, name = _identity(channel, model, value.get("name", model), identities)
    pricing, warning = _pricing(value.get("cost"), channel, model, value.get("last_updated") or value["release_date"])
    inputs = modalities.get("input", ())
    return CatalogModel(
        ref=CatalogRef(provider=channel, model=model),
        model_api=api,
        identity=identity,
        name=name,
        provider_name=provider_name,
        release_date=released,
        characteristics=HarnessModelCharacteristics(
            capabilities=frozenset(capability for kind, capability in _UNDERSTANDING.items() if kind in inputs),
            context_window_tokens=value.get("limit", {}).get("context"),
        ),
        pricing=pricing,
        pricing_warning=warning,
    )


def _identities(models: object) -> dict[str, tuple[str, str]]:
    """The document's model directory, `lab/model` IDs and names, by each unambiguous alias of an ID."""
    if not isinstance(models, dict):
        return {}
    if len(models) > MAX_MODELS:
        raise ValueError("The model catalog exceeds its model limit")
    found: dict[str, set[tuple[str, str]]] = {}
    for identity, value in models.items():
        name = value.get("name") if isinstance(value, dict) else None
        lab, separator, model = identity.partition("/")
        if not isinstance(name, str) or not separator:
            continue
        for alias in {identity, model, f"{lab}.{model}"}:
            found.setdefault(_alias(alias), set()).add((identity, name))
    return {alias: next(iter(matches)) for alias, matches in found.items() if len(matches) == 1}


def _identity(channel: str, model: str, name: str, identities: Identities) -> tuple[str, str]:
    """The identity and name a channel's model shares with its copies on other channels, else its own."""
    candidate = model
    if channel == "amazon-bedrock":
        candidate = _BEDROCK_REGION.sub("", candidate)
    elif channel == "google-vertex":
        candidate = candidate.removesuffix("@default")
    # Exact IDs only, punctuation aside: dates, editions and deployment suffixes stay distinct models, and
    # display names never group.
    return identities.get(_alias(candidate), (f"{channel}/{model}", name))


def _alias(value: str) -> str:
    return re.sub(r"[./_-]", "-", value.lower())


def _pricing(cost: object, channel: str, model: str, revision: str) -> tuple[ModelPricingEntry | None, str | None]:
    """The catalog's prices as a pricing entry, or why they are withheld: never an approximation."""
    if cost is None:
        return None, None
    try:
        return _price_entry(cost, channel, model, revision), None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None, UNSUPPORTED_PRICING


def _price_entry(cost: object, channel: str, model: str, revision: str) -> ModelPricingEntry:
    if not isinstance(cost, dict):
        raise TypeError("Catalog prices must be an object")
    tiers = cost.get("tiers", [])
    # `context_over_*` prices repeat context tiers without their token count, so they only pass beside them;
    # a reasoning price equal to the output price is already charged as output.
    stated = {"tiers", *_PRICE_KEYS, *(key for key in cost if tiers and key.startswith("context_over_"))}
    if "reasoning" in cost and cost["reasoning"] == cost.get("output"):
        stated.add("reasoning")
    if not cost.keys() <= stated:
        raise ValueError("Catalog prices name an unsupported price")
    base = {key: price for key, price in cost.items() if key in _PRICE_KEYS}
    thresholds: list[tuple[int, dict]] = []
    for tier in tiers:
        condition = tier["tier"]
        size = condition["size"]
        if condition != {"type": "context", "size": size} or type(size) is not int:
            raise ValueError("Catalog price tiers must be context sizes")
        if not tier.keys() - {"tier"} <= base.keys():
            raise ValueError("A catalog price tier names a price the base does not")
        thresholds.append((size, tier))
    thresholds.sort(key=lambda threshold: threshold[0])
    prices = tuple(
        PriceComponent(
            price_key=_PRICE_KEYS[key],
            price=price,
            # A tier lists only the prices it changes; the entry states each price at every tier.
            tiers=tuple(PriceTier(start=size, price=tier.get(key, price)) for size, tier in thresholds),
        )
        for key, price in base.items()
    )
    return ModelPricingEntry(
        provider=channel,
        model=model,
        rules=(ModelPriceRule(rule_id="standard", prices=prices),),
        source="models.dev",
        source_revision=revision,
    )
