"""One validated snapshot backing official model facts and pricing supplements."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from a13n_harness.pricing import ModelPricingEntry
from a13n_harness.spec import HarnessModelCharacteristics


def pricing_provider(provider: str) -> str:
    """Translate catalog namespaces, without fuzzy model matching."""
    return {"grok": "x-ai", "google-gla": "google"}.get(provider, provider)


class OfficialModelEntry(BaseModel):
    """One explicitly selected official upstream model declaration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(min_length=1, max_length=512)
    characteristics: HarnessModelCharacteristics
    source_url: HttpUrl

    @model_validator(mode="after")
    def _validate_entry(self) -> OfficialModelEntry:
        provider, separator, name = self.model.partition(":")
        if "\x00" in self.model or not separator or not provider or not name:
            raise ValueError("official model must use a provider-qualified model name")
        return self

    @property
    def key(self) -> str:
        return self.model


@dataclass(frozen=True, eq=False)
class OfficialData:
    """Both immutable views are validated before this snapshot is published."""

    models: Mapping[str, OfficialModelEntry]
    pricing: Mapping[str, ModelPricingEntry]


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = super().construct_mapping(node, deep=deep)
        if len(mapping) != len(node.value):
            raise ValueError("official catalog contains duplicate mapping keys")
        return mapping


def parse_official_data(content: str | bytes) -> OfficialData:
    """Validate the complete versioned document; unknown fields fail closed."""
    raw = yaml.load(content, Loader=_UniqueLoader)
    if not isinstance(raw, dict) or raw.get("schema_version") != 2 or set(raw) != {"schema_version", "models"}:
        raise ValueError("official catalog has an unsupported schema")
    records = raw["models"]
    if not isinstance(records, dict) or not records:
        raise ValueError("official catalog must contain a nonempty model mapping")
    models: dict[str, OfficialModelEntry] = {}
    prices: dict[str, ModelPricingEntry] = {}
    for key, value in records.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise ValueError("official catalog contains an invalid model entry")
        if set(value) - {"characteristics", "source_url", "pricing"}:
            raise ValueError("official model contains unsupported fields")
        provider, separator, name = key.partition(":")
        if not separator or not provider or not name or "\x00" in key:
            raise ValueError("official model must use a provider-qualified model name")
        facts = value.get("characteristics")
        if facts is not None:
            models[key] = OfficialModelEntry.model_validate(
                {"model": key, "characteristics": facts, "source_url": value.get("source_url")}
            )
        elif "characteristics" in value or "source_url" in value:
            raise ValueError("official model characteristics and source must be declared together")
        price = value.get("pricing")
        if price is not None:
            if not isinstance(price, dict) or set(price) - {"source", "source_revision", "source_url", "rules"}:
                raise ValueError("official pricing contains unsupported fields")
            entry = ModelPricingEntry.model_validate(
                {
                    **price,
                    "provider": pricing_provider(provider),
                    "model": name,
                    "context_window": models[key].characteristics.context_window_tokens if key in models else None,
                }
            )
            if entry.key in prices:
                raise ValueError("official catalog contains duplicate pricing identities")
            prices[entry.key] = entry
        elif "pricing" in value:
            raise ValueError("official pricing must be omitted rather than null")
        if facts is None and price is None:
            raise ValueError("official model requires characteristics or pricing")
    return OfficialData(MappingProxyType(models), MappingProxyType(prices))


@lru_cache(maxsize=1)
def bundled_official_data() -> OfficialData:
    resource = files("a13n_harness").joinpath("data/official-models.yaml")
    return parse_official_data(resource.read_bytes())


_current: OfficialData | None = None


def current_official_data() -> OfficialData:
    """Read one snapshot reference, without starting network work."""
    return _current if _current is not None else bundled_official_data()


def publish_official_data(candidate: OfficialData) -> None:
    """Publish both validated views together; previous snapshots remain unchanged."""
    global _current
    _current = candidate
