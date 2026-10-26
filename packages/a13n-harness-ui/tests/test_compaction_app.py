from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import RootOperationStatus
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("enabled", [None, False, True])
async def test_background_compaction_configuration_is_captured(tmp_path: Path, enabled: bool | None):
    from a13n_harness.capabilities import CompactionCapability
    from a13n_harness_ui.compaction import BackgroundCompaction
    from a13n_harness_ui.composition import AgentCompositionResolver, AgentReconstructor, ResolvedRunComposition
    from a13n_harness_ui.configuration import load_harness_ui_configuration
    from a13n_harness_ui.storage import open_local_store

    from .test_composition import _catalog, _selection, _UnusedOperator, _write_source

    root = _write_source(tmp_path)
    if enabled is not None:
        root.write_text(root.read_text() + f"background_compaction: {str(enabled).lower()}\n")
    source = await load_harness_ui_configuration(root)
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    assert composition.background_compaction is (enabled is not False)
    payload = composition.model_dump(mode="json")
    payload.pop("background_compaction", None)
    assert ResolvedRunComposition.model_validate(payload).background_compaction is False
    async with open_local_store(_settings(tmp_path / "state").storage) as store:
        owner = BackgroundCompaction(store.compactions, tmp_path / "state")
        reconstructed = AgentReconstructor(_catalog()).reconstruct(
            composition, subagent_operator=_UnusedOperator(), background_compaction=owner
        )
        assert (reconstructed.compaction is not None) is (enabled is not False)
        for child in reconstructed.executable.subagents.values():
            leaves = []
            child.executable._agent.root_capability.apply(leaves.append)
            assert all(not item.background for item in leaves if isinstance(item, CompactionCapability))
        # Async child reconstruction receives no root Host operator, even with the captured flag enabled.
        child_run = AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())
        assert child_run.compaction is None
        await owner.close()


async def test_root_run_passes_effective_limits_to_detached_compaction(tmp_path: Path, monkeypatch):
    from a13n_harness.compaction import CompactionInput
    from a13n_harness.execution import ExecutableAgent
    from a13n_harness_ui.composition import AgentReconstructor
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.usage import UsageLimits

    root = _write_configuration(tmp_path)
    reconstructed = []
    reconstruct = AgentReconstructor.reconstruct
    background_calls = 0

    def capture(self, *args, **kwargs):
        result = reconstruct(self, *args, **kwargs)
        reconstructed.append(result)
        return result

    async def foreground(messages, info):
        yield "foreground answer"

    async def background(messages, info):
        nonlocal background_calls
        background_calls += 1
        return ModelResponse(parts=[TextPart("summary")])

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(function=background, stream_function=foreground)

    monkeypatch.setattr(AgentReconstructor, "reconstruct", capture)
    monkeypatch.setattr(ExecutableAgent, "definition_usage_limits", lambda self: UsageLimits(request_limit=1))
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="original input")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed, operation
        assert operation.run_id is not None
        compaction = reconstructed[-1].compaction
        assert compaction is not None
        input = CompactionInput.create(
            operation_id="compaction-limited",
            thread_id=thread.thread_id,
            run_id=operation.run_id,
            model_id="model-primary",
            model_name="test",
            provider_name="function",
            messages=[ModelRequest(parts=[UserPromptPart("old")]), ModelResponse(parts=[TextPart("answer")])],
            parameters=ModelRequestParameters(),
        )
        await compaction.submit(input)
        result = await compaction.wait(thread.thread_id, input.basis.operation_id)
        assert result.status == "failed"
        assert background_calls == 0


@pytest.mark.parametrize("restart,clear", [(False, False), (True, False), (False, True)])
async def test_app_retains_background_result_then_delivers_with_current_input(
    tmp_path: Path, monkeypatch, restart: bool, clear: bool
):
    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(
        agent.read_text() + "capabilities:\n  - capability: compaction\n    configuration:\n      trigger_tokens: 10\n"
    )
    model_path = tmp_path / "models/primary.yaml"
    model_path.write_text(
        model_path.read_text() + "settings: {max_tokens: 100}\nmodel_characteristics: {context_window_tokens: 10000}\n"
    )
    started, release = asyncio.Event(), asyncio.Event()
    requests: list[str] = []
    summaries = 0

    async def foreground(messages, info):
        requests.append(str(messages))
        yield "obsolete answer " * 30 if len(requests) == 1 else "foreground answer"

    async def background(messages, info):
        nonlocal summaries
        summaries += 1
        started.set()
        await release.wait()
        return ModelResponse(
            parts=[TextPart("retained summary")], usage=RequestUsage(input_tokens=100, output_tokens=10)
        )

    class CountingModel(FunctionModel):
        async def count_tokens(self, messages, model_settings, model_request_parameters):
            return RequestUsage(input_tokens=100)

    async def resolve(self, model_id, *, thread_id):
        return CountingModel(function=background, stream_function=foreground)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    settings = _settings(tmp_path / "state")

    async def submit(app, thread_id, prompt):
        receipt = await app.submit_thread(thread_id=thread_id, prompt=prompt)
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed, operation

    async def consume(app, thread_id):
        await submit(app, thread_id, "current input")
        assert "retained summary" in requests[-1]
        assert "obsolete answer" not in requests[-1]
        assert "second input" in requests[-1] and "current input" in requests[-1]
        assert await app._store.compactions.get(thread_id) is None
        assert summaries == 1

    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        await submit(app, thread.thread_id, "original input")
        await submit(app, thread.thread_id, "second input")
        await asyncio.wait_for(started.wait(), 5)
        selected = await app.get_thread(thread.thread_id)
        assert app._compaction is not None
        running = await app._store.compactions.get(thread.thread_id)
        assert running is not None and running.status == "running"
        if clear:
            from a13n_harness_ui.errors import StoreConflictError

            stored = await app._store.threads.get(thread.thread_id)
            assert stored is not None and stored.continuation is not None
            read_model = (await app._store.threads.read_models((thread.thread_id,)))[thread.thread_id]
            # A losing checkpoint selection must retain the operation, including clear.
            with pytest.raises(StoreConflictError):
                await app._store.threads.select_continuation(
                    thread_id=thread.thread_id,
                    expected=None,
                    replacement=stored.continuation,
                    read_model=read_model,
                    clear_compaction=True,
                )
            assert await app._store.compactions.get(thread.thread_id) == running
            await app.clear_thread_context(
                thread_id=thread.thread_id, expected_continuation_id=selected.continuation_id
            )
            assert await app._store.compactions.get(thread.thread_id) is None
            release.set()
            await asyncio.gather(*app._compaction._tasks.values())
            assert await app._store.compactions.get(thread.thread_id) is None
            return
        release.set()
        ready = await app._compaction.wait(thread.thread_id, running.input.basis.operation_id)
        assert ready.status == "ready"
        # Completion has no idle checkpoint authority and does not create a synthetic Run.
        assert (await app.get_thread(thread.thread_id)).continuation_id == selected.continuation_id
        copied = await app.fork_thread(thread.thread_id)
        assert await app._store.compactions.get(copied.thread_id) is None
        root.write_text(root.read_text() + "background_compaction: false\n")
        await app.reload_configuration()
        if not restart:
            await consume(app, thread.thread_id)
    if restart:
        async with open_harness_ui_app(settings, configuration_path=root) as app:
            await consume(app, thread.thread_id)


@pytest.mark.parametrize("restart", [False, True])
async def test_app_recovers_failed_background_in_next_run_and_commits_retirement(tmp_path: Path, monkeypatch, restart):
    from a13n_harness.capabilities.context import _COMPACTION_PROMPT
    from pydantic_ai.messages import UserPromptPart

    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(
        agent.read_text() + "capabilities:\n  - capability: compaction\n    configuration:\n      trigger_tokens: 10\n"
    )
    model_path = tmp_path / "models/primary.yaml"
    model_path.write_text(
        model_path.read_text() + "settings: {max_tokens: 100}\nmodel_characteristics: {context_window_tokens: 10000}\n"
    )
    started = asyncio.Event()
    background_calls = 0
    fallback_calls = 0
    foreground_requests = []

    async def foreground(messages, info):
        nonlocal fallback_calls
        if any(
            isinstance(part, UserPromptPart) and part.content == _COMPACTION_PROMPT
            for message in messages
            for part in message.parts
        ):
            fallback_calls += 1
            assert "current recovery input" in str(messages)
            yield "recovered summary"
        else:
            foreground_requests.append(str(messages))
            yield "obsolete answer " * 30 if len(foreground_requests) == 1 else "done"

    async def background(messages, info):
        nonlocal background_calls, fallback_calls
        if any(
            isinstance(part, UserPromptPart) and part.content == _COMPACTION_PROMPT
            for message in messages
            for part in message.parts
        ):
            # Both paths use the same directive; a detached input ends in a
            # frozen prefix, whereas fallback includes this Run's new input.
            if "current recovery input" in str(messages):
                fallback_calls += 1
                return ModelResponse(parts=[TextPart("recovered summary")])
        background_calls += 1
        started.set()
        if restart:
            await asyncio.Event().wait()
        raise RuntimeError("background unavailable")

    class Model(FunctionModel):
        async def count_tokens(self, messages, model_settings, model_request_parameters):
            return RequestUsage(input_tokens=100)

    async def resolve(self, model_id, *, thread_id):
        return Model(function=background, stream_function=foreground)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    settings = _settings(tmp_path / "state")

    async def submit(app, thread_id, prompt):
        receipt = await app.submit_thread(thread_id=thread_id, prompt=prompt)
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed, operation
        return operation

    async def recover(app, thread_id):
        failed = await app._compaction.inspect(thread_id)
        assert failed is not None and failed.status == "failed"
        if restart:
            assert failed.error_code == "compaction_interrupted"
        await submit(app, thread_id, "current recovery input")
        assert "recovered summary" in foreground_requests[-1]
        assert "obsolete answer" not in foreground_requests[-1]
        assert "current recovery input" in foreground_requests[-1]
        assert await app._store.compactions.get(thread_id) is None
        assert fallback_calls == 1 and background_calls == 1

    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        await submit(app, thread.thread_id, "original input")
        await submit(app, thread.thread_id, "second input")
        await asyncio.wait_for(started.wait(), 5)
        if not restart:
            operation = await app._store.compactions.get(thread.thread_id)
            await app._compaction.wait(thread.thread_id, operation.input.basis.operation_id)
            await recover(app, thread.thread_id)
    if restart:
        async with open_harness_ui_app(settings, configuration_path=root) as app:
            await recover(app, thread.thread_id)
