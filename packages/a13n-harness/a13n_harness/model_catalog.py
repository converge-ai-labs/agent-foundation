"""Immutable views of curated official model characteristics."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from functools import lru_cache
from types import MappingProxyType

from a13n_harness._official_data import OfficialData, OfficialModelEntry, current_official_data, pricing_provider
from a13n_harness.pricing import _bundled_upstream_catalog


class OfficialModelCatalog(Mapping[str, OfficialModelEntry]):
    """Read-only model declarations captured from one validated official snapshot."""

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
def _catalog_from_data(data: OfficialData) -> OfficialModelCatalog:
    entries: dict[str, OfficialModelEntry] = {}
    pricing = _bundled_upstream_catalog()
    for key, entry in data.models.items():
        # Exact bundled context is a fallback, never a fuzzy media identity.
        provider, _, model = key.partition(":")
        price = pricing.get(f"{pricing_provider(provider)}:{model}")
        if "context_window_tokens" not in entry.characteristics.model_fields_set and price is not None:
            if price.context_window is not None:
                entry = entry.model_copy(
                    update={
                        "characteristics": entry.characteristics.model_copy(
                            update={"context_window_tokens": price.context_window}
                        )
                    }
                )
        entries[key] = entry
    return OfficialModelCatalog(entries)


def get_official_model_catalog() -> OfficialModelCatalog:
    """Read current official facts, falling back to bundled data without network I/O."""
    return _catalog_from_data(current_official_data())


def get_official_model(model: str) -> OfficialModelEntry:
    """Require one exact official model declaration."""
    return get_official_model_catalog()[model]


__all__ = [
    "OfficialModelCatalog",
    "OfficialModelEntry",
    "get_official_model",
    "get_official_model_catalog",
]
