"""Explicit per-request prices of the model a request selected, by its selected model ID."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from types import MappingProxyType

from .pricing import AbstractModelCostCapability, ModelCostInput, ModelCostQuote, ModelPricingEntry


class TokenPricingCapability(AbstractModelCostCapability):
    """Price selected model IDs against complete pricing entries.

    An entry prices the selected model's requests whatever provider and model it names.
    """

    def __init__(self, models: Mapping[str, ModelPricingEntry | None]) -> None:
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
        return pricing.quote(value, source="custom", revision=self.revision)
