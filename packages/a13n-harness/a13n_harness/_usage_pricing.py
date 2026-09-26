"""One valuation path for ordinary and auxiliary model responses."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic_ai.messages import ModelResponse

from a13n_harness.context import AgentContext
from a13n_harness.pricing import AbstractModelCostCapability, ModelCostInput, ModelCostQuote
from a13n_harness.usage import CostSource, PricingStatus


@dataclass(frozen=True, slots=True)
class _PricingOutcome:
    status: PricingStatus
    revision: str | None
    rule_id: str | None
    quote_source: Literal["catalog", "custom"] | None


def price_response(
    response: ModelResponse,
    *,
    capability: AbstractModelCostCapability,
    model_id: str | None,
    request_started_at: datetime,
) -> _PricingOutcome:
    revision: str | None = None
    status: PricingStatus = "failed"
    priced = response
    quote: ModelCostQuote | None = None
    try:
        enabled = capability.enabled
        revision = capability.revision
        if not isinstance(enabled, bool):
            raise TypeError("model-cost Capability enabled flag must be a boolean")
        if not isinstance(revision, str) or not revision:
            raise TypeError("model-cost Capability revision must be a non-empty string")
        status = "disabled" if not enabled else "declined"
        if enabled and not response.usage.has_values() and response.usage.cost is None:
            status = "not_reached"
        elif enabled:
            usage = deepcopy(response.usage)
            usage.cost = None
            value = ModelCostInput(
                selected_model_id=model_id,
                model_name=response.model_name,
                provider_name=response.provider_name,
                provider_url=_safe_provider_url(response.provider_url),
                request_started_at=request_started_at,
                response_timestamp=response.timestamp,
                usage=usage,
                service_tier=_response_service_tier(response),
            )
            quote = capability.quote(value)
            if quote is not None:
                if not isinstance(quote, ModelCostQuote):
                    raise TypeError("model-cost Capability returned an incompatible quote")
                status = "applied"
                response.usage.cost = quote.cost_usd
    except Exception:
        status = "failed"
        quote = None
    outcome = _PricingOutcome(
        status=status,
        revision=quote.pricing_revision if quote is not None else revision,
        rule_id=quote.rule_id if quote is not None else None,
        quote_source=quote.source if quote is not None else None,
    )
    _enrich_current_model_span(priced, outcome)
    return outcome


async def _pricing_diagnostic(
    context: AgentContext,
    response: ModelResponse,
    revision: str | None,
) -> None:
    from a13n_harness.events import HarnessExtensionEvent

    await context.events.emit(
        HarnessExtensionEvent(
            kind="diagnostic",
            payload={
                "type": "model_pricing_failed",
                "model_name": response.model_name,
                "provider_name": response.provider_name,
                "pricing_revision": revision,
            },
        )
    )


def _response_service_tier(response: ModelResponse) -> str | None:
    """Read only the bounded served tier, never the requested routing preference."""
    details = response.provider_details or {}
    value = details.get("service_tier")
    if response.provider_name == "google-vertex" and details.get("traffic_type") is not None:
        # Vertex exposes actual serving separately from the Developer API header.
        traffic_type = details["traffic_type"]
        if not isinstance(traffic_type, str):
            raise ValueError("response service tier must be a bounded identifier")
        value = traffic_type.lower()
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or not value.isascii()
        or not value[0].islower()
        or any(not (char.islower() or char.isdigit() or char in "_-") for char in value)
    ):
        raise ValueError("response service tier must be a bounded identifier")
    return value


def _safe_provider_url(value: str | None) -> str | None:
    if value is None or len(value.encode("utf-8")) > 2048 or "\x00" in value:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        _ = parsed.port
    except ValueError:
        return None
    return value


def _cost_source(response: ModelResponse, pricing: _PricingOutcome | None) -> CostSource:
    if response.usage.cost is None:
        return "unknown"
    if pricing is None:
        return "provider_or_genai_prices"
    if pricing.status == "applied" and pricing.quote_source is not None:
        return pricing.quote_source
    return "provider_or_genai_prices"


def _enrich_current_model_span(response: ModelResponse, pricing: _PricingOutcome) -> None:
    """Add bounded custom-pricing facts to the active Pydantic-owned model span."""
    from a13n_harness.observation import _set_current_model_span_attributes

    attributes: dict[str, str | float] = {
        "a13n.usage.cost.source": _cost_source(response, pricing),
        "a13n.usage.pricing.status": pricing.status,
    }
    if response.usage.cost is not None:
        attributes["gen_ai.usage.cost"] = float(response.usage.cost)
    if pricing.revision is not None:
        attributes["a13n.usage.pricing.revision"] = pricing.revision
    if pricing.rule_id is not None:
        attributes["a13n.usage.pricing.rule.id"] = pricing.rule_id
    _set_current_model_span_attributes(attributes)
