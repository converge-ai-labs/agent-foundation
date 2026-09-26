from __future__ import annotations

import json
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from a13n_harness import (
    AgentContext,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessState,
    ModelRecoveryPolicy,
    RunBindings,
)
from a13n_harness.pricing import (
    AbstractModelCostCapability,
    ModelCostInput,
    ModelCostQuote,
)
from a13n_harness.tools import (
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from a13n_harness.usage import (
    BoundedRequestUsage,
    ModelUsageRecord,
    ProviderUsage,
    ProviderUsageRecord,
    RunUsageLedger,
    UsageMeasure,
)
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.agent import ModelRequestNode
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


def _metadata() -> HarnessToolMetadata:
    return HarnessToolMetadata(
        tool_id="metered.search",
        effects=frozenset({"read", "external_communication"}),
        credential_audiences=(),
        idempotency="read_only",
        output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
    )


async def _allow(*args: Any, **kwargs: Any) -> InvocationPolicyDecision:
    del args, kwargs
    return InvocationPolicyDecision.allow()


def _mixed_usage_model() -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returned:
            yield {
                0: DeltaToolCall(
                    name="metered_search",
                    json_args=json.dumps({}),
                    tool_call_id="call-1",
                )
            }
        else:
            yield "done"

    return FunctionModel(stream_function=stream)


async def test_each_model_request_reports_mixed_usage_once() -> None:
    receipt = ProviderUsage(
        usage_id="provider-request-1",
        provider="search-provider",
        product="web-search",
        timestamp=datetime.now(UTC),
        measures=(UsageMeasure(unit="request", quantity=Decimal(1)),),
        cost=Decimal("0.004"),
        currency="usd",
    )

    async def metered_search(ctx: RunContext[Any]) -> dict[str, bool]:
        await ctx.deps.record_provider_usage(
            receipt,
            source="managed_tool",
            tool_id="metered.search",
            tool_call_id=ctx.tool_call_id,
        )
        # Re-reporting the same provider receipt is idempotent.
        await ctx.deps.record_provider_usage(
            receipt,
            source="managed_tool",
            tool_id="metered.search",
            tool_call_id=ctx.tool_call_id,
        )
        return {"ok": True}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_mixed_usage_model(),
        capabilities=(
            Capability(
                tools=[HarnessTool(metered_search, harness_metadata=_metadata())],
                id="metered-tools",
            ),
        ),
    )

    async with executable.stream(
        "go",
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=_allow),)),
    ) as stream:
        assert isinstance(stream.context.usage_attribution, RunUsageLedger)
        items = [item async for item in stream]
        ledger_records = stream.context.usage_attribution.records

    reports = [
        item.event.payload
        for item in items
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.kind == "usage"
    ]
    result = items[-1].result

    assert [report["reason"] for report in reports] == ["model_request", "provider", "model_request"]
    assert [len(report["records"]) for report in reports] == [1, 1, 1]
    assert len(result.usage_records) == 3
    assert ledger_records == result.usage_records
    assert [record.kind for record in result.usage_records] == ["model", "provider", "model"]
    provider = result.usage_records[1]
    assert isinstance(provider, ProviderUsageRecord)
    assert provider.usage.cost == Decimal("0.004")
    assert provider.usage.currency == "USD"
    assert len({record.record_id for record in result.usage_records}) == 3
    assert result.usage.requests == 2

    repeated = await executable.run(
        "go again",
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=_allow),)),
    )
    repeated_provider = next(record for record in repeated.usage_records if isinstance(record, ProviderUsageRecord))
    assert repeated_provider.record_id == provider.record_id
    assert repeated.run_id != result.run_id


async def test_recovery_reports_interrupted_and_completed_model_requests() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            yield "partial"
            raise ConnectionResetError("disconnected")
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )

    async with executable.stream("go", bindings=RunBindings.embedded()) as stream:
        items = [item async for item in stream]

    result = items[-1].result
    model_records = [record for record in result.usage_records if isinstance(record, ModelUsageRecord)]
    usage_events = [
        item
        for item in items
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.kind == "usage"
    ]

    assert [record.response_ordinal for record in model_records] == [0, 1]
    assert [record.response_state for record in model_records] == ["interrupted", "complete"]
    assert len({record.model_run_id for record in model_records}) == 2
    assert len(usage_events) == 2
    assert result.usage.requests == 2


@dataclass(kw_only=True)
class _LateProviderUsageCapability(AbstractCapability[AgentContext]):
    receipt: ProviderUsage
    id: str | None = "test.late-provider-usage"

    async def wrap_node_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        node: Any,
        handler: Any,
    ) -> Any:
        result = await handler(node)
        if isinstance(node, ModelRequestNode):
            await ctx.deps.record_provider_usage(
                self.receipt,
                source="test.after_model_request",
            )
        return result


async def test_provider_usage_after_final_model_request_is_reported_at_terminal() -> None:
    receipt = ProviderUsage(
        usage_id="late-provider-request",
        provider="late-provider",
        product="post-model-operation",
        timestamp=datetime.now(UTC),
        measures=(UsageMeasure(unit="request", quantity=Decimal(1)),),
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(_LateProviderUsageCapability(receipt=receipt),),
    )

    async with executable.stream("go", bindings=RunBindings.embedded()) as run:
        items = [item async for item in run]

    reports = [
        item.event.payload
        for item in items
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.kind == "usage"
    ]
    result = items[-1].result

    assert [report["reason"] for report in reports] == ["model_request", "provider"]
    assert [len(report["records"]) for report in reports] == [1, 1]
    assert isinstance(result.usage_records[-1], ProviderUsageRecord)
    assert result.usage_records[-1].record_id == reports[-1]["records"][0]["record_id"]


class _FixedCostCapability(AbstractModelCostCapability):
    def __init__(self, *, inputs: list[ModelCostInput]) -> None:
        self.inputs = inputs

    @property
    def revision(self) -> str:
        return "catalog-7"

    def quote(self, value: ModelCostInput) -> ModelCostQuote:
        self.inputs.append(value)
        return ModelCostQuote(
            cost_usd=Decimal("0.125"),
            source="custom",
            pricing_revision=self.revision,
            rule_id="fixed",
        )


class _FailingCostCapability(AbstractModelCostCapability):
    def __init__(self, stage: str) -> None:
        self.stage = stage

    @property
    def enabled(self) -> bool:
        if self.stage == "enabled":
            raise RuntimeError("enabled failed")
        return True

    @property
    def revision(self) -> str:
        if self.stage == "revision":
            raise RuntimeError("revision failed")
        return "failing-1"

    def quote(self, value: ModelCostInput) -> ModelCostQuote | None:
        del value
        if self.stage == "quote":
            raise RuntimeError("quote failed")
        return None


@pytest.mark.parametrize("stage", ("enabled", "revision", "quote"))
async def test_custom_model_cost_failure_does_not_fail_the_agent_run(stage: str) -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(_FailingCostCapability(stage),),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    record = result.usage_records[0]
    assert isinstance(record, ModelUsageRecord)
    assert record.pricing_status == "failed"
    assert record.request_usage.cost is None


async def test_custom_model_cost_is_applied_before_native_accumulation() -> None:
    inputs: list[ModelCostInput] = []
    cost_capability = _FixedCostCapability(inputs=inputs)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(cost_capability,),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())

    assert result.usage.cost == Decimal("0.125")
    assert len(inputs) == 1
    assert inputs[0].usage.cost is None
    model_record = result.usage_records[0]
    assert isinstance(model_record, ModelUsageRecord)
    assert model_record.request_usage.cost == Decimal("0.125")
    assert model_record.pricing_revision == "catalog-7"
    assert model_record.pricing_status == "applied"
    assert model_record.pricing_rule_id == "fixed"
    assert model_record.cost_source == "custom"


@dataclass
class _RetryFirstResponse(AbstractCapability[AgentContext]):
    calls: int = 0

    async def after_model_request(self, ctx, *, request_context, response):
        del ctx, request_context
        self.calls += 1
        if self.calls == 1:
            raise ModelRetry("retry once")
        return response


@dataclass
class _ReplaceResponseCost(AbstractCapability[AgentContext]):
    async def after_model_request(self, ctx, *, request_context, response):
        del ctx, request_context
        return replace(response, usage=replace(response.usage, cost=Decimal("0.5")))


@dataclass
class _FailAfterResponse(AbstractCapability[AgentContext]):
    async def after_model_request(self, ctx, *, request_context, response):
        del ctx, request_context, response
        raise RuntimeError("post-response failure")


async def test_custom_pricing_survives_retry_of_a_committed_response() -> None:
    inputs: list[ModelCostInput] = []
    retry = _RetryFirstResponse()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(retry, _FixedCostCapability(inputs=inputs)),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())

    model_records = [record for record in result.usage_records if isinstance(record, ModelUsageRecord)]
    assert retry.calls == 2
    assert result.usage.requests == 2
    assert result.usage.cost == Decimal("0.250")
    assert [record.request_usage.cost for record in model_records] == [Decimal("0.125"), Decimal("0.125")]
    assert [record.pricing_status for record in model_records] == ["applied", "applied"]


async def test_later_response_replacement_cannot_mislabel_custom_cost() -> None:
    inputs: list[ModelCostInput] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(_ReplaceResponseCost(), _FixedCostCapability(inputs=inputs)),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())

    record = result.usage_records[0]
    assert isinstance(record, ModelUsageRecord)
    assert result.usage.cost == Decimal("0.125")
    assert record.request_usage.cost == Decimal("0.125")
    assert record.pricing_status == "applied"
    assert record.cost_source == "custom"


async def test_post_response_failure_retains_captured_usage() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(_FailAfterResponse(),),
    )
    async with executable.stream("go", bindings=RunBindings.embedded()) as run:
        items = [item async for item in run]

    assert items[-1].result.status == "failed"
    assert any(
        isinstance(item, HarnessEvent) and isinstance(item.event, HarnessExtensionEvent) and item.event.kind == "usage"
        for item in items
    )


def test_sensitive_usage_detail_keys_are_omitted_without_corrupting_report_shape() -> None:
    projected = BoundedRequestUsage.from_request_usage(RequestUsage(details={"token": 1, "reasoning_tokens": 2}))
    assert projected.details == {"reasoning_tokens": 2}


async def test_imported_history_is_not_reattributed_on_resume() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        turn = sum(isinstance(message, ModelResponse) for message in messages) + 1
        yield f"turn-{turn}"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    first = await executable.run("one", bindings=RunBindings.embedded())
    state = first.state
    assert isinstance(state, HarnessState)

    second = await executable.run("two", bindings=RunBindings.embedded(), previous_state=state)

    first_records = [record for record in first.usage_records if isinstance(record, ModelUsageRecord)]
    second_records = [record for record in second.usage_records if isinstance(record, ModelUsageRecord)]
    assert len(first_records) == 1
    assert len(second_records) == 1
    assert first_records[0].record_id != second_records[0].record_id
    assert second.usage.requests == 1


# Nesting only changes parent identity; cancellation keeps both placements because it crosses the child run.
@pytest.mark.parametrize(
    ("outcome", "inline"),
    (("success", False), ("failure", True), ("cancelled", False), ("cancelled", True)),
)
async def test_auxiliary_model_responses_share_pricing_and_ledger_not_native_budget(outcome: str, inline: bool) -> None:
    import asyncio

    from a13n_harness.metering import ModelUsageBinding
    from a13n_harness.toolsets.file_media import (
        AgentMediaUnderstandingProvider,
        MediaUnderstandingError,
        MediaUnderstandingRequest,
    )
    from pydantic_ai.messages import TextPart

    inputs: list[ModelCostInput] = []
    calls = 0

    async def analyze(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 2 and outcome == "cancelled":
            raise asyncio.CancelledError
        return ModelResponse(
            parts=[TextPart("valid" if calls == 2 and outcome == "success" else " ")],
            model_name="media-actual",
            provider_name="media-provider",
            usage=RequestUsage(input_tokens=7, output_tokens=2, cache_read_tokens=3),
        )

    from a13n_harness import AgentDefinition

    from .test_delegation import _bindings_factory, _parent_definition, _returns_after_latest_user

    provider = AgentMediaUnderstandingProvider(models={"image": FunctionModel(analyze, model_name="media-actual")})

    async def metered_search(ctx: RunContext[AgentContext]) -> str:
        before = deepcopy(ctx.usage)
        binding = ModelUsageBinding.for_context(
            ctx.deps, source="files.media_understanding", tool_id="filesystem.view", tool_call_id=ctx.tool_call_id
        )
        try:
            result = await provider.understand(
                MediaUnderstandingRequest(
                    kind="image",
                    media_type="image/png",
                    source_name="image.png",
                    source_bytes=b"image",
                ),
                usage=binding,
            )
        except asyncio.CancelledError:
            assert outcome == "cancelled"
        except MediaUnderstandingError as exc:
            assert outcome == "failure"
            assert exc.usage == ()
        else:
            assert outcome == "success"
            assert result.usage == ()  # No second provider receipt for the same model response.
        assert ctx.usage == before
        return "done"

    definition = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        model=_mixed_usage_model(),
        capabilities=(
            _FixedCostCapability(inputs=inputs),
            Capability(tools=[HarnessTool(metered_search, harness_metadata=_metadata())], id="media-tools"),
        ),
    )
    bindings = RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=_allow),))
    if inline:

        async def parent_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
            if not _returns_after_latest_user(messages):
                yield {
                    0: DeltaToolCall(name="delegate", json_args=json.dumps({"subagent": "reviewer", "prompt": "view"}))
                }
            else:
                yield "done"

        definition = _parent_definition(
            definition,
            FunctionModel(stream_function=parent_stream),
            capabilities=(_FixedCostCapability(inputs=inputs),),
        )
        bindings = _bindings_factory()
    executable = HarnessBuilder().build(definition)
    async with executable.stream("go", bindings=bindings) as stream:
        items = [item async for item in stream]
    records = [
        ModelUsageRecord.model_validate(record)
        for item in items
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.kind == "usage"
        for record in item.event.payload["records"]
    ]
    media = [r for r in records if isinstance(r, ModelUsageRecord) and r.source == "files.media_understanding"]
    expected = {"success": 2, "failure": 3, "cancelled": 2}[outcome]
    known = [r for r in media if r.usage_status != "unavailable"]
    assert len(media) == expected
    assert all((r.parent_agent_instance_id is not None) == inline for r in media)
    assert all(r.model_name == "media-actual" and r.provider_name == "media-provider" for r in known)
    assert all(r.tool_id == "filesystem.view" and r.tool_call_id == "call-1" for r in media)
    assert all(r.request_usage.cost == Decimal("0.125") and r.pricing_revision == "catalog-7" for r in known)
    assert all(r.request_usage.input_tokens == 7 for r in known)
    assert len({r.record_id for r in records}) == len(records)
    assert not any(isinstance(r, ProviderUsageRecord) for r in records)
    assert len([value for value in inputs if value.model_name == "media-actual"]) == len(known)
    reports = [
        item.event.payload
        for item in items
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.kind == "usage"
    ]
    assert reports
