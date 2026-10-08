"""Immutable package-local catalog of official upstream model characteristics."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from a13n_harness.pricing import get_default_pricing_catalog
from a13n_harness.spec import HarnessModelCharacteristics


class OfficialModelEntry(BaseModel):
    """One explicitly selected official upstream model declaration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(min_length=1, max_length=512)
    characteristics: HarnessModelCharacteristics
    source_url: HttpUrl

    @model_validator(mode="after")
    def _validate_entry(self) -> OfficialModelEntry:
        if "\x00" in self.model or ":" not in self.model:
            raise ValueError("official model must use a provider-qualified model name")
        return self

    @property
    def key(self) -> str:
        """Return the canonical catalog key."""
        return self.model


class OfficialModelCatalog(Mapping[str, OfficialModelEntry]):
    """Read-only official model declarations shipped with one Harness release."""

    def __init__(self, entries: Mapping[str, OfficialModelEntry]) -> None:
        copied = dict(entries)
        if any(key != entry.key for key, entry in copied.items()):
            raise ValueError("official model catalog keys must match entry model names")
        self._entries = MappingProxyType(copied)

    def __getitem__(self, key: str) -> OfficialModelEntry:
        return self._entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[OfficialModelEntry, ...]:
        """Return entries in canonical key order."""
        return tuple(self._entries[key] for key in sorted(self._entries))


@lru_cache(maxsize=1)
def get_official_model_catalog() -> OfficialModelCatalog:
    """Load and validate the immutable catalog bundled with this Harness release."""
    resource = files("a13n_harness").joinpath("data/official-models.yaml")
    raw = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise RuntimeError("bundled official model catalog has an unsupported schema")
    raw_entries = raw.get("models")
    if not isinstance(raw_entries, dict):
        raise RuntimeError("bundled official model catalog must contain a model mapping")
    entries: dict[str, OfficialModelEntry] = {}
    pricing = get_default_pricing_catalog()
    for key, value in raw_entries.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise RuntimeError("bundled official model catalog contains an invalid entry")
        entry = OfficialModelEntry.model_validate({"model": key, **value})
        if entry.key in entries:
            raise RuntimeError(f"bundled official model catalog contains duplicate model {entry.key!r}")
        # Reuse release-pinned context facts instead of maintaining a second copy.
        # Exact keys only: pricing's fuzzy aliases must not establish media identity.
        provider, _, model = entry.key.partition(":")
        price_provider = {"grok": "x-ai", "google-gla": "google"}.get(provider, provider)
        price = pricing.get(f"{price_provider}:{model}")
        if "context_window_tokens" not in entry.characteristics.model_fields_set and price is not None:
            if price.context_window is not None:
                entry = entry.model_copy(
                    update={
                        "characteristics": entry.characteristics.model_copy(
                            update={"context_window_tokens": price.context_window}
                        )
                    }
                )
        entries[entry.key] = entry
    return OfficialModelCatalog(entries)


def get_official_model(model: str) -> OfficialModelEntry:
    """Require one exact official model declaration."""
    return get_official_model_catalog()[model]


__all__ = [
    "OfficialModelCatalog",
    "OfficialModelEntry",
    "get_official_model",
    "get_official_model_catalog",
]
