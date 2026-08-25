from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from converge_agent_harness import HarnessState
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart

import converge_host_example.store as store_module
from converge_host_example.application import run_host_demo
from converge_host_example.store import HostStoreError, JsonFileHostStore


def test_host_demo_selects_state_and_rejects_the_stale_execution_attempt(tmp_path: Path) -> None:
    result = asyncio.run(run_host_demo(tmp_path))

    assert result.execution.state == "completed"
    assert result.execution.current_execution_attempt is None
    assert result.execution.selected_checkpoint_ref == "checkpoint-2"
    assert result.execution.next_execution_attempt_generation == 3
    assert result.first_run_id != result.replacement_run_id
    assert result.replacement_model_message_count > result.first_model_message_count
    assert result.partial_text_recovered is True
    assert result.completed_thinking_recovered is True
    assert result.incomplete_thinking_excluded is True
    assert result.stale_execution_attempt_rejected is True

    selected = asyncio.run(JsonFileHostStore(tmp_path).load_selected_checkpoint("execution-1"))
    assert selected is not None
    assert selected.execution_attempt_id == "execution-attempt-2"
    assert selected.thread_id == selected.harness_state.thread_id
    assert len(selected.harness_state.message_history) > 0
    interrupted = next(
        message
        for message in selected.harness_state.message_history
        if isinstance(message, ModelResponse) and message.state == "interrupted"
    )
    assert any(
        isinstance(part, TextPart) and part.content == "visible answer before interruption"
        for part in interrupted.parts
    )
    assert not any(
        isinstance(part, ThinkingPart) and part.content == "unfinished private reasoning" for part in interrupted.parts
    )


def test_harness_state_does_not_persist_fresh_host_authority(tmp_path: Path) -> None:
    asyncio.run(run_host_demo(tmp_path))

    checkpoint_path = tmp_path / "executions" / "execution-1" / "checkpoints" / "checkpoint-2.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    harness_payload = json.dumps(checkpoint["harness_state"], sort_keys=True)

    assert "definition-1" not in harness_payload
    assert "execution-1" not in harness_payload
    assert "execution-attempt-2" not in harness_payload
    assert "agent-1" not in harness_payload
    assert "fence_digest" not in harness_payload
    assert checkpoint["thread_id"] == checkpoint["harness_state"]["thread_id"]


def test_execution_attempt_freezes_its_selected_starting_checkpoint(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = JsonFileHostStore(tmp_path)
        await store.create_execution(
            execution_id="execution-1",
            definition_revision_ref="definition-1",
            agent_instance_id="agent-1",
        )
        first = await store.acquire_execution_attempt("execution-1")
        assert first.starting_checkpoint is None
        checkpoint_1 = await store.commit_checkpoint(
            first,
            harness_run_id="run-1",
            harness_state=HarnessState(),
        )
        await store.abandon_execution_attempt_for_recovery(first)

        second = await store.acquire_execution_attempt("execution-1")
        assert second.starting_checkpoint is not None
        assert second.starting_checkpoint.checkpoint_ref == checkpoint_1.checkpoint_ref
        record = await store.read_execution("execution-1")
        assert record.current_execution_attempt is not None
        assert record.current_execution_attempt.starting_checkpoint_ref == checkpoint_1.checkpoint_ref

        checkpoint_2 = await store.commit_checkpoint(
            second,
            harness_run_id="run-2",
            harness_state=HarnessState(),
        )
        selected = await store.load_selected_checkpoint("execution-1")
        assert selected is not None
        assert selected.checkpoint_ref == checkpoint_2.checkpoint_ref
        assert second.starting_checkpoint.checkpoint_ref == checkpoint_1.checkpoint_ref

    asyncio.run(exercise())


def test_cancelled_write_finishes_before_releasing_the_transition_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        store = JsonFileHostStore(tmp_path)
        await store.create_execution(
            execution_id="execution-1",
            definition_revision_ref="definition-1",
            agent_instance_id="agent-1",
        )
        entered = threading.Event()
        release = threading.Event()
        original_write = store_module._atomic_write_text

        def blocked_write(path: Path, payload: str) -> None:
            entered.set()
            if not release.wait(timeout=2):
                raise RuntimeError("Test did not release the Host state write")
            original_write(path, payload)

        monkeypatch.setattr(store_module, "_atomic_write_text", blocked_write)
        cancelled_acquisition = asyncio.create_task(store.acquire_execution_attempt("execution-1"))
        assert await asyncio.to_thread(entered.wait, 2)
        cancelled_acquisition.cancel()
        await asyncio.sleep(0)
        assert not cancelled_acquisition.done()

        competing_acquisition = asyncio.create_task(store.acquire_execution_attempt("execution-1"))
        await asyncio.sleep(0)
        assert not competing_acquisition.done()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await cancelled_acquisition
        with pytest.raises(HostStoreError, match="already has a current ExecutionAttempt"):
            await competing_acquisition
        record = await store.read_execution("execution-1")
        assert record.current_execution_attempt is not None
        assert record.current_execution_attempt.execution_attempt_id == "execution-attempt-1"

    asyncio.run(exercise())


def test_store_rejects_invalid_or_stale_fences(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = JsonFileHostStore(tmp_path)
        await store.create_execution(
            execution_id="execution-1",
            definition_revision_ref="definition-1",
            agent_instance_id="agent-1",
        )
        lease = await store.acquire_execution_attempt("execution-1")
        invalid = replace(lease, fence="not-the-issued-fence")
        with pytest.raises(HostStoreError, match="stale or invalid"):
            await store.commit_checkpoint(
                invalid,
                harness_run_id="run-1",
                harness_state=HarnessState(),
            )

        await store.commit_checkpoint(
            lease,
            harness_run_id="run-1",
            harness_state=HarnessState(),
        )
        await store.abandon_execution_attempt_for_recovery(lease)
        await store.acquire_execution_attempt("execution-1")
        with pytest.raises(HostStoreError, match="stale or invalid"):
            await store.commit_checkpoint(
                lease,
                harness_run_id="run-1",
                harness_state=HarnessState(),
            )

    asyncio.run(exercise())


def test_store_rejects_path_like_identifiers(tmp_path: Path) -> None:
    store = JsonFileHostStore(tmp_path)

    with pytest.raises(ValueError, match="kind-prefixed"):
        asyncio.run(
            store.create_execution(
                execution_id="../outside",
                definition_revision_ref="definition-1",
                agent_instance_id="agent-1",
            )
        )
