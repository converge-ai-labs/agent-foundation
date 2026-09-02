from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import a13n_ui.app as app_module
import a13n_ui.storage.runtime as storage_runtime
import pytest
from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder, SubagentDefinition
from a13n_harness.capabilities import SubagentCapability
from a13n_ui.app import AgentUiApp, AppState, open_agent_ui_app
from a13n_ui.composition import ReconstructedAgent
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.errors import AppStateError, ObjectIntegrityError
from a13n_ui.model_runtime import AgentUiModelResolver
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.storage import ObjectKind
from a13n_ui.subagent_operator import AgentUiSubagentOperator
from anyio import TASK_STATUS_IGNORED, create_task_group, fail_after, sleep, sleep_forever
from anyio import Event as AsyncEvent
from anyio.abc import TaskStatus
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


def _settings(root: Path) -> AgentUiSettings:
    return AgentUiSettings(storage=StorageSettings(data_root=root))


async def test_application_starts_publishes_reopens_and_closes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_agent_ui_app(settings) as app:
        retained_app = app
        first_instance = (await app.status()).instance_id
        assert app.state is AppState.ready
        assert (await app.status()).object_count == 0
        reference = await app._store.publish_object(
            object_kind=ObjectKind.agent_snapshot,
            object_schema_version="1",
            payload={"agent": "root"},
        )
        assert (await app._store.read_object(reference)).payload == {"agent": "root"}
        assert (await app.status()).object_count == 1

    assert retained_app.state is AppState.closed
    with pytest.raises(AppStateError) as closed:
        await retained_app.status()
    assert closed.value.code == "app_not_ready"

    async with open_agent_ui_app(settings) as reopened:
        assert (await reopened._store.read_object(reference)).payload == {"agent": "root"}
        assert (await reopened.status()).instance_id != first_instance


async def test_application_accepts_configuration_and_runs_roster_with_app_owned_operator(tmp_path: Path) -> None:
    config_path = tmp_path / "agent-ui.yaml"
    config_path.write_text(
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
    subagents:
      - agent: reviewer
  reviewer:
    model: primary
environments:
  native:
    kind: native
""".lstrip()
    )
    configuration = await load_agent_ui_configuration(config_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async with open_agent_ui_app(_settings(tmp_path / "state"), configuration=configuration) as app:
        app._sessions._agents = _FunctionRosterReconstructor()
        session = await app.create_session(agent_name="assistant", environment_name="native")
        outcome = await app.run_session(
            session_id=session.session_id,
            prompt="start background review",
            folders=(workspace,),
        )
        assert outcome.result.output_or_raise() == "accepted"
        with fail_after(5):
            while True:
                heads, total = await app._store.child_executions.list_scope(
                    session_id=session.session_id,
                    parent_thread_id=session.root_thread_id,
                )
                if heads and heads[0].status != "running":
                    break
                await sleep(0.01)

        assert total == 1
        assert heads[0].status == "succeeded"
        assert heads[0].subagent_name == "reviewer"
        assert await app._store.configurations.current_digest() == configuration.source_digest


async def test_production_app_exposes_fixed_release_tool_families(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "agent-ui.yaml"
    config_path.write_text(
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
    configuration = await load_agent_ui_configuration(config_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    observed: set[str] = set()

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        observed.update(tool.name for tool in info.function_tools)
        yield "tool surface ready"

    async def resolve_model(self, context, model_id):
        del self, context, model_id
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(AgentUiModelResolver, "__call__", resolve_model)

    async with open_agent_ui_app(_settings(tmp_path / "state"), configuration=configuration) as app:
        session = await app.create_session(agent_name="assistant", environment_name="native")
        outcome = await app.run_session(
            session_id=session.session_id,
            prompt="inspect available tools",
            folders=(workspace,),
        )

    assert outcome.result.output_or_raise() == "tool surface ready"
    expected = {
        "search",
        "scrape",
        "fetch",
        "download",
        "pdf_convert",
        "office_to_markdown",
        "view",
        "list_sessions",
        "get_session",
        "run_session",
        "steer_session",
    }
    if os.name != "nt":
        expected.add("shell_exec")
    assert expected <= observed


async def test_root_control_targets_live_stream_and_session_is_readmitted_after_cancel(tmp_path: Path) -> None:
    config_path = tmp_path / "agent-ui.yaml"
    config_path.write_text(
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
    configuration = await load_agent_ui_configuration(config_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = AsyncEvent()
    outcomes = []

    async with open_agent_ui_app(_settings(tmp_path / "state"), configuration=configuration) as app:
        app._sessions._agents = _SlowRootReconstructor(started)
        session = await app.create_session(agent_name="assistant", environment_name="native")

        async def run_root() -> None:
            outcomes.append(
                await app.run_session(
                    session_id=session.session_id,
                    prompt="wait",
                    folders=(workspace,),
                )
            )

        async with create_task_group() as tasks:
            tasks.start_soon(run_root)
            await started.wait()
            steering = await app.steer_session(session_id=session.session_id, message="focus")
            assert steering.accepted
            assert steering.enqueue_id is not None
            cancellation = await app.cancel_session(session_id=session.session_id)
            assert cancellation.accepted

        assert len(outcomes) == 1
        assert outcomes[0].result.status == "cancelled"
        assert outcomes[0].continuation.status == "not_available"
        assert not (await app.cancel_session(session_id=session.session_id)).accepted

        app._sessions._agents = _CompletedRootReconstructor()
        retried = await app.run_session(
            session_id=session.session_id,
            prompt="retry",
            folders=(workspace,),
        )
        assert retried.result.output_or_raise() == "root complete"


async def test_application_shutdown_drains_an_accepted_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    shutdown = AsyncEvent()
    close_completed = AsyncEvent()
    operation_started = AsyncEvent()
    release_operation = AsyncEvent()
    statuses: list[object] = []

    async with create_task_group() as tasks:
        app = await tasks.start(_run_until_shutdown, settings, shutdown, close_completed)

        async def slow_object_count() -> int:
            operation_started.set()
            await release_operation.wait()
            return 0

        async def read_status() -> None:
            statuses.append(await app.status())

        monkeypatch.setattr(app._store, "object_count", slow_object_count)
        tasks.start_soon(read_status)
        await operation_started.wait()
        shutdown.set()
        with fail_after(2):
            while app.state is not AppState.stopping:
                await sleep(0)
        assert not close_completed.is_set()
        release_operation.set()
        await close_completed.wait()

    assert len(statuses) == 1
    assert app.state is AppState.closed


async def test_application_shutdown_cancels_an_operation_after_its_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AgentUiSettings(storage=StorageSettings(data_root=tmp_path), shutdown_timeout_seconds=0.01)
    shutdown = AsyncEvent()
    close_completed = AsyncEvent()
    operation_started = AsyncEvent()
    errors: list[AppStateError] = []

    async with create_task_group() as tasks:
        app = await tasks.start(_run_until_shutdown, settings, shutdown, close_completed)

        async def stalled_object_count() -> int:
            operation_started.set()
            await sleep_forever()

        async def read_status() -> None:
            try:
                await app.status()
            except AppStateError as exc:
                errors.append(exc)

        monkeypatch.setattr(app._store, "object_count", stalled_object_count)
        tasks.start_soon(read_status)
        await operation_started.wait()
        shutdown.set()
        await close_completed.wait()

    assert app.state is AppState.closed
    assert len(errors) == 1
    assert errors[0].code == "app_stopping"


async def test_application_closes_after_external_cancellation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with create_task_group() as tasks:
        app = await tasks.start(_hold_application, settings)
        tasks.cancel_scope.cancel()

    assert app.state is AppState.closed


async def test_application_marks_closed_when_store_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    original_open_store = app_module.open_local_store

    @asynccontextmanager
    async def failing_close(storage: StorageSettings) -> AsyncGenerator[storage_runtime.LocalStore]:
        async with original_open_store(storage) as store:
            yield store
        raise RuntimeError("store close failed")

    monkeypatch.setattr(app_module, "open_local_store", failing_close)
    retained: AgentUiApp | None = None
    with pytest.raises(RuntimeError, match="store close failed"):
        async with open_agent_ui_app(settings) as app:
            retained = app

    assert retained is not None
    assert retained.state is AppState.closed


async def test_concurrent_apps_share_one_data_root(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_agent_ui_app(settings) as first:
        async with open_agent_ui_app(settings) as second:
            assert first.state is AppState.ready
            assert second.state is AppState.ready
            assert (await first.status()).instance_id != (await second.status()).instance_id


async def test_missing_unselected_object_does_not_block_startup_but_fails_on_read(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_agent_ui_app(settings) as app:
        reference = await app._store.publish_object(
            object_kind=ObjectKind.environment_snapshot,
            object_schema_version="1",
            payload={"environment": "local"},
        )

    next((tmp_path / "objects").rglob("*.json.zst")).unlink()

    async with open_agent_ui_app(settings) as reopened:
        with pytest.raises(ObjectIntegrityError) as missing:
            await reopened._store.read_object(reference)
    assert missing.value.code == "object_unreadable"


class _SlowRootReconstructor:
    def __init__(self, started: AsyncEvent) -> None:
        self._started = started

    def reconstruct(self, snapshot, *, subagent_operator, root_capabilities=()):
        del snapshot, subagent_operator

        async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            del messages, info
            self._started.set()
            await sleep_forever()
            yield "unreachable"

        executable = HarnessBuilder().build(
            AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                definition_id=f"agent-ui:{'6' * 64}",
                model=FunctionModel(stream_function=model),
                capabilities=tuple(root_capabilities),
            )
        )
        return ReconstructedAgent(executable=executable, model_resolver=AgentUiModelResolver({}))


class _CompletedRootReconstructor:
    def reconstruct(self, snapshot, *, subagent_operator, root_capabilities=()):
        del snapshot, subagent_operator

        async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            del messages, info
            yield "root complete"

        executable = HarnessBuilder().build(
            AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                definition_id=f"agent-ui:{'7' * 64}",
                model=FunctionModel(stream_function=model),
                capabilities=tuple(root_capabilities),
            )
        )
        return ReconstructedAgent(executable=executable, model_resolver=AgentUiModelResolver({}))


class _FunctionRosterReconstructor:
    def reconstruct(self, snapshot, *, subagent_operator, root_capabilities=()):
        del snapshot
        assert isinstance(subagent_operator, AgentUiSubagentOperator)
        child = AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            definition_id=f"agent-ui:{'4' * 64}",
            model=FunctionModel(stream_function=_completed_child_model),
        )
        parent = AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            definition_id=f"agent-ui:{'5' * 64}",
            model=FunctionModel(stream_function=_delegating_parent_model),
            capabilities=(
                SubagentCapability(async_enabled=True, operator=subagent_operator),
                *root_capabilities,
            ),
            subagents=(
                SubagentDefinition(
                    name="reviewer",
                    description="Review one bounded task.",
                    agent=child,
                ),
            ),
        )
        return ReconstructedAgent(
            executable=HarnessBuilder().build(parent),
            model_resolver=AgentUiModelResolver({}),
        )


async def _delegating_parent_model(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str | DeltaToolCalls]:
    del info
    returned = any(
        isinstance(part, ToolReturnPart)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
    if not returned:
        yield {
            0: DeltaToolCall(
                name="delegate",
                json_args=json.dumps({"subagent_name": "reviewer", "prompt": "inspect"}),
                tool_call_id="delegate-1",
            )
        }
        return
    yield "accepted"


async def _completed_child_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    del messages, info
    yield "child complete"


async def _run_until_shutdown(
    settings: AgentUiSettings,
    shutdown: AsyncEvent,
    close_completed: AsyncEvent,
    *,
    task_status: TaskStatus[AgentUiApp] = TASK_STATUS_IGNORED,
) -> None:
    async with open_agent_ui_app(settings) as app:
        task_status.started(app)
        await shutdown.wait()
    close_completed.set()


async def _hold_application(
    settings: AgentUiSettings,
    *,
    task_status: TaskStatus[AgentUiApp] = TASK_STATUS_IGNORED,
) -> None:
    async with open_agent_ui_app(settings) as app:
        task_status.started(app)
        await sleep_forever()
