from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import a13n_harness_ui.subagent_operator as subagent_module
import pytest
from a13n_harness import SafeFailure
from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import ChildExecutionHead, CompactChildDisplay, ObjectKind, ObjectRef, open_local_store
from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator
from anyio import Event

pytestmark = pytest.mark.anyio


async def test_subagent_operator_owns_bounded_lifecycle_and_parent_scoped_queries(tmp_path: Path) -> None:
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        unavailable = cast(Any, object())
        operator = HarnessUiSubagentOperator(
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

        for timeout in (-1.0, float("inf"), float("nan")):
            with pytest.raises(RunCoordinationError) as invalid_wait:
                await operator.wait_child_executions(
                    parent_thread_id="thread-parent",
                    timeout_seconds=timeout,
                )
            assert invalid_wait.value.code == "child_wait_invalid"

        await operator.stop_admission()
        await operator.close(timeout_seconds=1)


async def test_saved_children_with_retired_composition_fields_remain_inspectable(tmp_path: Path) -> None:
    from a13n_harness_ui.composition import AgentCompositionResolver, ResolvedRunComposition
    from a13n_harness_ui.configuration import load_harness_ui_configuration

    from .test_composition import _catalog, _selection, _write_source
    from .test_thread_repository import _configuration, _initial

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    payload = composition.model_dump(mode="json")
    # Before Provider consolidation, every captured environment carried this field.
    payload["environment_profile"]["provider_schema_version"] = "1"
    payload["retired_capture_field"] = {"opaque": True}
    async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
        envelope = await store.objects.publish(
            object_kind=ObjectKind.run_composition, object_schema_version="1", payload=payload
        )
        await store.threads.create(thread_id="thread-parent", configuration=_configuration(), initial_state=_initial())
        await store.threads.create(
            thread_id=composition.thread_id,
            parent_thread_id="thread-parent",
            configuration=_configuration(),
            initial_state=_initial(),
        )
        await store.child_executions.create(
            execution_id="execution-legacy",
            parent_thread_id="thread-parent",
            child_thread_id=composition.thread_id,
            child_run_id="run-legacy",
            run_composition=envelope.ref,
        )
        unavailable = cast(Any, object())
        operator = HarnessUiSubagentOperator(
            store=store,
            configurations=unavailable,
            compositions=unavailable,
            agent_reconstructor=unavailable,
            environment_service=unavailable,
        )
        page = await operator.query_child_executions(parent_thread_id="thread-parent")
        assert len(page.executions) == 1
        child = page.executions[0]
        assert child.subagent_name == composition.root.roster_name
        assert child.child_thread_id == composition.thread_id
        detail = await operator.query_child_executions(
            parent_thread_id="thread-parent", execution_id=child.execution_id
        )
        assert detail.executions[0].activity is not None
        restored = await store.objects.read_model(envelope.ref, ResolvedRunComposition)
        assert restored.environment_profile.provider_key == composition.environment_profile.provider_key
        assert restored.model_dump(mode="json") == composition.model_dump(mode="json")
        assert await store.objects.read(envelope.ref) == envelope


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(None, 30.0), (0.0, 0.0), (60.0, 60.0), (90.0, 90.0), (180.0, 180.0), (181.0, 180.0), (1000.0, 180.0)],
)
def test_child_wait_caps_only_requests_above_180_seconds(requested: float | None, expected: float) -> None:
    assert subagent_module._wait_timeout(requested) == expected


async def test_model_subagent_boundary_preserves_programming_integrity_and_cancellation_errors() -> None:
    from anyio import get_cancelled_exc_class

    errors = [
        RuntimeError("programming defect"),
        RunCoordinationError("corrupt checkpoint", code="subagent_checkpoint_incompatible"),
        get_cancelled_exc_class()(),
    ]

    @subagent_module._subagent_tool
    async def operation(error: BaseException) -> None:
        raise error

    for error in errors:
        with pytest.raises(type(error)) as raised:
            await operation(error)
        assert raised.value is error


async def test_terminal_child_projection_does_not_expose_stale_local_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable = cast(Any, object())
    operator = HarnessUiSubagentOperator(
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

    async def unreadable_checkpoint(_head: ChildExecutionHead):
        pytest.fail("list and locally active inspection must not deserialize checkpoints")

    async def child_thread(_head: ChildExecutionHead) -> Any:
        return unavailable

    async def root_thread_id(_thread: Any) -> str:
        return "thread-root"

    async def execution_identity(_head: ChildExecutionHead) -> tuple[str, str]:
        return "worker", "a13n-harness-ui:agent:worker"

    monkeypatch.setattr(operator, "_execution_identity", execution_identity)
    monkeypatch.setattr(operator, "_read_checkpoint", unreadable_checkpoint)
    monkeypatch.setattr(operator, "_require_child_thread", child_thread)
    monkeypatch.setattr(operator, "_root_thread_id", root_thread_id)

    projection = await operator._execution_projection(head)

    assert projection.persisted_status == "succeeded"
    assert projection.local_status == "unavailable"
    assert projection.available_actions == ()
    assert projection.activity is None
    active_head = head.model_copy(
        update={
            "status": "running",
            "selected_checkpoint": ObjectRef(
                object_kind=ObjectKind.child_checkpoint, logical_digest="2" * 64, object_schema_version="1"
            ),
        }
    )
    inspected = await operator._execution_projection(active_head, include_activity=True)
    assert inspected.activity is not None
    assert inspected.available_actions == ("wait", "steer", "cancel")


def test_child_failure_projection_is_bounded() -> None:
    failure = SafeFailure(
        code="x" * 300,
        message="y" * (40 * 1024),
        details={"value": "z" * (70 * 1024)},
    )
    projected = subagent_module._surface_failure(failure)
    display = subagent_module._with_failure(CompactChildDisplay(final_answer="Earlier output"), failure)

    assert len(projected.code) == 256
    assert len(projected.message) <= 32 * 1024
    assert projected.details is None
    assert display.activities[-1].kind == "failure"
    assert display.activities[-1].text == projected.message
    assert projected.message.endswith("[message truncated]")
    assert display.final_answer == "Earlier output"
    assert CompactChildDisplay.model_validate_json(display.model_dump_json()) == display
    assert failure.message == "y" * (40 * 1024)


@pytest.mark.parametrize("finalization_failure", [None, "cleanup", "publication"])
async def test_child_finalization_failure_preserves_checkpoint_without_success(
    monkeypatch: pytest.MonkeyPatch, finalization_failure: str | None
) -> None:
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_harness import AgentIdentityRef, HarnessBuilder, HarnessState
    from a13n_harness_ui.environment_runtime import EnvironmentFinalization
    from opentelemetry.trace import INVALID_SPAN
    from pydantic_ai.agent.spec import AgentSpec
    from pydantic_ai.models.test import TestModel

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=TestModel(custom_output_text="child answer")
    )
    state = HarnessState.new()
    stream = executable.stream("work", previous_state=state)
    checkpoint = ObjectRef(object_kind=ObjectKind.child_checkpoint, object_schema_version="1", logical_digest="2" * 64)
    finish = AsyncMock()
    store = SimpleNamespace(child_executions=SimpleNamespace(finish=finish), usage=SimpleNamespace(observe=AsyncMock()))
    unavailable = cast(Any, object())
    operator = HarnessUiSubagentOperator(
        store=cast(Any, store),
        configurations=unavailable,
        compositions=unavailable,
        agent_reconstructor=unavailable,
        environment_service=unavailable,
    )

    @asynccontextmanager
    async def bind_parent_run(**kwargs):
        yield

    publish = AsyncMock(return_value=checkpoint)
    monkeypatch.setattr(operator, "bind_parent_run", bind_parent_run)
    monkeypatch.setattr(operator, "_publish_checkpoint_object", publish)
    monkeypatch.setattr(operator, "_publish_summary", AsyncMock())
    monkeypatch.setattr(operator, "_publish_summary_by_execution", AsyncMock())
    now = datetime.now(UTC)
    head = ChildExecutionHead(
        execution_id="execution-1",
        parent_thread_id="thread-parent",
        child_thread_id=state.thread_id,
        child_run_id=stream.run_id,
        segment_index=0,
        run_composition=ObjectRef(
            object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="1" * 64
        ),
        status="running",
        selected_checkpoint=None,
        resumed_from=None,
        failure=None,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    finalized = EnvironmentFinalization(
        cleanup_errors=(RuntimeError("cleanup failed"),) if finalization_failure == "cleanup" else (),
        state_publications=(cast(Any, SimpleNamespace(status="failed")),)
        if finalization_failure == "publication"
        else (),
    )
    prepared = subagent_module._PreparedSegment(
        head=head,
        scope=cast(Any, SimpleNamespace(agent_instance_id="parent-agent")),
        composition=unavailable,
        reconstructed=unavailable,
        input="work",
        usage_limits=None,
        identity=AgentIdentityRef(issuer="test", subject="child"),
        state=state,
        environment=cast(Any, SimpleNamespace(finalize=AsyncMock(return_value=finalized))),
        stream=stream,
        agent_instance_id="child-agent",
        display=CompactChildDisplay(),
    )
    active = subagent_module._ActiveSegment(
        execution_id=head.execution_id,
        parent_thread_id=head.parent_thread_id,
        stream=stream,
        done=Event(),
        display=CompactChildDisplay(),
    )
    await operator._execute_segment(prepared, active, INVALID_SPAN)
    finish.assert_awaited_once()
    outcome = finish.await_args.kwargs
    assert outcome["checkpoint"] == checkpoint
    assert "child answer" in str(publish.await_args.kwargs["state"].message_history)
    assert active.done.is_set()
    if finalization_failure is None:
        assert outcome["status"] == "succeeded"
        assert active.cleanup_succeeded
    else:
        assert outcome["status"] == "failed"
        assert outcome["failure"].code == "subagent_finalization_failed"
        assert not active.cleanup_succeeded
