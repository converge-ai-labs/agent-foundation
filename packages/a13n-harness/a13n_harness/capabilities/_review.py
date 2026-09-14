"""Shared streaming and usage accounting for auxiliary tool reviewers."""

from collections.abc import AsyncIterable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from pydantic_ai import RunContext
from pydantic_ai.messages import AgentStreamEvent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from a13n_harness.usage import ProviderUsage, UsageMeasure


async def drain_review_events(ctx: RunContext[None], events: AsyncIterable[AgentStreamEvent]) -> None:
    del ctx
    async for _ in events:
        pass


def provider_usage_receipts(model: Model, usage: RunUsage) -> tuple[ProviderUsage, ...]:
    measures = tuple(
        UsageMeasure(unit=unit, quantity=Decimal(value))
        for unit, value in (
            ("requests", usage.requests),
            ("tool_calls", usage.tool_calls),
            ("input_tokens", usage.input_tokens),
            ("cache_write_tokens", usage.cache_write_tokens),
            ("cache_read_tokens", usage.cache_read_tokens),
            ("output_tokens", usage.output_tokens),
        )
        if value
    )
    if not measures:
        return ()
    return (
        ProviderUsage(
            usage_id=f"tool-review-{uuid4()}",
            provider=model.system,
            product=model.model_name,
            timestamp=datetime.now(UTC),
            measures=measures,
        ),
    )
