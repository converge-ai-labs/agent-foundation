"""Cached models.dev declaration enrichment for resolved base models."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from time import monotonic
from typing import Any, Protocol, cast, get_args

import httpx2
from a13n_harness import ModelCapability
from a13n_logging import get_logger
from anyio import Lock, fail_after
from pydantic_ai.settings import ThinkingEffort

from .base_models import BaseModelReference
from .domain import ModelDeclarations, ModelPricing

logger = get_logger(__name__)

_CATALOG_URL = "https://models.dev/catalog.json"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_CANONICAL_MODELS = 20_000
_MAX_PROVIDER_MODELS = 50_000
_REFRESH_SECONDS = 60 * 60
_RETRY_SECONDS = 60
_REFRESH_TIMEOUT_SECONDS = 30
_THINKING_EFFORTS = cast(tuple[ThinkingEffort, ...], get_args(ThinkingEffort))
_DEFAULT_PROVIDER_IDS = {
    "alibaba_model_studio": "alibaba",
    "anthropic": "anthropic",
    "aws_bedrock": "amazon-bedrock",
    "deepseek": "deepseek",
    "google_gemini": "google",
    "google_vertex": "google-vertex",
    "moonshot": "moonshotai-cn",
    "openai": "openai",
    "openrouter": "openrouter",
    "zhipu": "zhipuai",
}


class ModelCatalog(Protocol):
    async def declarations(
        self,
        provider_type: str,
        provider_configuration: Mapping[str, object],
        reference: BaseModelReference,
    ) -> ModelDeclarations: ...


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    model_id: str
    declarations: ModelDeclarations


@dataclass(frozen=True, slots=True)
class _CatalogSnapshot:
    canonical: Mapping[str, _CatalogEntry] = dataclass_field(default_factory=dict)
    provider_models: Mapping[tuple[str, str], _CatalogEntry] = dataclass_field(default_factory=dict)


class ModelsDevCatalog:
    """One lifespan-owned, last-good models.dev snapshot with single-flight refresh."""

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        *,
        clock: Callable[[], float] = monotonic,
        refresh_seconds: float = _REFRESH_SECONDS,
        refresh_timeout_seconds: float = _REFRESH_TIMEOUT_SECONDS,
    ) -> None:
        self._http_client = http_client
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._refresh_timeout_seconds = refresh_timeout_seconds
        self._snapshot = _CatalogSnapshot()
        self._refresh_after = 0.0
        self._lock = Lock()

    async def declarations(
        self,
        provider_type: str,
        provider_configuration: Mapping[str, object],
        reference: BaseModelReference,
    ) -> ModelDeclarations:
        snapshot = await self._current_snapshot()
        provider = _actual_provider_id(provider_type, provider_configuration)
        candidate_ids = tuple(
            dict.fromkeys(value for value in (reference.catalog_model_id, reference.model_name) if value is not None)
        )
        if provider is not None:
            for model_id in candidate_ids:
                if (entry := snapshot.provider_models.get((provider, model_id))) is not None:
                    return entry.declarations
        if reference.catalog_model_id is not None:
            if (entry := snapshot.canonical.get(reference.catalog_model_id)) is not None:
                return entry.declarations
        return ModelDeclarations()

    async def _current_snapshot(self) -> _CatalogSnapshot:
        now = self._clock()
        if now < self._refresh_after:
            return self._snapshot
        async with self._lock:
            now = self._clock()
            if now < self._refresh_after:
                return self._snapshot
            try:
                with fail_after(self._refresh_timeout_seconds):
                    snapshot = await self._download()
            except (TimeoutError, httpx2.HTTPError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                logger.warning(
                    "model_catalog_refresh_failed", extra={"event": "model_catalog_refresh_failed"}, exc_info=True
                )
                self._refresh_after = self._clock() + _RETRY_SECONDS
            else:
                self._snapshot = snapshot
                self._refresh_after = self._clock() + self._refresh_seconds
                logger.info(
                    "model_catalog_refreshed",
                    extra={"event": "model_catalog_refreshed", "canonical_models": len(snapshot.canonical)},
                )
            return self._snapshot

    async def _download(self) -> _CatalogSnapshot:
        async with self._http_client.stream("GET", _CATALOG_URL) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise ValueError("the models.dev catalog exceeds the response limit")
                body.extend(chunk)
        payload = json.loads(body)
        return parse_catalog(payload)


def parse_catalog(payload: Any) -> _CatalogSnapshot:
    if not isinstance(payload, Mapping):
        raise ValueError("the models.dev catalog must be an object")
    raw_models = payload.get("models")
    raw_providers = payload.get("providers")
    if not isinstance(raw_models, Mapping) or not isinstance(raw_providers, Mapping):
        raise ValueError("the models.dev catalog is missing models or providers")
    if len(raw_models) > _MAX_CANONICAL_MODELS or len(raw_providers) > 1_000:
        raise ValueError("the models.dev catalog exceeds its item limit")

    canonical = {
        entry.model_id: entry
        for key, value in raw_models.items()
        if (entry := _entry(key, value, provider_facts=False)) is not None
    }
    provider_models: dict[tuple[str, str], _CatalogEntry] = {}
    for provider_id, provider in raw_providers.items():
        if not isinstance(provider_id, str) or not isinstance(provider, Mapping):
            continue
        models = provider.get("models")
        if not isinstance(models, Mapping):
            continue
        for key, value in models.items():
            if len(provider_models) >= _MAX_PROVIDER_MODELS:
                raise ValueError("the models.dev catalog exceeds its provider-model limit")
            entry = _entry(key, value, provider_facts=True)
            if entry is not None:
                provider_models[(provider_id, entry.model_id)] = entry
    return _CatalogSnapshot(canonical=canonical, provider_models=provider_models)


def merge_declarations(suggested: ModelDeclarations, explicit: ModelDeclarations) -> ModelDeclarations:
    """Overlay only explicitly supplied declaration fields, including pricing leaves."""
    values = suggested.model_dump()
    for field in explicit.model_fields_set - {"pricing"}:
        values[field] = getattr(explicit, field)
    if "pricing" in explicit.model_fields_set:
        if explicit.pricing is None:
            values["pricing"] = None
        else:
            pricing = (suggested.pricing or ModelPricing()).model_dump()
            for field in explicit.pricing.model_fields_set:
                pricing[field] = getattr(explicit.pricing, field)
            values["pricing"] = ModelPricing.model_validate(pricing)
    return ModelDeclarations.model_validate(values)


def _entry(key: Any, value: Any, *, provider_facts: bool) -> _CatalogEntry | None:
    if not isinstance(key, str) or not 1 <= len(key) <= 512 or not isinstance(value, Mapping):
        return None
    model_id = value.get("id", key)
    if not isinstance(model_id, str) or not 1 <= len(model_id) <= 512:
        return None
    return _CatalogEntry(
        model_id=model_id,
        declarations=_declarations(value, provider_facts=provider_facts),
    )


def _declarations(value: Mapping[str, Any], *, provider_facts: bool) -> ModelDeclarations:
    modalities = value.get("modalities")
    inputs = modalities.get("input") if isinstance(modalities, Mapping) else None
    input_values = set(inputs) if isinstance(inputs, Sequence) and not isinstance(inputs, (str, bytes)) else set()
    capability_by_modality = {
        "image": ModelCapability.IMAGE_UNDERSTANDING,
        "audio": ModelCapability.AUDIO_UNDERSTANDING,
        "video": ModelCapability.VIDEO_UNDERSTANDING,
    }
    capabilities = frozenset(
        capability for modality, capability in capability_by_modality.items() if modality in input_values
    )
    limits = value.get("limit")
    context_window_tokens = _positive_int(limits.get("context")) if isinstance(limits, Mapping) else None
    max_output_tokens = _positive_int(limits.get("output")) if isinstance(limits, Mapping) else None
    structured_output = value.get("structured_output")
    if not isinstance(structured_output, bool):
        structured_output = None
    return ModelDeclarations(
        thinking_efforts=_efforts(value) if provider_facts else (),
        capabilities=capabilities,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        structured_output=structured_output,
        pricing=_pricing(value.get("cost")) if provider_facts else None,
    )


def _efforts(value: Mapping[str, Any]) -> tuple[ThinkingEffort, ...]:
    options = value.get("reasoning_options")
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        return ()
    selected: set[str] = set()
    for option in options:
        if not isinstance(option, Mapping) or option.get("type") != "effort":
            continue
        values = option.get("values")
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            selected.update(item for item in values if isinstance(item, str))
    return tuple(effort for effort in _THINKING_EFFORTS if effort in selected)


def _pricing(value: Any) -> ModelPricing | None:
    if not isinstance(value, Mapping):
        return None
    prices = {field: _nonnegative_number(value.get(field)) for field in ModelPricing.model_fields}
    return ModelPricing.model_validate(prices) if any(price is not None for price in prices.values()) else None


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _nonnegative_number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    result = float(value)
    return result if result >= 0 and math.isfinite(result) else None


def _actual_provider_id(provider_type: str, configuration: Mapping[str, object]) -> str | None:
    if any(key.endswith("base_url") and value for key, value in configuration.items()):
        return None
    if provider_type == "alibaba_model_studio":
        return "alibaba-cn" if configuration.get("domain_type") == "mainland_china" else "alibaba"
    return _DEFAULT_PROVIDER_IDS.get(provider_type)


__all__ = ["ModelCatalog", "ModelsDevCatalog", "merge_declarations"]
