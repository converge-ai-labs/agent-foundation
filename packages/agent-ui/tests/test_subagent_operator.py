from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_harness import (
    AgentDefinition,
    AgentIdentityRef,
    AgentSpec,
    DelegationContextPolicy,
    HarnessBuilder,
    SubagentDefinition,
)
from a13n_harness.capabilities import (
    AsyncDelegateRequest,
    AsyncResumeRequest,
    ResolvedDelegationContext,
    SubagentCancelRequest,
    SubagentDelegationPlan,
    SubagentInfoRequest,
    SubagentOperatorContext,
    SubagentSteerRequest,
    SubagentWaitRequest,
)
from a13n_ui.composition import AgentCompositionResolver, AgentReconstructor, CompositionAcceptanceService
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.environment_runtime import EnvironmentSnapshotReconstructor, normalize_workspace_binding
from a13n_ui.errors import RunCoordinationError
from a13n_ui.model_runtime import AgentUiModelResolver
from a13n_ui.session_service import SessionService
from a13n_ui.settings import StorageSettings
from a13n_ui.storage import StoredChildCheckpoint, open_local_store
from a13n_ui.subagent_operator import AgentUiSubagentOperator
from ag_ui.core.events import RunErrorEvent, RunFinishedEvent
from anyio import Event, sleep_forever
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio

_DEFINITION_DIGEST = "3" * 64
_DEFINITION_ID = f"agent-ui:{_DEFINITION_DIGEST}"


class _OrderingLiveHub:
    def __init__(self, store) -> None:
        self._store = store
        self.terminal_statuses: list[str] = []

    async def publish(self, *, execution_id: str | None = None, events=(), **kwargs) -> None:
        del kwargs
        if execution_id is None or not any(isinstance(event, (RunFinishedEvent, RunErrorEvent)) for event in events):
            return
        head = await self._store.child_executions.get(execution_id)
        assert head is not None
        self.terminal_statuses.append(head.status)


async def _child_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    del messages, info
    yield "child complete"


def _child_plan(
    context: SubagentOperatorContext,
    *,
    prompt: str,
    model: FunctionModel | None = None,
) -> SubagentDelegationPlan:
    child_definition = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id=_DEFINITION_ID,
        model=model or FunctionModel(stream_function=_child_model),
    )
    parent_definition = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=_child_model),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Review one bounded change.",
                agent=child_definition,
            ),
        ),
    )
    child = HarnessBuilder().build(parent_definition).subagents.require("reviewer")
    return SubagentDelegationPlan(
        child=child,
        child_identity=AgentIdentityRef(issuer="agent-ui", subject="session-test", agent_id=_DEFINITION_ID),
        context=ResolvedDelegationContext(
            input=prompt,
            policy=DelegationContextPolicy(),
        ),
        usage_limits=None,
        parent=context,
    )


async def _runtime(tmp_path: Path):
    source_path = tmp_path / "agent-ui.yaml"
    source_path.write_text(
        """
schema_version: "1"
defaults:
  agent: assistant
  environment: native
models:
  primary:
    model: openai:gpt-5
agents:
  assistant:
    model: primary
environments:
  native:
    kind: native
""".lstrip()
    )
    source = await load_agent_ui_configuration(source_path)
    context = open_local_store(StorageSettings(data_root=tmp_path / "state"))
    store = await context.__aenter__()
    await CompositionAcceptanceService(store, AgentCompositionResolver()).accept(
        source,
        expected_current_digest=None,
    )
    environment_reconstructor = EnvironmentSnapshotReconstructor(local_runtime_parent=store.layout.runtimes)
    session_service = SessionService(
        store=store,
        agent_reconstructor=AgentReconstructor(),
        environment_reconstructor=environment_reconstructor,
    )
    session = await session_service.create(agent_name="assistant", environment_name="native")
    operator = AgentUiSubagentOperator(
        store=store,
        environment_reconstructor=environment_reconstructor,
    )
    await operator.start()
    return context, store, session, operator


async def test_delegate_and_linked_resume_publish_exact_child_checkpoints(tmp_path: Path) -> None:
    context, store, session, operator = await _runtime(tmp_path)
    ordering_hub = _OrderingLiveHub(store)
    operator._live_hub = ordering_hub
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    binding = await normalize_workspace_binding((workspace,))
    parent = SubagentOperatorContext(
        parent_thread_id=session.root_thread_id,
        parent_run_id="run-parent",
        parent_agent_instance_id="agent-parent",
        host_refs={"session_id": session.session_id, "thread_id": session.root_thread_id},
    )
    try:
        async with operator.bind_parent_run(
            session_id=session.session_id,
            thread_id=session.root_thread_id,
            run_id=parent.parent_run_id,
            agent_instance_id=parent.parent_agent_instance_id,
            binding=binding,
            model_resolver=AgentUiModelResolver({}),
        ):
            first = await operator.delegate(
                _child_plan(parent, prompt="Inspect the implementation"),
                AsyncDelegateRequest(subagent_name="reviewer", prompt="Inspect the implementation"),
            )
            assert first.status == "running"
            assert first.segment_index == 0
            assert first.child_run_id is not None
            assert first.thread_id is not None

            waited = await operator.wait(
                parent,
                SubagentWaitRequest(execution_id=first.execution_id, timeout_seconds=5),
            )
            completed = waited.executions[0]
            assert completed.status == "succeeded"
            assert completed.resumable
            assert completed.segment_index == 0
            assert completed.activity is not None
            assert "child complete" in completed.activity.output_preview
            assert not hasattr(completed, "output")
            assert ordering_hub.terminal_statuses == ["succeeded"]

            first_head = await store.child_executions.get(first.execution_id)
            assert first_head is not None
            assert first_head.selected_checkpoint is not None
            checkpoint = await store.objects.read_model(first_head.selected_checkpoint, StoredChildCheckpoint)
            assert checkpoint.execution_id == first.execution_id
            assert checkpoint.child_thread_id == first.thread_id
            assert checkpoint.terminal

            second = await operator.resume(
                _child_plan(parent, prompt="Continue the review"),
                AsyncResumeRequest(execution_id=first.execution_id, prompt="Continue the review"),
            )
            assert second.resumed_from == first.execution_id
            assert second.thread_id == first.thread_id
            assert second.segment_index == 1
            assert second.child_run_id != first.child_run_id

            resumed = await operator.wait(
                parent,
                SubagentWaitRequest(execution_id=second.execution_id, timeout_seconds=5),
            )
            assert resumed.executions[0].status == "succeeded"
            assert ordering_hub.terminal_statuses == ["succeeded", "succeeded"]
            consumed = await store.child_executions.get(first.execution_id)
            assert consumed is not None
            assert not consumed.resumable
    finally:
        await operator.close()
        await context.__aexit__(None, None, None)


async def test_cancel_and_steer_report_acknowledgement_then_saved_terminal_truth(tmp_path: Path) -> None:
    context, store, session, operator = await _runtime(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    binding = await normalize_workspace_binding((workspace,))
    parent = SubagentOperatorContext(
        parent_thread_id=session.root_thread_id,
        parent_run_id="run-parent",
        parent_agent_instance_id="agent-parent",
        host_refs={"session_id": session.session_id},
    )
    started = Event()

    async def slow_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        started.set()
        await sleep_forever()
        yield "unreachable"

    try:
        async with operator.bind_parent_run(
            session_id=session.session_id,
            thread_id=session.root_thread_id,
            run_id=parent.parent_run_id,
            agent_instance_id=parent.parent_agent_instance_id,
            binding=binding,
            model_resolver=AgentUiModelResolver({}),
        ):
            execution = await operator.delegate(
                _child_plan(
                    parent,
                    prompt="Inspect",
                    model=FunctionModel(stream_function=slow_model),
                ),
                AsyncDelegateRequest(subagent_name="reviewer", prompt="Inspect"),
            )
            await started.wait()
            steering = await operator.steer(
                parent,
                SubagentSteerRequest(execution_id=execution.execution_id, message="Focus on storage"),
            )
            assert steering.accepted
            assert steering.enqueue_id is not None

            cancellation = await operator.cancel(
                parent,
                SubagentCancelRequest(execution_id=execution.execution_id),
            )
            assert cancellation.accepted
            assert cancellation.status == "running"
            waited = await operator.wait(
                parent,
                SubagentWaitRequest(execution_id=execution.execution_id, timeout_seconds=5),
            )
            assert waited.executions[0].status == "cancelled"
            assert (await store.child_executions.get(execution.execution_id)).status == "cancelled"  # type: ignore[union-attr]
    finally:
        await operator.close()
        await context.__aexit__(None, None, None)


async def test_shutdown_leaves_no_process_owned_execution_running(tmp_path: Path) -> None:
    context, store, session, operator = await _runtime(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    binding = await normalize_workspace_binding((workspace,))
    parent = SubagentOperatorContext(
        parent_thread_id=session.root_thread_id,
        parent_run_id="run-parent",
        parent_agent_instance_id="agent-parent",
        host_refs={"session_id": session.session_id},
    )
    started = Event()

    async def slow_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        started.set()
        await sleep_forever()
        yield "unreachable"

    try:
        async with operator.bind_parent_run(
            session_id=session.session_id,
            thread_id=session.root_thread_id,
            run_id=parent.parent_run_id,
            agent_instance_id=parent.parent_agent_instance_id,
            binding=binding,
            model_resolver=AgentUiModelResolver({}),
        ):
            execution = await operator.delegate(
                _child_plan(
                    parent,
                    prompt="Inspect",
                    model=FunctionModel(stream_function=slow_model),
                ),
                AsyncDelegateRequest(subagent_name="reviewer", prompt="Inspect"),
            )
            await started.wait()

        await operator.close(timeout_seconds=1)
        head = await store.child_executions.get(execution.execution_id)
        assert head is not None
        assert head.status in {"cancelled", "lost"}
    finally:
        await operator.close()
        await context.__aexit__(None, None, None)


async def test_linked_resume_starts_a_segment_owned_display_and_rejects_double_resume(tmp_path: Path) -> None:
    context, store, session, operator = await _runtime(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    binding = await normalize_workspace_binding((workspace,))
    parent = SubagentOperatorContext(
        parent_thread_id=session.root_thread_id,
        parent_run_id="run-parent",
        parent_agent_instance_id="agent-parent",
        host_refs={"session_id": session.session_id},
    )

    def output_model(value: str) -> FunctionModel:
        async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            del messages, info
            yield value

        return FunctionModel(stream_function=stream)

    try:
        async with operator.bind_parent_run(
            session_id=session.session_id,
            thread_id=session.root_thread_id,
            run_id=parent.parent_run_id,
            agent_instance_id=parent.parent_agent_instance_id,
            binding=binding,
            model_resolver=AgentUiModelResolver({}),
        ):
            first = await operator.delegate(
                _child_plan(parent, prompt="First", model=output_model("first segment")),
                AsyncDelegateRequest(subagent_name="reviewer", prompt="First"),
            )
            await operator.wait(parent, SubagentWaitRequest(execution_id=first.execution_id, timeout_seconds=5))
            second = await operator.resume(
                _child_plan(parent, prompt="Second", model=output_model("second segment")),
                AsyncResumeRequest(execution_id=first.execution_id, prompt="Second"),
            )
            with pytest.raises(RunCoordinationError) as conflict:
                await operator.resume(
                    _child_plan(parent, prompt="Again", model=output_model("third segment")),
                    AsyncResumeRequest(execution_id=first.execution_id, prompt="Again"),
                )
            assert conflict.value.code == "subagent_resume_incompatible"
            await operator.wait(parent, SubagentWaitRequest(execution_id=second.execution_id, timeout_seconds=5))

            second_head = await store.child_executions.get(second.execution_id)
            assert second_head is not None and second_head.selected_checkpoint is not None
            checkpoint = await store.objects.read_model(second_head.selected_checkpoint, StoredChildCheckpoint)
            text = [activity.text for activity in checkpoint.display.activities if activity.kind == "text"]
            assert "second segment" in text
            assert "first segment" not in text
    finally:
        await operator.close()
        await context.__aexit__(None, None, None)


async def test_execution_queries_are_scoped_to_the_exact_active_parent(tmp_path: Path) -> None:
    context, _store, session, operator = await _runtime(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    binding = await normalize_workspace_binding((workspace,))
    parent = SubagentOperatorContext(
        parent_thread_id=session.root_thread_id,
        parent_run_id="run-parent",
        parent_agent_instance_id="agent-parent",
        host_refs={"session_id": session.session_id},
    )
    try:
        async with operator.bind_parent_run(
            session_id=session.session_id,
            thread_id=session.root_thread_id,
            run_id=parent.parent_run_id,
            agent_instance_id=parent.parent_agent_instance_id,
            binding=binding,
            model_resolver=AgentUiModelResolver({}),
        ):
            execution = await operator.delegate(
                _child_plan(parent, prompt="Inspect"),
                AsyncDelegateRequest(subagent_name="reviewer", prompt="Inspect"),
            )
            wrong_run = parent.__class__(
                parent_thread_id=parent.parent_thread_id,
                parent_run_id="run-other",
                parent_agent_instance_id=parent.parent_agent_instance_id,
                host_refs=parent.host_refs,
            )
            with pytest.raises(RunCoordinationError) as unavailable:
                await operator.info(wrong_run, SubagentInfoRequest(execution_id=execution.execution_id))
            assert unavailable.value.code == "subagent_parent_scope_invalid"
    finally:
        await operator.close()
        await context.__aexit__(None, None, None)
