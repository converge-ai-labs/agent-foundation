"""Public models.dev directory; never consulted by model execution."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import date
from time import monotonic
from typing import Any, Protocol

import httpx2
from a13n_harness import ModelCapability
from a13n_harness.token_pricing import TokenPriceTier, TokenPricing, TokenRates
from a13n_logging import get_logger
from anyio import Lock, fail_after

from .catalog_identity import catalog_identity, model_identities
from .domain import CatalogModel, CatalogRef, ModelCatalogCollection, ModelDeclarations
from .profiles import PROVIDER_CATALOGS

logger = get_logger(__name__)
_CATALOG_URL = "https://models.dev/catalog.json"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_RELEASED_SINCE = date(2026, 4, 23)

# Directory channels, not executable provider types.
CATALOG_PROVIDERS = frozenset(channel for channels in PROVIDER_CATALOGS.values() for channel in channels) | {
    "volcengine"
}


class ModelCatalog(Protocol):
    async def models(self) -> ModelCatalogCollection: ...


class ModelsDevCatalog:
    """Lifespan-owned last-good directory with bounded single-flight refresh."""

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        *,
        released_since: date = DEFAULT_RELEASED_SINCE,
        clock: Callable[[], float] = monotonic,
        refresh_seconds: float = 3600,
        refresh_timeout_seconds: float = 30,
    ) -> None:
        self._http_client = http_client
        self._released_since = released_since
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._refresh_timeout_seconds = refresh_timeout_seconds
        self._snapshot = ModelCatalogCollection(status="unavailable", released_since=released_since)
        self._refresh_after = 0.0
        self._lock = Lock()

    async def models(self) -> ModelCatalogCollection:
        if self._clock() < self._refresh_after:
            return self._snapshot
        async with self._lock:
            if self._clock() < self._refresh_after:
                return self._snapshot
            try:
                with fail_after(self._refresh_timeout_seconds):
                    async with self._http_client.stream("GET", _CATALOG_URL) as response:
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                                raise ValueError("model catalog exceeds its response limit")
                            body.extend(chunk)
                    snapshot = parse_catalog(json.loads(body), released_since=self._released_since)
            except (TimeoutError, httpx2.HTTPError, ValueError, TypeError):
                logger.warning("model_catalog_refresh_failed", exc_info=True)
                status = "unavailable" if self._snapshot.status == "unavailable" else "stale"
                self._snapshot = self._snapshot.model_copy(update={"status": status})
                self._refresh_after = self._clock() + 60
            else:
                self._snapshot = snapshot
                self._refresh_after = self._clock() + self._refresh_seconds
            return self._snapshot


def parse_catalog(payload: Any, *, released_since: date = DEFAULT_RELEASED_SINCE) -> ModelCatalogCollection:
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
        raise ValueError("model catalog must contain providers")
    providers = payload["providers"]
    if len(providers) > 1000:
        raise ValueError("model catalog exceeds its provider limit")
    items: list[CatalogModel] = []
    identities = model_identities(payload.get("models"))
    count = 0
    for provider_id, provider in providers.items():
        if provider_id not in CATALOG_PROVIDERS or not isinstance(provider, dict):
            continue
        models = provider.get("models", {})
        if not isinstance(models, dict):
            continue
        count += len(models)
        if count > 50_000:
            raise ValueError("model catalog exceeds its model limit")
        for model_id, value in models.items():
            if not isinstance(value, dict):
                continue
            try:
                released = date.fromisoformat(value.get("release_date", ""))
                modalities = value.get("modalities", {})
                if released < released_since or "text" not in modalities.get("output", []):
                    continue
                pricing, warning = catalog_pricing(value.get("cost"))
                capabilities = frozenset(
                    capability
                    for modality, capability in {
                        "image": ModelCapability.IMAGE_UNDERSTANDING,
                        "audio": ModelCapability.AUDIO_UNDERSTANDING,
                        "video": ModelCapability.VIDEO_UNDERSTANDING,
                    }.items()
                    if modality in modalities.get("input", [])
                )
                upstream = value.get("id", model_id)
                identity, name = catalog_identity(provider_id, upstream, value.get("name", model_id), identities)
                items.append(
                    CatalogModel(
                        ref=CatalogRef(provider=provider_id, model=upstream),
                        identity=identity,
                        name=name,
                        provider_name=provider.get("name", provider_id),
                        release_date=released,
                        declarations=ModelDeclarations(
                            supports_tools=value.get("tool_call"),
                            capabilities=capabilities,
                            context_window_tokens=value.get("limit", {}).get("context"),
                            structured_output=value.get("structured_output"),
                            pricing=pricing,
                        ),
                        pricing_warning=warning,
                    )
                )
            except (ValueError, TypeError, AttributeError):
                continue
    return ModelCatalogCollection(
        items=tuple(sorted(items, key=lambda item: (item.ref.provider, item.ref.model))),
        status="ready",
        released_since=released_since,
    )


def catalog_pricing(value: Any) -> tuple[TokenPricing | None, str | None]:
    """Translate explicit context tiers; never silently flatten unknown conditions."""
    if value is None:
        return None, None
    if not isinstance(value, Mapping):
        return None, "Unsupported catalog pricing"
    try:
        base = {key: value.get(key) for key in TokenRates.model_fields}
        tiers = [TokenPriceTier(rates=TokenRates.model_validate(base))]
        raw_tiers = value.get("tiers", [])
        if not isinstance(raw_tiers, list):
            raise ValueError("invalid tiers")
        if not raw_tiers and any(key.startswith("context_over_") for key in value):
            raise ValueError("legacy threshold has no explicit token count")
        for raw in raw_tiers:
            condition = raw["tier"]
            if condition.get("type") != "context" or set(condition) != {"type", "size"}:
                raise ValueError("unsupported tier condition")
            # Materialize models.dev's base-price overrides. Saved tiers never inherit.
            rates = {key: raw.get(key, base[key]) for key in TokenRates.model_fields}
            tiers.append(TokenPriceTier(above=condition["size"], rates=TokenRates.model_validate(rates)))
        return TokenPricing(tiers=tuple(tiers)), None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None, "Unsupported catalog pricing; configure prices manually"
