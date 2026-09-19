"""Provider-owned usage receipts without Run attribution."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class UsageMeasure(BaseModel):
    """One provider-neutral quantity reported by a non-model usage owner."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    unit: str = Field(min_length=1, max_length=128)
    quantity: Decimal

    @model_validator(mode="after")
    def _validate_measure(self) -> UsageMeasure:
        if "\x00" in self.unit or not self.quantity.is_finite() or self.quantity < 0:
            raise ValueError("usage measure is invalid")
        return self


class ProviderUsage(BaseModel):
    """Stable provider receipt or metered contribution from any Harness-owned path."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    usage_id: str = Field(min_length=1, max_length=512)
    provider: str = Field(min_length=1, max_length=256)
    product: str = Field(min_length=1, max_length=256)
    timestamp: datetime
    measures: tuple[UsageMeasure, ...] = Field(default=(), max_length=64)
    cost: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=8)

    @field_validator("usage_id", "provider", "product")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("provider usage text must not contain NUL")
        return value

    @model_validator(mode="after")
    def _validate_usage(self) -> ProviderUsage:
        if self.timestamp.utcoffset() is None:
            raise ValueError("provider usage timestamp must be timezone-aware")
        if not self.measures and self.cost is None:
            raise ValueError("provider usage must contain a measure or cost")
        if len({item.unit for item in self.measures}) != len(self.measures):
            raise ValueError("provider usage measure units must be unique")
        if self.cost is None:
            if self.currency is not None:
                raise ValueError("provider usage currency requires a cost")
        else:
            if not self.cost.is_finite() or self.cost < 0 or self.currency is None:
                raise ValueError("provider usage cost is invalid")
            object.__setattr__(self, "currency", self.currency.upper())
        return self
