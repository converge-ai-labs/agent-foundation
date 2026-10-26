from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from weakref import WeakValueDictionary

import pytest
from a13n_harness import AgentIdentityRef, AgentInstanceContext, HarnessState
from a13n_harness.compaction import CompactionInput
from a13n_harness.model_calls import ModelCall
from a13n_harness.state import AgentContextState
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, UsageSnapshot
from a13n_harness_ui.compaction import BackgroundCompaction
from a13n_harness_ui.compaction_model import CompactionBudget, CompactionRun
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.compaction import CompactionRepository
from a13n_harness_ui.storage.database import open_database, transaction
from a13n_harness_ui.storage.models import ThreadRecord
from a13n_harness_ui.storage.usage import ThreadUsageRepository
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage, UsageLimits

pytestmark = pytest.mark.anyio


def _call(name: str) -> ModelCall:
    return ModelCall(
        call_id=name,
        harness_run_id="run-original",
        model_run_id=None,
        agent_instance_id="agent-test",
        parent_agent_instance_id=None,
        delegation_id=None,
        model_id="model-test",
        model_name="test",
        provider_name="function",
        source="agent",
        tool_id=None,
        tool_call_id=None,
    )


def _record(name: str, *, tokens: int = 1, cost: Decimal = Decimal("0.1")) -> ModelUsageRecord:
    return ModelUsageRecord(
        call_id=name,
        record_id=f"usage-{name}",
        run_id="run-original",
        response_ordinal=0,
        agent_instance_id="agent-test",
        response_state="complete",
        model_name="test",
        provider_name="function",
        response_timestamp=datetime.now(UTC),
        request_usage=BoundedRequestUsage(input_tokens=tokens, cost=cost),
        cost_source="custom",
        pricing_status="applied",
    )


async def test_shared_budget_counts_pending_and_deduplicates_revisions() -> None:
    budget = CompactionBudget(UsageLimits(request_limit=2), WeakValueDictionary())
    budget.agent_instance_ids.add("agent-test")
    await budget.check(_call("foreground"))
    await budget.check(_call("background"))
    with pytest.raises(UsageLimitExceeded):
        await budget.check(_call("third"))
    assert budget.finish("background", _record("background")) is None
    assert budget.used == 2 and budget.pending == 1
    assert budget.finish("background", _record("background", tokens=3)) is None
    assert budget.used == 2
    budget.cancel("foreground")
    await budget.check(_call("third"))
    assert budget.used == 2


@pytest.mark.parametrize("limits", [UsageLimits(total_tokens_limit=2), UsageLimits(cost_limit=Decimal("0.15"))])
async def test_shared_token_and_cost_limits_settle_before_next_dispatch(limits: UsageLimits) -> None:
    budget = CompactionBudget(limits, WeakValueDictionary())
    budget.agent_instance_ids.add("agent-test")
    await budget.check(_call("foreground"))
    await budget.check(_call("background"))
    assert budget.finish("foreground", _record("foreground")) is None
    assert isinstance(budget.finish("background", _record("background", tokens=2)), UsageLimitExceeded)
    with pytest.raises(UsageLimitExceeded):
        await budget.check(_call("third"))


async def test_inline_child_has_independent_admission_budget() -> None:
    budget = CompactionBudget(UsageLimits(request_limit=1), WeakValueDictionary())
    budget.agent_instance_ids.add("agent-test")
    await budget.check(_call("foreground"))
    assert await budget.check(replace(_call("inline"), agent_instance_id="agent-child")) is None
    assert budget.pending == 1
    with pytest.raises(UsageLimitExceeded):
        await budget.check(_call("second"))


async def test_background_output_limit_refuses_next_foreground_request() -> None:
    budget = CompactionBudget(UsageLimits(output_tokens_limit=2), WeakValueDictionary())
    budget.agent_instance_ids.add("agent-test")
    await budget.check(_call("background"))
    record = _record("background").model_copy(update={"request_usage": BoundedRequestUsage(output_tokens=3)})
    assert isinstance(budget.finish("background", record), UsageLimitExceeded)
    with pytest.raises(UsageLimitExceeded):
        await budget.check(_call("foreground"))
    assert budget.pending == 0


async def test_detached_model_resources_usage_and_resumed_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    resources: list[str] = []
    resolvers: list[HarnessUiModelResolver] = []

    async def model(messages, info):
        started.set()
        await release.wait()
        return ModelResponse(parts=[TextPart("summary")], usage=RequestUsage(input_tokens=7, output_tokens=3))

    class ManagedModel(FunctionModel):
        async def __aenter__(self):
            resources.append("enter")
            return self

        async def __aexit__(self, *args):
            resources.append("exit")

    async def resolve(self, model_id, *, thread_id):
        assert model_id == "model-test" and thread_id == "thread_test"
        resolvers.append(self)
        return ManagedModel(function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        async with transaction(database.sessions) as session:
            session.add(
                ThreadRecord(
                    thread_id="thread_test",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    initial_state_schema_version="1",
                    initial_state_digest="a" * 64,
                )
            )
        repository = CompactionRepository(database.sessions)
        usage = ThreadUsageRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        resolver = HarnessUiModelResolver({})
        instance = AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="agent"), agent_instance_id="agent-test"
        )
        state = HarnessState.new(thread_id="thread_test")
        snapshot = UsageSnapshot(
            usage_id="scope-original",
            thread_id=state.thread_id,
            run_id="run-original",
            agent_instance_id=instance.agent_instance_id,
        )
        context = AgentContextState()
        await context.write("a13n.usage", snapshot, version="1")
        state = HarnessState.new(thread_id=state.thread_id, agent_context_state=await context.snapshot())
        run = CompactionRun(owner, resolver, None)
        budget = await run.prepare(
            instance=instance,
            reporter=usage.reporter(state.thread_id),
            state=state,
            limits=UsageLimits(request_limit=1),
            resume_usage=True,
        )
        input = CompactionInput.create(
            operation_id="compact-test",
            thread_id=state.thread_id,
            run_id="run-resumed",
            model_id="model-test",
            model_name="test",
            provider_name="function",
            messages=[ModelRequest(parts=[UserPromptPart("old")]), ModelResponse(parts=[TextPart("answer")])],
            parameters=ModelRequestParameters(),
        )
        await run.submit(input)
        await asyncio.wait_for(started.wait(), 5)
        resumed = CompactionRun(owner, resolver, None)
        shared = await resumed.prepare(
            instance=instance,
            reporter=usage.reporter(state.thread_id),
            state=state,
            limits=UsageLimits(request_limit=1),
            resume_usage=True,
        )
        assert shared is budget and shared.pending == 1
        with pytest.raises(UsageLimitExceeded):
            await shared.check(_call("foreground"))
        release.set()
        ready = await run.wait(state.thread_id, input.basis.operation_id)
        assert ready.status == "ready"
        assert resources == ["enter", "exit"]
        assert len(resolvers) == 1 and resolvers[0] is not resolver
        records = await usage.model_records(
            thread_id=state.thread_id,
            run_id="run-original",
            agent_instance_id=instance.agent_instance_id,
        )
        assert len(records) == 1 and records[0].source == "compaction"
        assert records[0].request_usage.input_tokens == 7
        await owner.close()
        # A new App restores the detached scope even though execution state never contained it.
        reopened = BackgroundCompaction(repository, tmp_path)
        restored = await CompactionRun(reopened, resolver, None).prepare(
            instance=instance,
            reporter=usage.reporter(state.thread_id),
            state=state,
            limits=UsageLimits(request_limit=1),
            resume_usage=True,
        )
        assert restored.used == 1 and restored.pending == 0
        with pytest.raises(UsageLimitExceeded):
            await restored.check(_call("foreground"))
        await reopened.close()


async def test_foreground_fallback_honors_shared_budget_after_background_consumption(tmp_path: Path) -> None:
    from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
    from a13n_harness.capabilities.context import CompactionCapability
    from a13n_harness.compaction import compaction_disposition

    async def unavailable(input):
        raise RuntimeError("background unavailable")

    calls = []

    async def model(messages, info):
        calls.append(messages)
        yield "must not dispatch"

    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        operator = owner.bind(unavailable)
        messages = [ModelRequest(parts=[UserPromptPart("old")]), ModelResponse(parts=[TextPart("answer")])]
        state = HarnessState.new(message_history=messages)
        input = CompactionInput.create(
            operation_id="compact-budget",
            thread_id=state.thread_id,
            run_id="run-original",
            model_name="test",
            provider_name="function",
            messages=messages,
            parameters=ModelRequestParameters(),
        )
        await operator.submit(input)
        failed = await operator.wait(state.thread_id, input.basis.operation_id)
        budget = CompactionBudget(UsageLimits(request_limit=1), WeakValueDictionary())
        bindings = RunBindings.embedded(model_call_check=budget)
        budget.agent_instance_ids.add(bindings.instance.agent_instance_id)
        background = replace(_call("background"), agent_instance_id=bindings.instance.agent_instance_id)
        await budget.check(background)
        budget.finish("background", _record("background"))
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(CompactionCapability(background=True, operator=operator),),
        )
        from a13n_harness.model_calls import ModelCallCheckError

        async with executable.stream(
            "continue",
            previous_state=state,
            bindings=bindings,
            usage_limits=UsageLimits(request_limit=2),
        ) as stream:
            with pytest.raises(ModelCallCheckError):
                async for _ in stream:
                    pass
            retained = await stream.export_state()
            assert not stream.context.usage_attribution.records
        assert not calls
        assert any(isinstance(m, ModelResponse) and m.parts == messages[1].parts for m in retained.message_history)
        assert await compaction_disposition(retained, basis=input.basis) is None
        await owner.acknowledge(retained)
        assert await repository.get(state.thread_id) == failed
        assert budget.used == 1 and budget.pending == 0
        await owner.close()
