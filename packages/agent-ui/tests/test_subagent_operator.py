from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import a13n_ui.subagent_operator as subagent_module
import pytest
from a13n_harness import SafeFailure
from a13n_harness.capabilities import SubagentExecutionView
from a13n_ui.errors import RunCoordinationError
from a13n_ui.settings import StorageSettings
from a13n_ui.storage import ChildExecutionHead, CompactChildDisplay, ObjectKind, ObjectRef, open_local_store
from a13n_ui.subagent_operator import AgentUiSubagentOperator
from anyio import Event

pytestmark = pytest.mark.anyio


async def test_subagent_operator_owns_bounded_lifecycle_and_parent_scoped_queries(tmp_path: Path) -> None:
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        unavailable = cast(Any, object())
        operator = AgentUiSubagentOperator(
            store=store,
            configurations=unavailable,
            compositions=unavailable,
            agent_reconstructor=unavailable,
            environment_service=unavailable,
        )
        await operator.start()
        with pytest.raises(RunCoordinationError) as duplicate:
            await operator.start()
        assert duplicate.value.code == "subagent_operator_started"

        with pytest.raises(RunCoordinationError) as missing:
            await operator.query_child_executions(
                parent_thread_id="thread-parent",
                execution_id="execution-missing",
            )
        assert missing.value.code == "subagent_execution_unavailable"

        for timeout in (-1.0, 61.0):
            with pytest.raises(RunCoordinationError) as invalid_wait:
                await operator.wait_child_executions(
                    parent_thread_id="thread-parent",
                    timeout_seconds=timeout,
                )
            assert invalid_wait.value.code == "child_wait_invalid"

        await operator.stop_admission()
        await operator.close(timeout_seconds=1)


async def test_terminal_child_projection_does_not_expose_stale_local_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable = cast(Any, object())
    operator = AgentUiSubagentOperator(
        store=unavailable,
        configurations=unavailable,
        compositions=unavailable,
        agent_reconstructor=unavailable,
        environment_service=unavailable,
    )
    reference = ObjectRef(
        object_kind=ObjectKind.run_composition,
        object_schema_version="1",
        logical_digest="1" * 64,
    )
    now = datetime.now(UTC)
    head = ChildExecutionHead(
        execution_id="execution-1",
        parent_thread_id="thread-parent",
        child_thread_id="thread-child",
        child_run_id="run-child",
        segment_index=0,
        run_composition=reference,
        status="succeeded",
        selected_checkpoint=None,
        resumed_from=None,
        failure=None,
        created_at=now,
        updated_at=now,
        completed_at=now,
    )
    operator._active[head.execution_id] = subagent_module._ActiveSegment(
        execution_id=head.execution_id,
        parent_thread_id=head.parent_thread_id,
        stream=unavailable,
        done=Event(),
        display=CompactChildDisplay(),
    )

    async def execution_view(_head: ChildExecutionHead) -> SubagentExecutionView:
        return SubagentExecutionView(
            execution_id=head.execution_id,
            subagent_name="worker",
            child_definition_id="agent-ui:agent:worker",
            status="succeeded",
            resumable=False,
            thread_id=head.child_thread_id,
            child_run_id=head.child_run_id,
            segment_index=0,
        )

    async def child_thread(_head: ChildExecutionHead) -> Any:
        return unavailable

    async def root_thread_id(_thread: Any) -> str:
        return "thread-root"

    monkeypatch.setattr(operator, "_execution_view", execution_view)
    monkeypatch.setattr(operator, "_require_child_thread", child_thread)
    monkeypatch.setattr(operator, "_root_thread_id", root_thread_id)

    projection = await operator._execution_projection(head)

    assert projection.persisted_status == "succeeded"
    assert projection.local_status == "unavailable"
    assert projection.available_actions == ()


def test_child_failure_projection_is_bounded() -> None:
    projected = subagent_module._surface_failure(
        SafeFailure(
            code="x" * 300,
            message="y" * (40 * 1024),
            details={"value": "z" * (70 * 1024)},
        )
    )

    assert len(projected.code) == 256
    assert len(projected.message) <= 32 * 1024
    assert projected.details is None
