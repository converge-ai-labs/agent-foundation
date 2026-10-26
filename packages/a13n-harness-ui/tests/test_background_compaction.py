from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from a13n_harness import HarnessState
from a13n_harness.compaction import CompactionInput, CompactionOperation
from a13n_harness_ui.compaction import BackgroundCompaction
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.compaction import CompactionRepository, settle_compaction
from a13n_harness_ui.storage.database import open_database, short_session, transaction
from a13n_harness_ui.storage.models import CompactionRecord
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

pytestmark = pytest.mark.anyio


def _input(operation_id: str = "compact-test") -> CompactionInput:
    return CompactionInput.create(
        operation_id=operation_id,
        thread_id="thread_test",
        run_id="run-test",
        model_name="test",
        provider_name="function",
        messages=[ModelRequest(parts=[UserPromptPart("old history")]), ModelResponse(parts=[TextPart("answer")])],
        parameters=ModelRequestParameters(),
    )


async def _summary(input: CompactionInput) -> ModelResponse:
    return ModelResponse(parts=[TextPart("summary")])


async def test_ready_result_survives_owner_close_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    settings = StorageSettings(data_root=tmp_path)
    input = _input()
    async with open_database(path, settings) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        operator = owner.bind(_summary)
        await operator.submit(input)
        ready = await operator.wait(input.basis.thread_id, input.basis.operation_id)
        assert ready.status == "ready"
        assert await repository.get(input.basis.thread_id) == ready
        await owner.close()
    async with open_database(path, settings) as database:
        owner = BackgroundCompaction(CompactionRepository(database.sessions), tmp_path)
        assert await owner.inspect(input.basis.thread_id) == ready
        assert await owner.bind(_summary).submit(_input("compact-other")) == ready
        await owner.close()


async def test_cancelled_waiter_does_not_cancel_generation(tmp_path: Path) -> None:
    started, finish = asyncio.Event(), asyncio.Event()

    async def request(input: CompactionInput) -> ModelResponse:
        started.set()
        await finish.wait()
        return await _summary(input)

    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        owner = BackgroundCompaction(CompactionRepository(database.sessions), tmp_path)
        operator = owner.bind(request)
        input = _input()
        await operator.submit(input)
        await started.wait()
        waiter = asyncio.create_task(operator.wait(input.basis.thread_id, input.basis.operation_id))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        finish.set()
        assert (await operator.wait(input.basis.thread_id, input.basis.operation_id)).status == "ready"
        await owner.close()


async def test_other_owner_does_not_interrupt_live_generation(tmp_path: Path) -> None:
    started, finish = asyncio.Event(), asyncio.Event()

    async def request(input: CompactionInput) -> ModelResponse:
        started.set()
        await finish.wait()
        return await _summary(input)

    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        first, second = BackgroundCompaction(repository, tmp_path), BackgroundCompaction(repository, tmp_path)
        input = _input()
        await first.bind(request).submit(input)
        await started.wait()
        observed = await second.inspect(input.basis.thread_id)
        assert observed is not None and observed.status == "running"
        await first.close()
        observed = await second.inspect(input.basis.thread_id)
        assert observed is not None and observed.status == "failed"
        await second.close()


async def test_owner_lock_contention_does_not_block_other_threads(tmp_path: Path, monkeypatch) -> None:
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        owner = BackgroundCompaction(CompactionRepository(database.sessions), tmp_path)
        input = _input()
        held = owner._owner_lock(input.basis.operation_id)
        contender = owner._owner_lock(input.basis.operation_id)
        attempted = asyncio.Event()
        acquire = contender.acquire

        def acquire_contended(*args, **kwargs):
            attempted.set()
            return acquire(*args, **kwargs)

        monkeypatch.setattr(contender, "acquire", acquire_contended)
        monkeypatch.setattr(owner, "_owner_lock", lambda operation_id: contender)
        held.acquire(timeout=0)
        submitting = asyncio.create_task(owner.bind(_summary).submit(input))
        try:
            await asyncio.wait_for(attempted.wait(), timeout=2)
            assert await asyncio.wait_for(owner.inspect("thread_other"), timeout=2) is None
        finally:
            held.release()
            await submitting
            await owner.close()


async def test_dead_owner_is_failed_without_replaying_generation(tmp_path: Path) -> None:
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        input = _input()
        await repository.admit(CompactionOperation(input=input, status="running"))
        owner = BackgroundCompaction(repository, tmp_path)
        failed = await owner.bind(_summary).submit(_input("compact-other"))
        assert failed.status == "failed"
        assert failed.error_code == "compaction_interrupted"
        await owner.close()


async def test_clear_fences_late_completion(tmp_path: Path) -> None:
    started, finish = asyncio.Event(), asyncio.Event()

    async def request(input: CompactionInput) -> ModelResponse:
        started.set()
        await finish.wait()
        return await _summary(input)

    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        input = _input()
        await owner.bind(request).submit(input)
        await started.wait()
        async with transaction(database.sessions) as session:
            await settle_compaction(session, input.basis.thread_id, clear=True)
        finish.set()
        await asyncio.gather(*owner._tasks.values())
        assert await repository.get(input.basis.thread_id) is None
        assert await owner.inspect(input.basis.thread_id) is None
        await owner.close()


async def test_unacknowledged_result_is_not_consumed_by_restore(tmp_path: Path) -> None:
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        input = _input()
        operator = owner.bind(_summary)
        await operator.submit(input)
        ready = await operator.wait(input.basis.thread_id, input.basis.operation_id)
        state = HarnessState.new(thread_id=input.basis.thread_id)
        assert await owner.restore(state) == ready.result
        assert await owner.restore(state) == ready.result
        assert await repository.get(state.thread_id) == ready
        await owner.close()


async def test_close_before_worker_starts_releases_owner(tmp_path: Path) -> None:
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        input = _input()
        # Direct admission returns without yielding after registering the worker.
        await owner._admit(input, _summary)
        await owner.close()
        assert not owner._tasks
        assert not owner._owners
        operation = await repository.get(input.basis.thread_id)
        assert operation is not None and operation.status == "failed"


async def test_publication_retry_retains_summary_without_exposure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def request(input: CompactionInput) -> ModelResponse:
        nonlocal calls
        calls += 1
        return await _summary(input)

    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        finish = repository.finish

        async def unavailable(operation: CompactionOperation) -> bool:
            raise OSError("storage unavailable")

        monkeypatch.setattr(repository, "finish", unavailable)
        input = _input()
        await owner.bind(request).submit(input)
        await asyncio.gather(*owner._tasks.values())
        persisted = await repository.get(input.basis.thread_id)
        assert persisted is not None and persisted.status == "running"
        assert owner._pending[input.basis.operation_id].status == "ready"
        assert calls == 1
        with pytest.raises(RuntimeError, match="could not be persisted"):
            await asyncio.wait_for(owner.wait(input.basis.thread_id, input.basis.operation_id), 3)
        assert calls == 1
        monkeypatch.setattr(repository, "finish", finish)
        operation = await owner.inspect(input.basis.thread_id)
        assert operation is not None and operation.status == "ready"
        assert await repository.get(input.basis.thread_id) == operation
        assert calls == 1
        await owner.close()


async def test_close_joins_in_progress_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    publishing, release = asyncio.Event(), asyncio.Event()
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        finish = repository.finish

        async def blocked(operation: CompactionOperation) -> bool:
            publishing.set()
            await release.wait()
            return await finish(operation)

        monkeypatch.setattr(repository, "finish", blocked)
        input = _input()
        await owner.bind(_summary).submit(input)
        await publishing.wait()
        closing = asyncio.create_task(owner.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
        operation = await repository.get(input.basis.thread_id)
        assert operation is not None and operation.status == "ready"


async def test_concurrent_owners_admit_one_generation(tmp_path: Path) -> None:
    calls = 0
    release = asyncio.Event()

    async def request(input: CompactionInput) -> ModelResponse:
        nonlocal calls
        calls += 1
        await release.wait()
        return await _summary(input)

    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owners = [BackgroundCompaction(repository, tmp_path) for _ in range(2)]
        input = _input()
        results = await asyncio.gather(*(owner.bind(request).submit(input) for owner in owners))
        assert results[0] == results[1]
        release.set()
        ready = await owners[0].wait(input.basis.thread_id, input.basis.operation_id)
        assert ready.status == "ready"
        assert calls == 1
        await asyncio.gather(*(owner.close() for owner in owners))


@pytest.mark.parametrize("outcome", ["ready", "stale", "failed"])
async def test_native_marker_prepares_settlement_without_consuming_result(tmp_path: Path, outcome: str) -> None:
    from a13n_harness import AgentSpec, HarnessBuilder
    from a13n_harness.capabilities.context import CompactionCapability
    from a13n_harness.compaction import compaction_disposition
    from pydantic_ai.models.function import FunctionModel

    async def model(messages, info):
        yield "done"

    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        input = _input()

        async def generate(input):
            if outcome == "failed":
                raise RuntimeError("background unavailable")
            return await _summary(input)

        operator = owner.bind(generate)
        await operator.submit(input)
        ready = await operator.wait(input.basis.thread_id, input.basis.operation_id)
        state = HarnessState.new(thread_id=input.basis.thread_id, message_history=input.request_messages[:-1])
        if outcome == "stale":
            state = HarnessState.new(thread_id=state.thread_id)
        result = (
            await HarnessBuilder()
            .build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(CompactionCapability(background=True, operator=operator),),
            )
            .run(
                "current input",
                previous_state=state,
                compaction_result=await owner.restore(state),
            )
        )
        assert result.state is not None
        assert await compaction_disposition(result.state, basis=input.basis) == (
            "applied" if outcome == "ready" else "skipped"
        )
        # Generated/merged state alone is not selected checkpoint authority.
        assert await repository.get(state.thread_id) == ready
        settlement = await repository.settlement(result.state)
        assert settlement is not None
        assert settlement.operation_id == input.basis.operation_id
        assert settlement.disposition == ("applied" if outcome == "ready" else "skipped")
        assert await repository.get(state.thread_id) == ready
        async with transaction(database.sessions) as session:
            await settle_compaction(session, state.thread_id, settlement=settlement)
        async with short_session(database.sessions) as session:
            row = await session.get(CompactionRecord, input.basis.operation_id)
            assert row is not None and row.settled_at is not None
            assert row.disposition == settlement.disposition
        assert await owner.restore(result.state) is None
        assert await repository.get(state.thread_id) is None
        await owner.close()


async def test_cancelled_close_still_joins_publication(tmp_path: Path, monkeypatch) -> None:
    publishing, release = asyncio.Event(), asyncio.Event()
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        finish = repository.finish

        async def blocked(operation):
            publishing.set()
            await release.wait()
            return await finish(operation)

        monkeypatch.setattr(repository, "finish", blocked)
        input = _input()
        await owner.submit(input, _summary)
        await publishing.wait()
        closing = asyncio.create_task(owner.close())
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert not owner._tasks and not owner._owners
        operation = await repository.get(input.basis.thread_id)
        assert operation is not None and operation.status == "ready"


async def test_durable_result_applies_at_same_run_tool_boundary(tmp_path: Path) -> None:
    from a13n_harness import AgentSpec, HarnessBuilder, HarnessModelCharacteristics
    from a13n_harness.capabilities import CompactionCapability, CompactionPolicy
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from pydantic_ai.usage import RequestUsage

    release = asyncio.Event()
    requests: list[str] = []
    input = _input()

    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)

        async def summarize(input: CompactionInput) -> ModelResponse:
            await release.wait()
            return await _summary(input)

        async def finish_summary() -> str:
            release.set()
            operation = await owner.inspect(input.basis.thread_id)
            assert operation is not None
            ready = await owner.wait(input.basis.thread_id, operation.input.basis.operation_id)
            assert await repository.get(input.basis.thread_id) == ready
            return "fresh tool result"

        async def stream(messages, info):
            requests.append(str(messages))
            if len(requests) == 1:
                yield {0: DeltaToolCall(name="finish_summary", json_args="{}", tool_call_id="finish")}
            else:
                yield "done"

        class CountingModel(FunctionModel):
            async def count_tokens(self, messages, model_settings, model_request_parameters):
                return RequestUsage(input_tokens=100)

        executable = HarnessBuilder().build(
            AgentSpec(
                model_settings={"max_tokens": 100},
                model_characteristics=HarnessModelCharacteristics(context_window_tokens=10000),
            ),
            model=CountingModel(stream_function=stream),
            output_type=str,
            capabilities=(
                CompactionCapability(
                    CompactionPolicy(trigger_tokens=10), background=True, operator=owner.bind(summarize)
                ),
                Capability(id="finish", tools=[finish_summary]),
            ),
        )
        previous = HarnessState.new(
            thread_id=input.basis.thread_id,
            message_history=[
                ModelRequest(parts=[UserPromptPart("old history")]),
                ModelResponse(parts=[TextPart("old answer")], usage=RequestUsage(input_tokens=100)),
            ],
        )
        result = await asyncio.wait_for(executable.run("current input", previous_state=previous), 10)
        assert result.output_or_raise() == "done"
        assert len(requests) == 2
        assert "old history" in requests[0] and "old history" not in requests[1]
        assert "summary" in requests[1] and "fresh tool result" in requests[1]
        assert requests[1].count("current input") == 1
        # Runtime application alone cannot retire a durable result.
        assert await repository.get(input.basis.thread_id) is not None
        await owner.close()
