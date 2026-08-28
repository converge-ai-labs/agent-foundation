"""Run-local mixed-usage attribution without replacing Pydantic AI accounting."""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai import RunContext
from pydantic_ai.agent import ModelRequestNode
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.usage import RequestUsage

from a13n_harness._json import dump_json_bytes, is_sensitive_key
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError, RunError
from a13n_harness.identity import AgentInstanceContext
from a13n_harness.pricing import (
    MODEL_COST_CAPABILITY_ID,
    AbstractModelCostCapability,
    ModelCostInput,
    ModelCostQuote,
)

if TYPE_CHECKING:
    from a13n_harness.events import HarnessEventEmitter

USAGE_CAPABILITY_ID = "a13n.usage"
_MAX_RECORDS = 10_000
_MAX_REPORT_BYTES = 56 * 1024
_MAX_REPORT_RECORDS = 64
_MAX_USAGE_DETAILS = 64
_MAX_COUNTER = 2**63 - 1

type CostSource = Literal[
    "catalog",
    "custom",
    "provider_or_genai_prices",
    "unknown",
]
type PricingStatus = Literal[
    "applied",
    "declined",
    "failed",
    "disabled",
    "not_reached",
]
type UsageReportReason = Literal["model_request", "terminal"]


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


class BoundedRequestUsage(BaseModel):
    """Safe fixed-shape projection of one Pydantic request usage value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    cache_write_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    cache_read_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    output_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    input_audio_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    cache_audio_read_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    output_audio_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    details: dict[str, int] = Field(default_factory=dict)
    cost: Decimal | None = None

    @model_validator(mode="after")
    def _validate_projection(self) -> BoundedRequestUsage:
        if len(self.details) > _MAX_USAGE_DETAILS:
            raise ValueError("request usage has too many detail counters")
        for key, value in self.details.items():
            if not key or len(key) > 128 or "\x00" in key or not 0 <= value <= _MAX_COUNTER:
                raise ValueError("request usage detail is invalid")
        if self.cost is not None and (not self.cost.is_finite() or self.cost < 0):
            raise ValueError("request usage cost is invalid")
        return self

    @classmethod
    def from_request_usage(cls, usage: RequestUsage) -> BoundedRequestUsage:
        """Copy supported counters and omit unsafe provider detail instead of leaking it."""
        details = {
            key: value
            for key, value in sorted(usage.details.items())
            if isinstance(key, str)
            and bool(key)
            and len(key) <= 128
            and "\x00" not in key
            and not is_sensitive_key(key)
            and isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= _MAX_COUNTER
        }
        return cls(
            input_tokens=usage.input_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            output_tokens=usage.output_tokens,
            input_audio_tokens=usage.input_audio_tokens,
            cache_audio_read_tokens=usage.cache_audio_read_tokens,
            output_audio_tokens=usage.output_audio_tokens,
            details=dict(tuple(details.items())[:_MAX_USAGE_DETAILS]),
            cost=usage.cost,
        )


class ModelUsageRecord(BaseModel):
    """One model response proven to have entered the native RunUsage accumulator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["model"] = "model"
    record_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=256)
    response_ordinal: int = Field(ge=0)
    model_run_id: str | None = Field(default=None, max_length=256)
    agent_instance_id: str = Field(min_length=1, max_length=512)
    parent_agent_instance_id: str | None = Field(default=None, max_length=512)
    delegation_id: str | None = Field(default=None, max_length=512)
    response_state: str = Field(min_length=1, max_length=64)
    model_name: str | None = Field(default=None, max_length=1024)
    provider_name: str | None = Field(default=None, max_length=512)
    response_timestamp: datetime
    request_usage: BoundedRequestUsage
    pricing_revision: str | None = Field(default=None, max_length=256)
    pricing_rule_id: str | None = Field(default=None, max_length=128)
    cost_source: CostSource
    pricing_status: PricingStatus


class ProviderUsageRecord(BaseModel):
    """One deduplicated non-model usage contribution with run and tool attribution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["provider"] = "provider"
    record_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=256)
    ordinal: int = Field(ge=0)
    agent_instance_id: str = Field(min_length=1, max_length=512)
    parent_agent_instance_id: str | None = Field(default=None, max_length=512)
    delegation_id: str | None = Field(default=None, max_length=512)
    source: str = Field(min_length=1, max_length=256)
    tool_id: str | None = Field(default=None, max_length=256)
    tool_call_id: str | None = Field(default=None, max_length=1024)
    usage: ProviderUsage

    @field_validator("source", "tool_id", "tool_call_id")
    @classmethod
    def _validate_text(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("usage attribution text must not contain NUL")
        return value


type UsageRecord = ModelUsageRecord | ProviderUsageRecord


@dataclass(frozen=True, slots=True)
class _PricingOutcome:
    status: PricingStatus
    revision: str | None
    rule_id: str | None
    quote_source: Literal["catalog", "custom"] | None
    original_cost_present: bool
    calculated_cost: Decimal | None
    response: ModelResponse


class RunUsageLedger:
    """Append-only mixed-usage attribution ledger for one logical Harness run."""

    def __init__(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
        events: HarnessEventEmitter,
    ) -> None:
        self.run_id = run_id
        self._instance = instance
        self._events = events
        self._records: list[UsageRecord] = []
        self._records_by_id: dict[str, UsageRecord] = {}
        self._reported_index = 0
        self._model_ordinal = 0
        self._flush_lock = asyncio.Lock()

    @property
    def records(self) -> tuple[UsageRecord, ...]:
        """Return a detached complete run-local attribution snapshot."""
        return tuple(record.model_copy(deep=True) for record in self._records)

    async def _record_provider(
        self,
        usage: ProviderUsage,
        *,
        source: str,
        tool_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> ProviderUsageRecord:
        """Deduplicate one source receipt and retain it for the next report boundary."""
        detached = (
            usage.model_copy(deep=True) if isinstance(usage, ProviderUsage) else ProviderUsage.model_validate(usage)
        )
        record_id = _stable_id("provider", detached.provider, detached.product, detached.usage_id)
        candidate = ProviderUsageRecord(
            record_id=record_id,
            run_id=self.run_id,
            ordinal=len(self._records),
            agent_instance_id=self._instance.agent_instance_id,
            parent_agent_instance_id=self._instance.parent_agent_instance_id,
            delegation_id=self._instance.delegation_id,
            source=source,
            tool_id=tool_id,
            tool_call_id=tool_call_id,
            usage=detached,
        )
        existing = self._records_by_id.get(record_id)
        if existing is not None:
            if not isinstance(existing, ProviderUsageRecord):
                raise RunError("Usage record identity collision.", code="usage_record_conflict")
            comparable = candidate.model_copy(update={"ordinal": existing.ordinal})
            if existing != comparable:
                raise RunError(
                    "A provider usage ID was reused with different attribution.",
                    code="usage_record_conflict",
                )
            return existing.model_copy(deep=True)
        self._append(candidate)
        return candidate.model_copy(deep=True)

    async def _record_model(
        self,
        response: ModelResponse,
        *,
        pricing: _PricingOutcome | None,
    ) -> ModelUsageRecord:
        """Append and immediately report one proven native response commit."""
        ordinal = self._model_ordinal
        self._model_ordinal += 1
        status: PricingStatus = pricing.status if pricing is not None else "not_reached"
        revision = pricing.revision if pricing is not None else None
        rule_id = pricing.rule_id if pricing is not None else None
        cost_source = _cost_source(response, pricing)
        record = ModelUsageRecord(
            record_id=_stable_id("model", self.run_id, str(ordinal)),
            run_id=self.run_id,
            response_ordinal=ordinal,
            model_run_id=response.run_id,
            agent_instance_id=self._instance.agent_instance_id,
            parent_agent_instance_id=self._instance.parent_agent_instance_id,
            delegation_id=self._instance.delegation_id,
            response_state=response.state,
            model_name=response.model_name,
            provider_name=response.provider_name,
            response_timestamp=response.timestamp,
            request_usage=BoundedRequestUsage.from_request_usage(response.usage),
            pricing_revision=revision,
            pricing_rule_id=rule_id,
            cost_source=cost_source,
            pricing_status=status,
        )
        self._append(record)
        await self._flush(reason="model_request", trigger_record_id=record.record_id)
        return record.model_copy(deep=True)

    async def _flush(
        self,
        *,
        reason: UsageReportReason,
        trigger_record_id: str | None = None,
    ) -> None:
        """Emit all records not included in an earlier report, split into stable bounded chunks."""
        from a13n_harness.events import UsageReportPayload, emit_harness_event

        async with self._flush_lock:
            checkpoint = len(self._records)
            pending = self._records[self._reported_index : checkpoint]
            if not pending:
                return
            chunks = _report_chunks(pending)
            report_id = _stable_id(
                "report",
                self.run_id,
                reason,
                trigger_record_id or "terminal",
                str(_record_ordinal(pending[0])),
            )
            for chunk_index, chunk in enumerate(chunks):
                await emit_harness_event(
                    self._events,
                    kind="usage",
                    payload=UsageReportPayload(
                        report_id=report_id,
                        reason=reason,
                        trigger_record_id=trigger_record_id,
                        chunk_index=chunk_index,
                        chunk_count=len(chunks),
                        records=tuple(record.model_dump(mode="json") for record in chunk),
                    ),
                )
            self._reported_index = checkpoint

    def _append(self, record: UsageRecord) -> None:
        if len(self._records) >= _MAX_RECORDS:
            raise RunError("Run usage attribution capacity was exceeded.", code="usage_capacity_exceeded")
        self._records.append(record)
        self._records_by_id[record.record_id] = record


@dataclass(init=False)
class UsageCapability(AbstractCapability[AgentContext]):
    """Core response-commit observer and mandatory model-cost integration."""

    id = USAGE_CAPABILITY_ID

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(USAGE_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _UsageActiveCapability):
                raise DefinitionError("Usage has an incompatible run replacement.", code="capability_type_mismatch")
            return existing
        replacement = _UsageActiveCapability(context=ctx.deps)
        ctx.deps._record_run_capability(USAGE_CAPABILITY_ID, replacement)
        return replacement

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")


@dataclass(init=False)
class _UsageActiveCapability(UsageCapability):
    def __init__(self, *, context: AgentContext) -> None:
        self._context = context
        self._cost_capability: AbstractModelCostCapability | None = None
        self._request_started_at: dict[str, datetime] = {}
        self._pending_pricing: dict[str, _PricingOutcome] = {}

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self._context:
            raise DefinitionError("Usage run replacement cannot cross logical runs.", code="capability_scope_invalid")
        return self

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        self._require_context(ctx)
        if ctx.run_id is not None:
            self._request_started_at[ctx.run_id] = datetime.now(UTC)
        return request_context

    async def after_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        self._require_context(ctx)
        capability = self._resolve_cost_capability(ctx)
        original_cost_present = response.usage.cost is not None
        request_started_at = (
            self._request_started_at.pop(ctx.run_id, None) if ctx.run_id is not None else None
        ) or response.timestamp
        revision: str | None = None
        status: PricingStatus = "failed"
        priced = response
        calculated_cost: Decimal | None = None
        quote: ModelCostQuote | None = None
        try:
            enabled = capability.enabled
            revision = capability.revision
            if not isinstance(enabled, bool):
                raise TypeError("model-cost Capability enabled flag must be a boolean")
            if not isinstance(revision, str) or not revision:
                raise TypeError("model-cost Capability revision must be a non-empty string")
            status = "disabled" if not enabled else "declined"
            if enabled:
                usage = deepcopy(response.usage)
                usage.cost = None
                value = ModelCostInput(
                    model_name=response.model_name,
                    provider_name=response.provider_name,
                    provider_url=_safe_provider_url(response.provider_url),
                    request_started_at=request_started_at,
                    response_timestamp=response.timestamp,
                    usage=usage,
                )
                quote = capability.quote(value)
                if quote is not None:
                    if not isinstance(quote, ModelCostQuote):
                        raise TypeError("model-cost Capability returned an incompatible quote")
                    status = "applied"
                    calculated_cost = quote.cost_usd
                    response.usage.cost = quote.cost_usd
        except Exception:
            status = "failed"
            quote = None
            calculated_cost = None
            try:
                await _pricing_diagnostic(ctx, response, revision)
            except Exception:
                pass
        if ctx.run_id is not None:
            self._pending_pricing[ctx.run_id] = _PricingOutcome(
                status=status,
                revision=quote.pricing_revision if quote is not None else revision,
                rule_id=quote.rule_id if quote is not None else None,
                quote_source=quote.source if quote is not None else None,
                original_cost_present=original_cost_present,
                calculated_cost=calculated_cost,
                response=priced,
            )
        return priced

    async def wrap_node_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        node: Any,
        handler: Any,
    ) -> Any:
        self._require_context(ctx)
        if not isinstance(node, ModelRequestNode):
            return await handler(node)
        requests_before = ctx.usage.requests
        try:
            result = await handler(node)
        except BaseException:
            await self._record_committed_boundary(ctx, requests_before=requests_before)
            raise
        await self._record_committed_boundary(ctx, requests_before=requests_before)
        return result

    async def _record_committed_boundary(self, ctx: RunContext[AgentContext], *, requests_before: int) -> None:
        if ctx.usage.requests <= requests_before:
            return
        pricing = self._pending_pricing.pop(ctx.run_id, None) if ctx.run_id is not None else None
        response: ModelResponse | None = None
        if ctx.messages and isinstance(ctx.messages[-1], ModelResponse):
            tail = ctx.messages[-1]
            if ctx.run_id is None or tail.run_id == ctx.run_id:
                response = tail
        if response is None:
            return
        await ctx.deps.usage_attribution._record_model(
            response,
            pricing=_pricing_for_committed_response(pricing, response),
        )

    def _resolve_cost_capability(self, ctx: RunContext[AgentContext]) -> AbstractModelCostCapability:
        if self._cost_capability is not None:
            return self._cost_capability
        inherited = ctx.deps._inherited_model_cost
        if inherited is not None:
            self._cost_capability = inherited
            return inherited
        capability = ctx.capabilities.get(MODEL_COST_CAPABILITY_ID)
        if not isinstance(capability, AbstractModelCostCapability):
            raise DefinitionError("Model-cost Capability has an incompatible type.", code="capability_type_mismatch")
        if MODEL_COST_CAPABILITY_ID in ctx.deps._capability_provenance.run_ids:
            raise DefinitionError(
                "Model-cost Capability cannot originate from RunBindings.",
                code="capability_scope_invalid",
            )
        self._cost_capability = capability
        return capability

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        if ctx.deps is not self._context:
            raise DefinitionError("Usage Capability cannot cross logical runs.", code="capability_scope_invalid")
        owner = ctx.capabilities.get(USAGE_CAPABILITY_ID)
        if type(owner) is not _UsageActiveCapability or owner is not self:
            raise DefinitionError(
                "The finalized Usage owner has an incompatible identity.",
                code="capability_scope_invalid",
            )


async def _pricing_diagnostic(
    ctx: RunContext[AgentContext],
    response: ModelResponse,
    revision: str | None,
) -> None:
    from a13n_harness.events import HarnessExtensionEvent

    await ctx.deps.events.emit(
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


def _pricing_for_committed_response(
    pricing: _PricingOutcome | None,
    response: ModelResponse,
) -> _PricingOutcome | None:
    if pricing is None:
        return None
    same_response = pricing.response is response or (
        pricing.response.run_id is not None
        and pricing.response.run_id == response.run_id
        and pricing.response.timestamp == response.timestamp
        and pricing.response.model_name == response.model_name
        and pricing.response.provider_name == response.provider_name
    )
    if not same_response:
        return None
    if pricing.status == "applied" and response.usage.cost != pricing.calculated_cost:
        return None
    return pricing


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


def _stable_id(kind: str, *values: str) -> str:
    digest = hashlib.sha256("\x00".join((kind, *values)).encode("utf-8")).hexdigest()[:24]
    return f"usage-{digest}"


def _record_ordinal(record: UsageRecord) -> int:
    return record.response_ordinal if isinstance(record, ModelUsageRecord) else record.ordinal


def _report_chunks(records: list[UsageRecord]) -> list[list[UsageRecord]]:
    chunks: list[list[UsageRecord]] = []
    current: list[UsageRecord] = []
    for record in records:
        candidate = [*current, record]
        encoded = dump_json_bytes([item.model_dump(mode="json") for item in candidate])
        if current and (len(candidate) > _MAX_REPORT_RECORDS or len(encoded) > _MAX_REPORT_BYTES):
            chunks.append(current)
            current = [record]
        else:
            current = candidate
        if len(dump_json_bytes([item.model_dump(mode="json") for item in current])) > _MAX_REPORT_BYTES:
            raise RunError("One usage record exceeds the report size bound.", code="usage_record_too_large")
    if current:
        chunks.append(current)
    return chunks


__all__ = [
    "BoundedRequestUsage",
    "CostSource",
    "ModelUsageRecord",
    "PricingStatus",
    "ProviderUsage",
    "ProviderUsageRecord",
    "RunUsageLedger",
    "UsageMeasure",
    "UsageRecord",
]
