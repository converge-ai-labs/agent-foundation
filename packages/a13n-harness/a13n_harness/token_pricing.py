"""Explicit per-request token prices, selected by total input length."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Literal

from genai_prices.types import ModelPrice
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .pricing import AbstractModelCostCapability, ModelCostInput, ModelCostQuote

TokenPrice = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class TokenRates(BaseModel):
    """USD per million tokens; null is unknown, never free."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: TokenPrice | None = None
    output: TokenPrice | None = None
    cache_read: TokenPrice | None = None
    cache_write: TokenPrice | None = None


class TokenPriceTier(BaseModel):
    """Complete rates for requests strictly above the input threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    above: int | None = Field(default=None, ge=0, strict=True)
    rates: TokenRates


class TokenPricing(BaseModel):
    """Cliff prices: one input-length tier prices the entire request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: Literal["USD"] = "USD"
    unit: Literal["million_tokens"] = "million_tokens"
    tier_basis: Literal["input_tokens"] = "input_tokens"
    tiers: tuple[TokenPriceTier, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_tiers(self) -> TokenPricing:
        if self.tiers[0].above is not None:
            raise ValueError("the first tier must be the base tier with above=null")
        previous = -1
        for tier in self.tiers[1:]:
            if tier.above is None or tier.above <= previous:
                raise ValueError("tier thresholds must be unique and strictly ascending")
            previous = tier.above
        return self

    def rates_for(self, input_tokens: int) -> TokenRates:
        if input_tokens < 0:
            raise ValueError("input tokens cannot be negative")
        for tier in reversed(self.tiers):
            if tier.above is None or input_tokens > tier.above:
                return tier.rates
        raise AssertionError("validated pricing always has a base tier")


class TokenPricingCapability(AbstractModelCostCapability):
    """Price selected model IDs against frozen, Host-authored tables."""

    def __init__(self, models: Mapping[str, TokenPricing | None]) -> None:
        self.models = MappingProxyType(dict(models))
        payload = "\n".join(
            f"{key}:{value.model_dump_json() if value else 'null'}" for key, value in sorted(models.items())
        )
        self._revision = "token-pricing-" + hashlib.sha256(payload.encode()).hexdigest()[:24]

    @property
    def revision(self) -> str:
        return self._revision

    def quote(self, value: ModelCostInput) -> ModelCostQuote | None:
        pricing = self.models.get(value.selected_model_id) if value.selected_model_id is not None else None
        if pricing is None:
            return None
        usage = value.usage
        # Audio has its own pricing dimensions; a text-token table is insufficient.
        if usage.input_audio_tokens or usage.output_audio_tokens or usage.cache_audio_read_tokens:
            return None
        uncached = usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens
        counts = {
            "input": uncached,
            "output": usage.output_tokens,
            "cache_read": usage.cache_read_tokens,
            "cache_write": usage.cache_write_tokens,
        }
        if any(count < 0 for count in counts.values()):
            return None
        rates = pricing.rates_for(usage.input_tokens)
        if any(count and getattr(rates, name) is None for name, count in counts.items()):
            return None
        # Reuse genai-prices' inclusive-cache accounting instead of a second formula.
        prices = ModelPrice(**{f"{name}_mtok": getattr(rates, name) or Decimal(0) for name in counts})
        cost = prices.calc_price(usage)["total_price"]
        selected = next(
            tier for tier in reversed(pricing.tiers) if tier.above is None or usage.input_tokens > tier.above
        )
        return ModelCostQuote(
            cost_usd=cost,
            source="custom",
            pricing_revision=self.revision,
            rule_id="base" if selected.above is None else f"above-{selected.above}",
        )
