"""Stable Harness pricing catalog and build-time model-cost capabilities."""

from __future__ import annotations

import hashlib
from abc import abstractmethod
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from functools import lru_cache
from importlib.metadata import version
from importlib.resources import files
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

import yaml
from genai_prices.data_snapshot import DataSnapshot, get_snapshot
from genai_prices.types import (
    ConditionalPrice,
    ModelPrice,
    StartDateConstraint,
    Tier,
    TieredPrices,
    TimeOfDateConstraint,
)
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from pydantic_ai.capabilities import AbstractCapability

from a13n_harness.context import AgentContext

if TYPE_CHECKING:
    from pydantic_ai.usage import RequestUsage

MODEL_COST_CAPABILITY_ID = "a13n.usage.model-cost"
_PROVIDER_ALIASES = {
    "google-gla": "google",
    "google-vertex": "google",
}

type PricingConstraintKind = Literal["always", "start_date", "daily_time"]
type ModelCostQuoteSource = Literal["catalog", "custom"]


class PriceTier(BaseModel):
    """One cliff-pricing threshold applied to the complete usage quantity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int = Field(ge=0)
    price: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def _validate_tier(self) -> PriceTier:
        if not self.price.is_finite():
            raise ValueError("tier price must be finite")
        return self


class PriceComponent(BaseModel):
    """One genai-prices usage dimension and its USD unit price."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    price_key: str = Field(min_length=1, max_length=128)
    price: Decimal = Field(ge=0)
    tiers: tuple[PriceTier, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def _validate_component(self) -> PriceComponent:
        if "\x00" in self.price_key or not self.price.is_finite():
            raise ValueError("price component is invalid")
        starts = [tier.start for tier in self.tiers]
        if starts != sorted(starts) or len(starts) != len(set(starts)):
            raise ValueError("price tiers must have unique ascending thresholds")
        return self


class PricingConstraint(BaseModel):
    """A stable condition selecting one ordered model price rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PricingConstraintKind = "always"
    start_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    weekdays: tuple[int, ...] = Field(default=(), max_length=7)

    @model_validator(mode="after")
    def _validate_constraint(self) -> PricingConstraint:
        if self.kind == "always":
            if self.start_date is not None or self.start_time is not None or self.end_time is not None or self.weekdays:
                raise ValueError("always constraint cannot contain condition fields")
        elif self.kind == "start_date":
            if self.start_date is None or self.start_time is not None or self.end_time is not None or self.weekdays:
                raise ValueError("start-date constraint fields are invalid")
        else:
            if self.start_date is not None or self.start_time is None or self.end_time is None:
                raise ValueError("daily-time constraint requires start_time and end_time")
            if self.start_time >= self.end_time:
                raise ValueError("daily-time constraint must not cross midnight")
            for value in self.weekdays:
                if not 0 <= value <= 6:
                    raise ValueError("weekday must be between 0 and 6")
            if len(self.weekdays) != len(set(self.weekdays)):
                raise ValueError("weekdays must be unique")
        return self

    def active(self, timestamp: datetime) -> bool:
        """Return whether this constraint applies at one timezone-aware instant."""
        if timestamp.utcoffset() is None:
            raise ValueError("pricing timestamp must be timezone-aware")
        instant = timestamp.astimezone(UTC)
        if self.kind == "always":
            return True
        if self.kind == "start_date":
            assert self.start_date is not None
            return instant.date() >= self.start_date
        assert self.start_time is not None and self.end_time is not None
        return (
            not self.weekdays or instant.weekday() in self.weekdays
        ) and self.start_time <= instant.time() < self.end_time


class ModelPriceRule(BaseModel):
    """One complete set of prices and the condition selecting it."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    rule_id: str = Field(min_length=1, max_length=128)
    constraint: PricingConstraint = Field(default_factory=PricingConstraint)
    prices: tuple[PriceComponent, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_rule(self) -> ModelPriceRule:
        if "\x00" in self.rule_id or len({item.price_key for item in self.prices}) != len(self.prices):
            raise ValueError("model price rule is invalid")
        return self


class ModelPricingEntry(BaseModel):
    """Complete pricing declaration for one provider-qualified model."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=512)
    context_window: int | None = Field(default=None, ge=1)
    rules: tuple[ModelPriceRule, ...] = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    source_revision: str = Field(min_length=1, max_length=256)
    source_url: HttpUrl | None = None

    @model_validator(mode="after")
    def _validate_entry(self) -> ModelPricingEntry:
        for value in (self.provider, self.model, self.source, self.source_revision):
            if "\x00" in value:
                raise ValueError("pricing entry text must not contain NUL")
        if ":" in self.provider:
            raise ValueError("pricing provider must be unqualified")
        if len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise ValueError("pricing rule IDs must be unique")
        if not any(rule.constraint.kind == "always" for rule in self.rules):
            raise ValueError("pricing entry requires an always rule")
        return self

    @property
    def key(self) -> str:
        """Return the canonical provider-qualified catalog key."""
        return f"{self.provider}:{self.model}"

    def select_rule(self, request_started_at: datetime) -> ModelPriceRule:
        """Select the last active rule, matching genai-prices precedence."""
        for rule in reversed(self.rules):
            if rule.constraint.active(request_started_at):
                return rule
        return self.rules[0]


@dataclass(frozen=True, slots=True)
class ModelCostInput:
    """Content-free input to one deterministic model-cost Capability."""

    model_name: str | None
    provider_name: str | None
    provider_url: str | None
    request_started_at: datetime
    response_timestamp: datetime
    usage: RequestUsage


class ModelCostQuote(BaseModel):
    """Validated USD valuation returned by a model-cost Capability."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    cost_usd: Decimal = Field(ge=0)
    source: ModelCostQuoteSource
    pricing_revision: str = Field(min_length=1, max_length=256)
    rule_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _validate_quote(self) -> ModelCostQuote:
        if not self.cost_usd.is_finite():
            raise ValueError("model cost must be finite")
        for value in (self.pricing_revision, self.rule_id):
            if value is not None and "\x00" in value:
                raise ValueError("model cost quote text must not contain NUL")
        return self


class PricingCatalog(Mapping[str, ModelPricingEntry]):
    """Immutable complete pricing snapshot with shallow replacement updates."""

    def __init__(
        self,
        entries: Mapping[str, ModelPricingEntry],
        *,
        source_snapshot: DataSnapshot | None = None,
    ) -> None:
        copied = dict(entries)
        if any(key != entry.key for key, entry in copied.items()):
            raise ValueError("pricing catalog keys must match provider-qualified entries")
        self._entries = MappingProxyType(copied)
        self._source_snapshot = source_snapshot
        self._revision = _catalog_revision(copied)

    def __getitem__(self, key: str) -> ModelPricingEntry:
        return self._entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def revision(self) -> str:
        """Return a deterministic revision of the complete catalog content."""
        return self._revision

    @property
    def entries(self) -> tuple[ModelPricingEntry, ...]:
        """Return complete entries in canonical key order."""
        return tuple(self._entries[key] for key in sorted(self._entries))

    def resolve(
        self,
        model: str,
        *,
        provider: str | None = None,
        provider_url: str | None = None,
    ) -> ModelPricingEntry | None:
        """Resolve an exact or genai-prices-recognized model reference."""
        model_ref = model.strip()
        provider_id = provider.strip().lower() if provider is not None else None
        if provider_id is not None:
            provider_id = _PROVIDER_ALIASES.get(provider_id, provider_id)
        if ":" in model_ref:
            qualified_provider, model_ref = model_ref.split(":", 1)
            provider_id = provider_id or qualified_provider.lower()
        if provider_id is not None:
            direct = self._entries.get(f"{provider_id}:{model_ref}")
            if direct is not None:
                return direct
        if self._source_snapshot is None:
            return None
        try:
            resolved_provider, resolved_model = self._source_snapshot.find_provider_model(
                model_ref,
                None,
                provider_id,
                provider_url,
            )
        except LookupError:
            return None
        return self._entries.get(f"{resolved_provider.id}:{resolved_model.id}")

    def with_updates(self, updates: Mapping[str, ModelPricingEntry]) -> PricingCatalog:
        """Return a snapshot after complete-entry shallow dictionary replacement."""
        validated: dict[str, ModelPricingEntry] = {}
        for key, value in updates.items():
            if not isinstance(key, str) or not isinstance(value, ModelPricingEntry):
                raise TypeError("pricing updates must map strings to ModelPricingEntry values")
            if key != value.key:
                raise ValueError("pricing update key must match its complete entry")
            validated[key] = value
        merged = dict(self._entries)
        merged.update(validated)
        return PricingCatalog(merged, source_snapshot=self._source_snapshot)

    def model_dump(self, *, mode: Literal["python", "json"] = "python") -> dict[str, Any]:
        """Export the complete catalog through stable Harness-owned schemas."""
        return {
            "revision": self.revision,
            "entries": {key: self._entries[key].model_dump(mode=mode) for key in sorted(self._entries)},
        }


@dataclass(init=False)
class AbstractModelCostCapability(AbstractCapability[AgentContext]):
    """Build-time extension point owning one Agent's model-cost policy."""

    id = MODEL_COST_CAPABILITY_ID

    @property
    def enabled(self) -> bool:
        """Return whether Harness valuation is enabled."""
        return True

    @property
    @abstractmethod
    def revision(self) -> str:
        """Return the immutable policy revision."""
        raise NotImplementedError

    @abstractmethod
    def quote(self, value: ModelCostInput) -> ModelCostQuote | None:
        """Return a deterministic quote or decline this response."""
        raise NotImplementedError


@dataclass(init=False)
class CatalogModelCostCapability(AbstractModelCostCapability):
    """Default model-cost implementation over one immutable pricing catalog."""

    def __init__(
        self,
        *,
        catalog: PricingCatalog | None = None,
        pricing_updates: Mapping[str, ModelPricingEntry] | None = None,
    ) -> None:
        selected = get_default_pricing_catalog() if catalog is None else catalog
        if not isinstance(selected, PricingCatalog):
            raise TypeError("catalog must be a PricingCatalog")
        self.catalog = selected.with_updates(pricing_updates or {})

    @property
    def revision(self) -> str:
        return self.catalog.revision

    def quote(self, value: ModelCostInput) -> ModelCostQuote | None:
        if value.model_name is None:
            return None
        entry = self.catalog.resolve(
            value.model_name,
            provider=value.provider_name,
            provider_url=value.provider_url,
        )
        if entry is None:
            return None
        rule = entry.select_rule(value.request_started_at)
        prices: dict[str, Decimal | TieredPrices] = {}
        for component in rule.prices:
            if component.tiers:
                prices[component.price_key] = TieredPrices(
                    base=component.price,
                    tiers=[Tier(start=tier.start, price=tier.price) for tier in component.tiers],
                )
            else:
                prices[component.price_key] = component.price
        calculation = ModelPrice(**prices).calc_price(value.usage)
        return ModelCostQuote(
            cost_usd=calculation["total_price"],
            source="catalog",
            pricing_revision=self.catalog.revision,
            rule_id=rule.rule_id,
        )


@dataclass(init=False)
class NoModelCostCapability(AbstractModelCostCapability):
    """Explicitly disable Harness model valuation while preserving upstream cost."""

    @property
    def enabled(self) -> bool:
        return False

    @property
    def revision(self) -> str:
        return "disabled"

    def quote(self, value: ModelCostInput) -> ModelCostQuote | None:
        return None


@lru_cache(maxsize=1)
def get_default_pricing_catalog() -> PricingCatalog:
    """Return the immutable genai-prices snapshot plus packaged replacements."""
    snapshot = deepcopy(get_snapshot())
    entries = _entries_from_snapshot(snapshot)
    entries.update(_load_packaged_updates())
    return PricingCatalog(entries, source_snapshot=snapshot)


def _entries_from_snapshot(snapshot: DataSnapshot) -> dict[str, ModelPricingEntry]:
    package_revision = version("genai-prices")
    snapshot_revision = (
        f"genai-prices:{package_revision}:auto:{snapshot.timestamp.isoformat()}"
        if snapshot.from_auto_update
        else f"genai-prices:{package_revision}:bundled"
    )
    entries: dict[str, ModelPricingEntry] = {}
    for provider in snapshot.providers:
        source_url = provider.pricing_urls[0] if provider.pricing_urls else None
        for model in provider.models:
            raw_rules = model.prices if isinstance(model.prices, list) else [ConditionalPrice(prices=model.prices)]
            normalized_rules: list[ModelPriceRule] = []
            for index, rule in enumerate(raw_rules):
                components = _components_from_genai(rule.prices)
                if not components:
                    continue
                normalized_rules.append(
                    ModelPriceRule(
                        rule_id=f"rule-{index}",
                        constraint=_constraint_from_genai(rule.constraint),
                        prices=components,
                    )
                )
            rules = tuple(normalized_rules)
            if not rules or not any(rule.constraint.kind == "always" for rule in rules):
                continue
            entry = ModelPricingEntry(
                provider=provider.id,
                model=model.id,
                context_window=model.context_window,
                rules=rules,
                source="genai_prices",
                source_revision=snapshot_revision,
                source_url=HttpUrl(source_url) if source_url is not None else None,
            )
            entries[entry.key] = entry
    return entries


def _constraint_from_genai(value: StartDateConstraint | TimeOfDateConstraint | None) -> PricingConstraint:
    if value is None:
        return PricingConstraint()
    if isinstance(value, StartDateConstraint):
        return PricingConstraint(kind="start_date", start_date=value.start_date)
    if isinstance(value, TimeOfDateConstraint):
        start = value.start_time.replace(tzinfo=None)
        end = value.end_time.replace(tzinfo=None)
        return PricingConstraint(kind="daily_time", start_time=start, end_time=end)
    raise TypeError("unsupported genai-prices constraint")


def _components_from_genai(value: ModelPrice) -> tuple[PriceComponent, ...]:
    components: list[PriceComponent] = []
    for price_key, raw_price in sorted(value.__dict__.items()):
        if price_key.startswith("_") or raw_price is None:
            continue
        if isinstance(raw_price, TieredPrices):
            components.append(
                PriceComponent(
                    price_key=price_key,
                    price=raw_price.base,
                    tiers=tuple(PriceTier(start=tier.start, price=tier.price) for tier in raw_price.tiers),
                )
            )
        elif isinstance(raw_price, Decimal):
            components.append(PriceComponent(price_key=price_key, price=raw_price))
        else:
            raise TypeError("unsupported genai-prices component")
    return tuple(components)


def _load_packaged_updates() -> dict[str, ModelPricingEntry]:
    resource = files("a13n_harness").joinpath("data/pricing-overrides.yaml")
    raw = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise RuntimeError("bundled pricing overlay has an unsupported schema")
    raw_entries = raw.get("entries")
    if not isinstance(raw_entries, dict):
        raise RuntimeError("bundled pricing overlay must contain an entry mapping")
    entries: dict[str, ModelPricingEntry] = {}
    for key, value in raw_entries.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise RuntimeError("bundled pricing overlay contains an invalid entry")
        entry = ModelPricingEntry.model_validate(value)
        if key != entry.key:
            raise RuntimeError("bundled pricing overlay key does not match its entry")
        entries[key] = entry
    return entries


def _catalog_revision(entries: Mapping[str, ModelPricingEntry]) -> str:
    payload = "\n".join(f"{key}\x00{entries[key].model_dump_json()}" for key in sorted(entries)).encode("utf-8")
    return f"pricing-{hashlib.sha256(payload).hexdigest()[:24]}"


__all__ = [
    "MODEL_COST_CAPABILITY_ID",
    "AbstractModelCostCapability",
    "CatalogModelCostCapability",
    "ModelCostInput",
    "ModelCostQuote",
    "ModelCostQuoteSource",
    "ModelPriceRule",
    "ModelPricingEntry",
    "NoModelCostCapability",
    "PriceComponent",
    "PriceTier",
    "PricingCatalog",
    "PricingConstraint",
    "PricingConstraintKind",
    "get_default_pricing_catalog",
]
