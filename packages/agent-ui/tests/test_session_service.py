from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from a13n_harness import HarnessRunResult, HarnessState, SafeFailure
from a13n_ui.composition import (
    AgentCompositionResolver,
    AgentReconstructor,
    CompositionAcceptanceService,
)
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.environment_runtime import EnvironmentSnapshotReconstructor
from a13n_ui.errors import RunCoordinationError
from a13n_ui.session_service import SessionService
from a13n_ui.settings import StorageSettings
from a13n_ui.storage import StoredSessionContinuation, open_local_store
from anyio import Event, create_task_group
from pydantic_ai.usage import RunUsage

pytestmark = pytest.mark.anyio


@dataclass(frozen=True, slots=True)
class _FakeReconstructed:
    executable: Any
    model_resolver: Any


class _CompletedExecutable:
    def __init__(self, *, entered: Event | None = None, release: Event | None = None) -> None:
        self.entered = entered
        self.release = release
        self.calls: list[dict[str, Any]] = []

    async def run(self, prompt: str, **kwargs: Any) -> HarnessRunResult[str]:
        self.calls.append({"prompt": prompt, **kwargs})
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        state = kwargs["previous_state"]
        assert isinstance(state, HarnessState)
        return HarnessRunResult(
            thread_id=state.thread_id,
            run_id="run-root-test",
            status="completed",
            output="done",
            state=state,
            usage=RunUsage(),
        )


class _FailedExecutable:
    async def run(self, prompt: str, **kwargs: Any) -> HarnessRunResult[str]:
        del prompt
        state = kwargs["previous_state"]
        assert isinstance(state, HarnessState)
        return HarnessRunResult(
            thread_id=state.thread_id,
            run_id="run-root-failed",
            status="failed",
            output=None,
            state=None,
            usage=RunUsage(),
            failure=SafeFailure(code="model_failed", message="Model failed."),
        )


class _FakeAgentReconstructor:
    def __init__(self, executable: Any) -> None:
        self.executable = executable

    def reconstruct(self, snapshot: Any, **kwargs: Any) -> _FakeReconstructed:
        del snapshot, kwargs

        async def resolve_model(*args: Any, **values: Any) -> Any:
            del args, values
            raise AssertionError("fake executable does not resolve a Model")

        return _FakeReconstructed(executable=self.executable, model_resolver=resolve_model)


async def _service(tmp_path: Path, executable: Any):
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
    service = SessionService(
        store=store,
        agent_reconstructor=cast(AgentReconstructor, _FakeAgentReconstructor(executable)),
        environment_reconstructor=EnvironmentSnapshotReconstructor(
            local_runtime_parent=store.layout.runtimes,
        ),
    )
    return context, store, service


async def test_session_creation_pins_snapshots_and_baseline_continuation(tmp_path: Path) -> None:
    context, store, service = await _service(tmp_path, _CompletedExecutable())
    try:
        session = await service.create(
            agent_name="assistant",
            environment_name="native",
            title="Native work",
        )

        assert session.title == "Native work"
        assert session.agent_snapshot.snapshot_kind == "agent"
        assert session.environment_snapshot.snapshot_kind == "environment"
        stored = await store.objects.read_model(session.continuation, StoredSessionContinuation)
        assert stored.harness_state.thread_id == session.root_thread_id
        assert stored.deferred_requests is None
    finally:
        await context.__aexit__(None, None, None)


async def test_root_run_selects_complete_continuation_after_environment_cleanup(tmp_path: Path) -> None:
    executable = _CompletedExecutable()
    context, _store, service = await _service(tmp_path, executable)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        session = await service.create(agent_name="assistant", environment_name="native")
        previous = session.continuation

        outcome = await service.run(
            session_id=session.session_id,
            prompt="Do the work",
            folders=(workspace,),
        )

        assert outcome.result.output == "done"
        assert outcome.continuation.status == "selected"
        assert outcome.continuation.reference is not None
        assert outcome.continuation.reference != previous
        assert outcome.environment.cleanup_errors == ()
        assert outcome.environment.state_publications[0].status == "unchanged"
        retained = await service.get(session.session_id)
        assert retained.continuation == outcome.continuation.reference
        call = executable.calls[0]
        assert tuple(call["environments"]) == ("workspace",)
        assert call["default_environment"] == "workspace"
    finally:
        await context.__aexit__(None, None, None)


async def test_failed_root_run_keeps_prior_continuation(tmp_path: Path) -> None:
    context, _store, service = await _service(tmp_path, _FailedExecutable())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        session = await service.create(agent_name="assistant", environment_name="native")

        outcome = await service.run(
            session_id=session.session_id,
            prompt="Fail safely",
            folders=(workspace,),
        )

        assert outcome.result.status == "failed"
        assert outcome.continuation.status == "not_available"
        retained = await service.get(session.session_id)
        assert retained.continuation == session.continuation
    finally:
        await context.__aexit__(None, None, None)


async def test_second_root_run_is_rejected_while_session_is_active(tmp_path: Path) -> None:
    entered = Event()
    release = Event()
    executable = _CompletedExecutable(entered=entered, release=release)
    context, _store, service = await _service(tmp_path, executable)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        session = await service.create(agent_name="assistant", environment_name="native")
        first_outcomes: list[object] = []

        async def first() -> None:
            first_outcomes.append(
                await service.run(
                    session_id=session.session_id,
                    prompt="First",
                    folders=(workspace,),
                )
            )

        async with create_task_group() as tasks:
            tasks.start_soon(first)
            await entered.wait()
            with pytest.raises(RunCoordinationError) as active:
                await service.run(
                    session_id=session.session_id,
                    prompt="Second",
                    folders=(workspace,),
                )
            assert active.value.code == "session_run_active"
            release.set()

        assert len(first_outcomes) == 1
    finally:
        await context.__aexit__(None, None, None)
