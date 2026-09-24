"""Awaited dispatch checks and committed usage identity."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from a13n_harness import AgentDefinition, HarnessBuilder, RunBindings
from a13n_harness.errors import RunError
from a13n_harness.model_calls import ModelCall
from a13n_harness.usage import ModelUsageRecord
from pydantic_ai import ModelRetry
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


@dataclass
class Checks:
    calls: list[ModelCall] = field(default_factory=list)
    error: BaseException | None = None

    async def check(self, call: ModelCall) -> None:
        self.calls.append(call)
        await asyncio.sleep(0)
        if isinstance(self.error, TimeoutError):
            async with asyncio.timeout(0):
                await asyncio.Event().wait()
        if self.error is not None:
            raise self.error


@pytest.mark.parametrize("error", [PermissionError("denied"), TimeoutError("deadline"), asyncio.CancelledError()])
async def test_failed_check_never_enters_provider(error):
    providers = []
    checks = Checks(error=error)

    async def provider(messages, info):
        providers.append("entered")
        yield "unexpected"

    executable = HarnessBuilder().build(
        AgentDefinition(agent=AgentSpec(), output_type=str, model=FunctionModel(stream_function=provider))
    )
    async with executable.stream("go", bindings=RunBindings.embedded(model_call_check=checks)) as stream:
        with pytest.raises(RunError, match="model-call check"):
            async for _ in stream:
                pass
        assert not stream.context.usage_attribution.records
    assert len(checks.calls) == 1
    assert providers == []


async def test_two_calls_one_native_run_keep_distinct_dispatch_and_existing_ordinal_identity():
    checks = Checks()

    async def echo():
        return "tool returned"

    async def provider(messages, info):
        if any(isinstance(part, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for part in m.parts):
            yield "done"
        else:
            yield {0: DeltaToolCall(name="echo", json_args="{}", tool_call_id="echo-1")}

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=provider),
            capabilities=(Capability(id="tools", tools=[echo]),),
        )
    )
    result = await executable.run("go", bindings=RunBindings.embedded(model_call_check=checks))
    assert result.output_or_raise() == "done"
    assert len(checks.calls) == 2
    assert len({call.model_run_id for call in checks.calls}) == 1
    assert len({call.call_id for call in checks.calls}) == 2
    records = result.usage_records
    assert [record.call_id for record in records] == [call.call_id for call in checks.calls]
    assert [record.response_ordinal for record in records] == [0, 1]
    assert all(record.run_id == checks.calls[0].harness_run_id for record in records)
    assert len({record.record_id for record in records}) == 2


async def test_retry_producing_response_retains_dispatch_identity():
    checks = Checks()

    class RetryOnce(AbstractCapability):
        calls = 0

        async def after_model_request(self, ctx, *, request_context, response):
            self.calls += 1
            if self.calls == 1:
                raise ModelRetry("try again")
            return response

    async def provider(messages, info):
        yield "done"

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=provider),
            capabilities=(RetryOnce(),),
        )
    )
    result = await executable.run("go", bindings=RunBindings.embedded(model_call_check=checks))
    assert result.output_or_raise() == "done"
    assert len(checks.calls) == 2
    assert [record.call_id for record in result.usage_records] == [call.call_id for call in checks.calls]


async def test_dispatched_pre_yield_failure_preserves_native_interrupted_accounting():
    checks = Checks()

    provider_calls = []

    class UnavailableModel(Model):
        @property
        def model_name(self):
            return "unavailable"

        @property
        def system(self):
            return "fixture"

        async def request(self, messages, model_settings, model_request_parameters):
            raise ModelHTTPError(status_code=401, model_name=self.model_name)

        @asynccontextmanager
        async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
            provider_calls.append("dispatched")
            raise ModelHTTPError(status_code=401, model_name=self.model_name)
            yield

    executable = HarnessBuilder().build(AgentDefinition(agent=AgentSpec(), output_type=str, model=UnavailableModel()))
    result = await executable.run("go", bindings=RunBindings.embedded(model_call_check=checks))
    assert result.status == "failed"
    assert provider_calls == ["dispatched"] and len(checks.calls) == 1
    assert result.usage.requests == 1
    assert len(result.usage_records) == 1
    record = result.usage_records[0]
    assert record.call_id == checks.calls[0].call_id
    assert record.response_state == "interrupted"
    assert record.request_usage.input_tokens == record.request_usage.output_tokens == 0
    assert record.request_usage.cost is None
    assert record.cost_source == "unknown" and record.pricing_status == "not_reached"


async def test_embedded_without_check_accepts_missing_call_correlation():
    async def provider(messages, info):
        yield "done"

    result = (
        await HarnessBuilder()
        .build(AgentDefinition(agent=AgentSpec(), output_type=str, model=FunctionModel(stream_function=provider)))
        .run("go")
    )
    assert result.output_or_raise() == "done"
    record = result.usage_records[0]
    assert isinstance(record, ModelUsageRecord) and record.call_id
    historical = record.model_dump()
    historical.pop("call_id")
    assert ModelUsageRecord.model_validate(historical).call_id is None


async def test_cancellation_during_check_prevents_provider_entry():
    entered = asyncio.Event()
    providers = []

    class PendingCheck:
        async def check(self, call):
            entered.set()
            await asyncio.Event().wait()

    async def provider(messages, info):
        providers.append("entered")
        yield "unexpected"

    executable = HarnessBuilder().build(
        AgentDefinition(agent=AgentSpec(), output_type=str, model=FunctionModel(stream_function=provider))
    )
    task = asyncio.create_task(executable.run("go", bindings=RunBindings.embedded(model_call_check=PendingCheck())))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10)
    finally:
        task.cancel()
    assert not providers


async def test_interrupted_committed_response_keeps_its_original_dispatch_identity():
    from a13n_harness import ModelRecoveryPolicy

    checks = Checks()
    count = 0

    async def provider(messages, info):
        nonlocal count
        count += 1
        if count == 1:
            yield "partial"
            raise ConnectionResetError("interrupted")
        yield "done"

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=provider),
            model_recovery=ModelRecoveryPolicy(
                enabled=True, max_attempts=2, backoff_initial_seconds=0, backoff_max_seconds=0
            ),
        )
    )
    result = await executable.run("go", bindings=RunBindings.embedded(model_call_check=checks))
    assert result.output_or_raise() == "done"
    assert [record.response_state for record in result.usage_records] == ["interrupted", "complete"]
    assert [record.call_id for record in result.usage_records] == [call.call_id for call in checks.calls]


async def test_interleaved_auxiliary_calls_keep_owner_and_dispatch_identity():
    from a13n_harness import AgentContext
    from a13n_harness.toolsets.file_media import AgentMediaUnderstandingProvider, MediaUnderstandingRequest
    from a13n_harness.usage import _auxiliary_usage_scope
    from pydantic_ai import RunContext
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.usage import RequestUsage

    checks = Checks()
    started = []
    both_started = asyncio.Event()

    def media(name):
        async def analyze(messages, info):
            started.append(name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=5)
            await asyncio.sleep(0 if name == "media-b" else 0.01)
            return ModelResponse(
                parts=[TextPart(name)], model_name=name, usage=RequestUsage(input_tokens=1, output_tokens=1)
            )

        return AgentMediaUnderstandingProvider(models={"image": FunctionModel(analyze, model_name=name)})

    providers = (media("media-a"), media("media-b"))

    async def inspect_media(ctx: RunContext[AgentContext]):
        before = ctx.usage.requests
        with _auxiliary_usage_scope(ctx, source="files.media_understanding", tool_id="filesystem.view"):
            await asyncio.gather(
                *(
                    provider.understand(
                        MediaUnderstandingRequest(
                            kind="image", media_type="image/png", source_name="fixture.png", source_bytes=b"fixture"
                        )
                    )
                    for provider in providers
                )
            )
        assert ctx.usage.requests == before
        return "inspected"

    async def primary(messages, info):
        if any(isinstance(part, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for part in m.parts):
            yield "done"
        else:
            yield {0: DeltaToolCall(name="inspect_media", json_args="{}", tool_call_id="media-tool")}

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=primary),
            capabilities=(Capability(id="tools", tools=[inspect_media]),),
        )
    )
    result = await executable.run("go", bindings=RunBindings.embedded(model_call_check=checks))
    assert result.output_or_raise() == "done"
    calls = {call.call_id: call for call in checks.calls}
    records = [record for record in result.usage_records if record.source == "files.media_understanding"]
    assert len(records) == 2 and len(calls) == 4
    for record in records:
        call = calls[record.call_id]
        assert call.model_name == record.model_name
        assert call.harness_run_id == record.run_id
        assert call.agent_instance_id == record.agent_instance_id
        assert call.tool_call_id == record.tool_call_id == "media-tool"
    assert result.usage.requests == 2  # Auxiliary native accumulators remain independent.


async def test_concurrent_inline_children_inherit_check_without_crossing_ledgers():
    from dataclasses import replace

    from a13n_harness import HarnessEvent, HarnessExtensionEvent

    from .test_delegation import _bindings_factory, _parent_definition, _returns_after_latest_user

    checks = Checks()
    child_count = 0
    both_children = asyncio.Event()

    async def child_model(messages, info):
        nonlocal child_count
        child_count += 1
        if child_count == 2:
            both_children.set()
        await asyncio.wait_for(both_children.wait(), timeout=5)
        yield "child done"

    async def parent_model(messages, info):
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate", json_args='{"subagent":"reviewer","prompt":"inspect"}', tool_call_id="delegate-1"
                )
            }
        else:
            yield "parent done"

    child = AgentDefinition(agent=AgentSpec(), output_type=str, model=FunctionModel(stream_function=child_model))
    definition = _parent_definition(child, FunctionModel(stream_function=parent_model))

    async def run_parent(identity):
        bindings = replace(_bindings_factory(parent_instance_id=identity), model_call_check=checks)
        async with HarnessBuilder().build(definition).stream("go", bindings=bindings) as stream:
            items = [item async for item in stream]
            root = stream.result
        records = {}
        for item in items:
            if (
                isinstance(item, HarnessEvent)
                and isinstance(item.event, HarnessExtensionEvent)
                and item.event.kind == "usage"
            ):
                for data in item.event.payload["records"]:
                    record = ModelUsageRecord.model_validate(data)
                    records[record.record_id] = record
        assert root is not None and root.output_or_raise() == "parent done"
        assert len(root.usage_records) == 2
        return records

    left, right = await asyncio.gather(run_parent("parent-left"), run_parent("parent-right"))
    assert not left.keys() & right.keys()
    calls = {call.call_id: call for call in checks.calls}
    assert len(calls) == 6
    for records, parent in ((left, "parent-left"), (right, "parent-right")):
        assert len(records) == 3
        for record in records.values():
            call = calls[record.call_id]
            assert call.harness_run_id == record.run_id
            assert call.agent_instance_id == record.agent_instance_id
            assert call.parent_agent_instance_id == record.parent_agent_instance_id
            assert call.delegation_id == record.delegation_id
            assert call.agent_instance_id == parent or call.parent_agent_instance_id == parent


@pytest.mark.parametrize("deny", [False, True])
async def test_builtin_reviewer_checks_before_dispatch_and_reuses_receipt_identity(deny):
    from a13n_harness import AgentContext
    from a13n_harness.capabilities.tool_review import AgentToolReviewer, ToolReviewConfig, ToolReviewRequest
    from pydantic_ai import RunContext

    checks = []
    reviewer_calls = []
    receipts = []

    class Policy:
        async def check(self, call):
            checks.append(call)
            if deny and call.source == "tool.review":
                raise PermissionError("review denied")

    async def reviewer_model(messages, info):
        reviewer_calls.append("dispatch")
        assert checks[-1].source == "tool.review"
        yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args='{"risk":"low"}')}

    reviewer = AgentToolReviewer(FunctionModel(stream_function=reviewer_model), ToolReviewConfig(model="test:review"))

    async def review_tool(ctx: RunContext[AgentContext]):
        result = await reviewer.review(
            ToolReviewRequest(
                tool_id="fixture.write", tool_call_id="effect-1", tool_name="write", parameters_schema={}, arguments={}
            ),
            context=ctx.deps,
        )
        receipts.extend(result.usage)
        return "reviewed"

    async def primary(messages, info):
        if any(isinstance(part, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for part in m.parts):
            yield "done"
        else:
            yield {0: DeltaToolCall(name="review_tool", json_args="{}", tool_call_id="review-1")}

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=primary),
            capabilities=(Capability(id="tools", tools=[review_tool]),),
        )
    )
    if deny:
        with pytest.raises(RunError, match="model-call check"):
            await executable.run("go", bindings=RunBindings.embedded(model_call_check=Policy()))
        assert reviewer_calls == [] and receipts == []
    else:
        result = await executable.run("go", bindings=RunBindings.embedded(model_call_check=Policy()))
        assert result.output_or_raise() == "done"
        review_checks = [call for call in checks if call.source == "tool.review"]
        assert len(review_checks) == len(receipts) == len(reviewer_calls) == 1
        assert receipts[0].usage_id == review_checks[0].call_id
        assert review_checks[0].tool_id == "fixture.write" and review_checks[0].tool_call_id == "effect-1"


@pytest.mark.parametrize("deny", [False, True])
async def test_compaction_dispatch_is_correlated_and_cannot_soften_host_veto(deny):
    from a13n_harness import HarnessState
    from a13n_harness.capabilities import CompactionCapability, CompactionPolicy
    from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.usage import RequestUsage

    checks = Checks(error=PermissionError("denied") if deny else None)
    providers = []

    async def provider(messages, info):
        providers.append("entered")
        yield "summary" if len(providers) == 1 else "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("old")]),
            ModelResponse(parts=[TextPart("answer")], usage=RequestUsage(input_tokens=2100)),
        )
    )
    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=provider),
            capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2000)),),
        )
    )
    if deny:
        with pytest.raises(RunError, match="model-call check"):
            await executable.run("new", previous_state=previous, bindings=RunBindings.embedded(model_call_check=checks))
        assert len(checks.calls) == 1
        assert providers == []
        return
    result = await executable.run(
        "new", previous_state=previous, bindings=RunBindings.embedded(model_call_check=checks)
    )
    assert result.output_or_raise() == "done"
    assert len(checks.calls) == len(providers) == 2
    assert len({call.model_run_id for call in checks.calls}) == 2
    assert len({call.call_id for call in checks.calls}) == 2
    assert [record.call_id for record in result.usage_records] == [call.call_id for call in checks.calls]
    assert all(record.run_id == checks.calls[0].harness_run_id for record in result.usage_records)


@pytest.mark.parametrize("mode", ["earlier", "copied", "synthetic", "short_circuit"])
async def test_outer_wrapper_commits_selected_response_instead_of_last_dispatch(mode):
    from pydantic_ai.messages import ModelResponse, TextPart

    checks = Checks()
    providers = []

    class SelectResponse(AbstractCapability):
        async def wrap_model_request(self, ctx, *, request_context, handler):
            if mode == "short_circuit":
                return ModelResponse(parts=[TextPart("synthetic")])
            earlier = await handler(request_context)
            await handler(request_context)
            if mode == "synthetic":
                return ModelResponse(parts=[TextPart("synthetic")])
            if mode == "copied":
                from dataclasses import replace

                return replace(earlier)
            return earlier

    class CountingModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            providers.append("entered")
            async with super().request_stream(*args, **kwargs) as response:
                yield response

    async def provider(messages, info):
        yield "first"

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=CountingModel(stream_function=provider),
            capabilities=(SelectResponse(),),
        )
    )
    result = await asyncio.wait_for(executable.run("go", bindings=RunBindings.embedded(model_call_check=checks)), 5)
    assert result.output_or_raise() == ("first" if mode in {"earlier", "copied"} else "synthetic")
    assert len(checks.calls) == (0 if mode == "short_circuit" else 2)
    # Native streaming consumes only the first stream; the second handler
    # invocation opens a lazy composite but never enters its provider segment.
    assert len(providers) == (0 if mode == "short_circuit" else 1)
    assert len(result.usage_records) == 1
    assert result.usage_records[0].call_id == (checks.calls[0].call_id if mode in {"earlier", "copied"} else None)


@pytest.mark.parametrize("retry_first", [False, True])
async def test_nonstreaming_auxiliary_wrapper_returns_first_of_two_real_provider_responses(retry_first):
    from a13n_harness import AgentContext
    from a13n_harness.usage import _auxiliary_model_usage_capability, _auxiliary_usage_scope
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.messages import ModelResponse, TextPart

    checks = Checks()
    providers = []

    class EarlierResponse(AbstractCapability):
        async def wrap_model_request(self, ctx, *, request_context, handler):
            try:
                earlier = await handler(request_context)
            except ModelHTTPError:
                return await handler(request_context)
            await handler(request_context)
            return earlier

    def auxiliary_provider(messages, info):
        providers.append(len(providers) + 1)
        if retry_first and len(providers) == 1:
            raise ModelHTTPError(503, "auxiliary")
        return ModelResponse(parts=[TextPart(str(providers[-1]))])

    async def analyze(ctx: RunContext[AgentContext]):
        with _auxiliary_usage_scope(ctx, source="files.media_understanding", tool_id="filesystem.view"):
            usage = _auxiliary_model_usage_capability()
            assert usage is not None
            agent = Agent(FunctionModel(auxiliary_provider), capabilities=[EarlierResponse(), usage])
            result = await agent.run("analyze")
            assert result.output == ("2" if retry_first else "1")
            return result.output

    async def primary(messages, info):
        if any(isinstance(part, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for part in m.parts):
            yield "done"
        else:
            yield {0: DeltaToolCall(name="analyze", json_args="{}", tool_call_id="analyze-1")}

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=primary),
            capabilities=(Capability(id="analysis", tools=[analyze]),),
        )
    )
    result = await executable.run("go", bindings=RunBindings.embedded(model_call_check=checks))
    assert result.output_or_raise() == "done"
    auxiliary_checks = [call for call in checks.calls if call.source == "files.media_understanding"]
    auxiliary_records = [record for record in result.usage_records if record.source == "files.media_understanding"]
    assert providers == [1, 2] and len(auxiliary_checks) == 2
    assert len(auxiliary_records) == 1
    assert auxiliary_records[0].call_id == auxiliary_checks[1 if retry_first else 0].call_id


async def test_current_usage_events_are_v2_and_result_keeps_same_call_identity():
    from a13n_harness import HarnessEvent, HarnessExtensionEvent

    async def provider(messages, info):
        yield "done"

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=provider))
    reports = []
    async with executable.stream("go") as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent) and isinstance(item.event, HarnessExtensionEvent):
                if item.event.kind == "usage":
                    assert item.event.schema_version == "1"
                    reports.extend(item.event.payload["records"])
                else:
                    assert item.event.schema_version == "1"
        result = stream.result
    assert result is not None
    assert len(result.usage_records) == len(reports) == 1
    assert reports[0]["call_id"] == result.usage_records[0].call_id
    assert reports[0]["call_id"].startswith("call_")
