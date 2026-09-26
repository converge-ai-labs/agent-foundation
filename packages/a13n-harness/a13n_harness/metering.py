"""Meter the public Model boundary, independently of Agent response commitment."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from copy import copy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import anyio
from a13n_logging import get_logger
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, WrapModelRequestHandler
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from a13n_harness._usage_pricing import (
    _cost_source,
    _enrich_current_model_span,
    _pricing_diagnostic,
    _PricingOutcome,
    price_response,
)
from a13n_harness.context import AgentContext
from a13n_harness.errors import StateError
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.model_calls import _check_model_call
from a13n_harness.models.self_healing import SelfHealingModel
from a13n_harness.pricing import AbstractModelCostCapability, CatalogModelCostCapability
from a13n_harness.providers.usage import ProviderUsage, UsageMeasure
from a13n_harness.request_budget import RequestBudget
from a13n_harness.usage import (
    TOKEN_COUNTERS,
    BoundedRequestUsage,
    ModelUsageObservation,
    ModelUsageRecord,
    RunUsageLedger,
    UsageCapability,
    _stable_id,
    update_observation,
)

logger = get_logger(__name__)
_STATE_KEY = "a13n_usage"


@dataclass(frozen=True)
class ModelUsageBinding:
    """Explicit attribution and price policy for a model-using collaborator."""

    ledger: RunUsageLedger
    cost: AbstractModelCostCapability
    owner: AgentContext | None = None
    source: str = "agent"
    tool_id: str | None = None
    tool_call_id: str | None = None

    @classmethod
    def standalone(cls, *, source: str) -> ModelUsageBinding:
        instance = AgentInstanceContext(
            identity=AgentIdentityRef(issuer="local", subject=source),
            agent_instance_id=f"agent-{uuid4().hex}",
        )
        return cls(
            RunUsageLedger(run_id=f"run-{uuid4().hex}", instance=instance), CatalogModelCostCapability(), source=source
        )

    @classmethod
    def for_context(
        cls, context: AgentContext, *, source: str, tool_id: str | None = None, tool_call_id: str | None = None
    ) -> ModelUsageBinding:
        ledger = context.usage_attribution
        return cls(
            ledger, ledger.cost_capability or CatalogModelCostCapability(), context, source, tool_id, tool_call_id
        )

    def receipts(self) -> tuple[ProviderUsage, ...]:
        """Project standalone model facts into the existing auxiliary receipt contract."""
        receipts = []
        for record in self.ledger.records:
            if not isinstance(record, ModelUsageRecord):
                continue
            usage = record.request_usage
            values = {
                "requests": Decimal(1),
                **{name: Decimal(getattr(usage, name)) for name in TOKEN_COUNTERS},
                "audio_seconds": usage.audio_seconds,
            }
            receipts.append(
                ProviderUsage(
                    usage_id=record.call_id or record.record_id,
                    provider=record.provider_name or "unknown",
                    product=record.model_name or "unknown",
                    timestamp=record.response_timestamp,
                    measures=tuple(UsageMeasure(unit=name, quantity=value) for name, value in values.items() if value),
                    cost=usage.cost,
                    currency="USD" if usage.cost is not None else None,
                )
            )
        return tuple(receipts)


class ModelUsageCapability(UsageCapability):
    def __init__(self, binding: ModelUsageBinding) -> None:
        self.binding = binding

    async def for_run(self, ctx: RunContext[Any]) -> AbstractCapability[Any]:
        return self

    async def wrap_model_request(
        self, ctx: RunContext[Any], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        return await meter_request(self.binding, ctx, request_context, handler)


async def meter_request(
    binding: ModelUsageBinding, ctx: RunContext[Any], request: ModelRequestContext, handler: WrapModelRequestHandler
) -> ModelResponse:
    if isinstance(request.model, SelfHealingModel):
        selected = copy(request.model)
        model = MeteredModel(replace(request, model=selected.wrapped), binding, ctx.run_id)
        selected.wrapped = model
    else:
        selected = model = MeteredModel(request, binding, ctx.run_id)
    response = await handler(replace(request, model=selected))
    # Preserve the existing logical-request trace enrichment after native stream finalization.
    if model.pricing is not None:
        from a13n_harness.usage import summarize_usage

        response.usage.cost = summarize_usage(model.records.values()).cost
        _enrich_current_model_span(response, model.pricing)
    return response


class MeteredModel(WrapperModel):
    """Request-local adapter: lazy streams are counted only when actually entered."""

    def __init__(self, request: ModelRequestContext, binding: ModelUsageBinding, model_run_id: str | None) -> None:
        super().__init__(request.model)
        self.request_context = request
        self.binding = binding
        self.model_run_id = model_run_id
        self.pricing: _PricingOutcome | None = None
        self.records: dict[str, ModelUsageRecord] = {}

    async def _admit(self, messages: list[ModelMessage]) -> tuple[str, ModelUsageRecord | None, RequestBudget | None]:
        binding = self.binding
        tail = messages[-1] if messages else None
        continuation = (
            tail.provider_response_id if isinstance(tail, ModelResponse) and tail.state == "suspended" else None
        )
        state = (tail.metadata or {}).get(_STATE_KEY) if isinstance(tail, ModelResponse) and continuation else None
        previous = None
        if continuation is not None:
            if not isinstance(state, dict) or state.get("usage_id") != binding.ledger.usage_id:
                raise StateError(
                    "A suspended provider generation requires resume_usage=True in its original scope.",
                    code="usage_resume_required",
                )
            record_id = state.get("record_id")
            if not isinstance(record_id, str):
                raise StateError("Provider continuation is missing its usage identity.", code="usage_state_missing")
            previous = binding.ledger.generation(record_id)
            if previous.provider_response_id != continuation or previous.model_id != self.request_context.model_id:
                raise StateError("Provider continuation does not match its usage state.", code="usage_state_mismatch")
        call_id = f"call_{uuid4().hex}"
        binding.ledger.reserve(call_id, continuation=previous is not None)
        try:
            if binding.owner is None:
                return call_id, previous, None
            budget = await _check_model_call(
                binding.owner,
                self.request_context,
                call_id=call_id,
                model_run_id=self.model_run_id,
                source=binding.source,
                tool_id=binding.tool_id,
                tool_call_id=binding.tool_call_id,
                continuation_of=continuation if previous is not None else None,
            )
            return call_id, previous, budget
        except BaseException:
            binding.ledger.requests.cancel(call_id)
            raise

    def _snapshot(
        self,
        response: ModelResponse | None,
        call_id: str,
        started: datetime,
        error: BaseException | None,
        seed: ModelUsageRecord | None,
    ) -> tuple[ModelUsageRecord, _PricingOutcome | None]:
        binding, ledger = self.binding, self.binding.ledger
        value = response or ModelResponse(parts=[], model_name=self.model_name, provider_name=self.system)
        # Only a continuation with the same provider generation may revise an earlier fact.
        previous = (
            seed
            if seed is not None
            and seed.provider_response_id == value.provider_response_id
            and seed.model_id == self.request_context.model_id
            else None
        )
        known = value.usage.has_values() or value.usage.cost is not None
        pricing = price_response(
            value,
            capability=binding.cost,
            model_id=self.request_context.model_id,
            request_started_at=(previous.request_started_at or started) if previous is not None else started,
        )
        usage = BoundedRequestUsage.from_request_usage(value.usage)
        observation = ModelUsageObservation(
            response_state=("interrupted" if error is not None and value.state != "complete" else value.state)
            if response is not None
            else "unavailable",
            model_name=value.model_name or self.model_name,
            provider_name=value.provider_name or self.system,
            response_timestamp=value.timestamp,
            request_usage=usage,
            usage_status="unavailable" if not known else "complete" if value.state == "complete" else "partial",
            outcome="completed" if error is None else "failed" if isinstance(error, Exception) else "cancelled",
            pricing_revision=pricing.revision,
            pricing_rule_id=pricing.rule_id,
            pricing_status=pricing.status,
            cost_source=_cost_source(value, pricing) if usage.cost is not None else "unknown",
        )
        if previous is not None:
            record = update_observation(previous, observation)
            pricing = replace(
                pricing,
                revision=record.pricing_revision,
                rule_id=record.pricing_rule_id,
                status=record.pricing_status,
                quote_source=pricing.quote_source if record.request_usage.cost is not None else None,
            )
        else:
            record = ModelUsageRecord(
                **observation.model_dump(),
                record_id=_stable_id("model", ledger.run_id, call_id),
                run_id=ledger.run_id,
                response_ordinal=ledger.next_model_ordinal(),
                call_id=call_id,
                request_started_at=started,
                model_run_id=self.model_run_id,
                model_id=self.request_context.model_id,
                provider_response_id=value.provider_response_id,
                agent_instance_id=ledger.instance.agent_instance_id,
                parent_agent_instance_id=ledger.instance.parent_agent_instance_id,
                delegation_id=ledger.instance.delegation_id,
                source=binding.source,
                tool_id=binding.tool_id,
                tool_call_id=binding.tool_call_id,
            )
        return record, pricing

    async def _capture(
        self,
        response: ModelResponse | None,
        call_id: str,
        started: datetime,
        error: BaseException | None,
        seed: ModelUsageRecord | None,
        host_budget: RequestBudget | None,
    ) -> None:
        binding, ledger = self.binding, self.binding.ledger
        try:
            # A failed poll has no new snapshot; deliver the existing fact without
            # inventing a revision that was never saved in a continuation checkpoint.
            record, pricing = (
                (seed, None)
                if response is None and seed is not None
                else self._snapshot(response, call_id, started, error, seed)
            )
            refusal = ledger.finish(call_id, record, host_budget)
            self.records[record.record_id] = record
            self.pricing = pricing
            if response is not None:
                if response.metadata is None:
                    response.metadata = {}
                response.metadata[_STATE_KEY] = {"usage_id": ledger.usage_id, "record_id": record.record_id}
            await ledger._flush(reason="model_request", trigger_record_id=record.record_id)
            if (
                pricing is not None
                and pricing.status == "failed"
                and binding.owner is not None
                and response is not None
            ):
                try:
                    with anyio.move_on_after(5, shield=True):
                        await _pricing_diagnostic(binding.owner, response, pricing.revision)
                except Exception:
                    logger.warning("Pricing diagnostic delivery failed")
            if error is None and refusal is not None:
                raise refusal
        except Exception:
            if error is None:
                raise
            logger.error("Usage cleanup failed while preserving the model failure")
        finally:
            ledger.requests.cancel(call_id)
            if host_budget is not None:
                host_budget.cancel(call_id)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        call_id, seed, host_budget = await self._admit(messages)
        started, response, error = datetime.now(UTC), None, None
        try:
            response = await super().request(messages, model_settings, model_request_parameters)
            return response
        except BaseException as exc:
            error = exc
            raise
        finally:
            await self._capture(response, call_id, started, error, seed, host_budget)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        call_id, seed, host_budget = await self._admit(messages)
        started, stream, error = datetime.now(UTC), None, None
        try:
            async with super().request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as stream:
                yield stream
        except BaseException as exc:
            error = exc
            raise
        finally:
            if stream is not None and stream.metadata is None:
                stream.metadata = {}
            await self._capture(
                stream.get() if stream is not None else None, call_id, started, error, seed, host_budget
            )
