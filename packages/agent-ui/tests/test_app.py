from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder
from a13n_harness.capabilities import SubagentCancelResult, SubagentSteerResult, WebCapability
from a13n_ui.app import AgentUiIntegrations, AppState, open_agent_ui_app
from a13n_ui.composition import ReconstructedAgent
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.errors import AppStateError
from a13n_ui.model_accounts import AccountStoreError, Availability, Provider
from a13n_ui.model_runtime import AgentUiModelResolver
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.storage import ObjectKind
from a13n_ui.subagent_operator import ChildExecutionPage
from anyio import Event, create_task_group, fail_after, sleep, sleep_forever
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


def _settings(root: Path, *, shutdown_timeout_seconds: float = 1.0) -> AgentUiSettings:
    return AgentUiSettings(
        storage=StorageSettings(data_root=root),
        shutdown_timeout_seconds=shutdown_timeout_seconds,
    )


def _write_configuration(tmp_path: Path, *, instructions: str = "Help the user.") -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    root = tmp_path / "a13n-ui.yaml"
    root.write_text('schema_version: "2"\ndefaults:\n  project: project-main\n  agent: agent-assistant\n')
    resources = {
        "models/primary.yaml": """
schema_version: "1"
kind: model
id: model-primary
name: Primary
route: openai:gpt-5
authentication: {kind: api_key, env: OPENAI_API_KEY}
""",
        "agents/assistant.yaml": f"""
schema_version: "1"
kind: agent
id: agent-assistant
name: Assistant
model: model-primary
instructions: {instructions}
""",
        "projects/main.yaml": f"""
schema_version: "1"
kind: project
id: project-main
name: Main
roots:
  - path: {workspace.as_posix()}
""",
    }
    for relative, content in resources.items():
        target = tmp_path / relative
        target.parent.mkdir(exist_ok=True)
        target.write_text(content.lstrip())
    return root


async def test_application_discovers_grok_account_without_startup_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuer = "https://issuer.example"
    client_id = "client-id"
    scope = f"{issuer}::{client_id}"
    auth_path = tmp_path / "grok-auth.json"
    auth_path.write_text(
        json.dumps(
            {
                scope: {
                    "key": "access-secret",
                    "auth_mode": "oidc",
                    "create_time": datetime.now(UTC).isoformat(),
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "user_id": "account-1",
                    "refresh_token": "refresh-secret",
                    "oidc_issuer": issuer,
                    "oidc_client_id": client_id,
                }
            }
        )
    )
    monkeypatch.setenv("GROK_AUTH_PATH", str(auth_path))

    async with open_agent_ui_app(_settings(tmp_path / "state")) as app:
        projection = await app.inspect_model_account(Provider.GROK)

    assert projection.availability is Availability.AVAILABLE


async def test_broken_unused_grok_store_does_not_block_application_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "grok-auth.json"
    auth_path.write_text("not-json")
    monkeypatch.setenv("GROK_AUTH_PATH", str(auth_path))

    async with open_agent_ui_app(_settings(tmp_path / "state")) as app:
        assert app.state is AppState.ready
        with pytest.raises(AccountStoreError) as failed:
            await app.inspect_model_account(Provider.GROK)

    assert failed.value.code == "account_store_malformed"


async def test_application_starts_persists_objects_and_closes(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "state")

    async with open_agent_ui_app(settings) as app:
        retained = app
        assert app.state is AppState.ready
        reference = await app._store.publish_object(
            object_kind=ObjectKind.run_composition,
            object_schema_version="1",
            payload={"run": "root"},
        )
        assert (await app._store.read_object(reference)).payload == {"run": "root"}

    assert retained.state is AppState.closed
    with pytest.raises(AppStateError) as closed:
        await retained.status()
    assert closed.value.code == "app_not_ready"

    async with open_agent_ui_app(settings) as reopened:
        assert (await reopened._store.read_object(reference)).payload == {"run": "root"}


async def test_invalid_first_candidate_starts_with_diagnostics_and_observer_accepts_repair(
    tmp_path: Path,
) -> None:
    root = tmp_path / "a13n-ui.yaml"
    root.write_text("not: [valid\n")

    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
        configuration_error=(await _candidate_error(root)),
    ) as app:
        status = await app.status()
        assert status.accepted_generation_digest is None
        assert status.candidate_error_code is not None

        _write_configuration(tmp_path)
        with fail_after(3):
            while True:
                status = await app.status()
                if status.accepted_generation_digest is not None:
                    break
                await sleep(0.05)
        assert status.candidate_error_code is None
        assert (await app.current_configuration()).document.defaults.agent == "agent-assistant"


async def test_startup_retains_last_accepted_generation_until_repaired_tree_is_stable(
    tmp_path: Path,
) -> None:
    root = _write_configuration(tmp_path, instructions="First")
    first = await load_agent_ui_configuration(root)
    settings = _settings(tmp_path / "state")

    async with open_agent_ui_app(
        settings,
        configuration_path=root,
    ):
        pass

    (tmp_path / "agents/assistant.yaml").write_text("invalid: [\n")
    error = await _candidate_error(root)
    async with open_agent_ui_app(
        settings,
        configuration_path=root,
        configuration_error=error,
    ) as app:
        status = await app.status()
        assert status.accepted_generation_digest == first.source_digest
        assert status.candidate_error_code is not None

        _write_configuration(tmp_path, instructions="Second")
        with fail_after(3):
            while True:
                current = await app.current_configuration()
                status = await app.status()
                if current is not None and current.source_digest != first.source_digest:
                    break
                await sleep(0.05)
        assert current.agents["agent-assistant"].instructions == "Second"
        assert status.candidate_error_code is None


async def test_application_catalog_includes_host_integrations_and_returns_detached_values(
    tmp_path: Path,
) -> None:
    integrations = AgentUiIntegrations(capabilities={"host.example": WebCapability})
    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        integrations=integrations,
    ) as app:
        first = await app.list_catalog()
        second = await app.refresh_catalog()

    assert any(item.kind == "capability" and item.key == "host.example" and item.source == "host" for item in first)
    assert first == second
    assert first is not second


async def test_application_exposes_detached_child_query_and_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    page = ChildExecutionPage(executions=(), execution_offset=0, total=0)

    async with open_agent_ui_app(_settings(tmp_path / "state")) as app:

        async def query(**kwargs):
            calls.append(("query", kwargs))
            return page

        async def wait(**kwargs):
            calls.append(("wait", kwargs))
            return page

        async def steer(**kwargs):
            calls.append(("steer", kwargs))
            return SubagentSteerResult(execution_id="execution-1", accepted=False)

        async def cancel(**kwargs):
            calls.append(("cancel", kwargs))
            return SubagentCancelResult(
                execution_id="execution-1",
                accepted=False,
                status="running",
            )

        monkeypatch.setattr(app._subagent_operator, "query_child_executions", query)
        monkeypatch.setattr(app._subagent_operator, "wait_child_executions", wait)
        monkeypatch.setattr(app._subagent_operator, "steer_execution", steer)
        monkeypatch.setattr(app._subagent_operator, "cancel_execution", cancel)

        assert await app.query_child_executions(parent_thread_id="thread-parent") == page
        assert (
            await app.wait_child_executions(
                parent_thread_id="thread-parent",
                timeout_seconds=0.1,
            )
            == page
        )
        assert not (
            await app.steer_child_execution(
                parent_thread_id="thread-parent",
                execution_id="execution-1",
                message="focus",
            )
        ).accepted
        assert not (
            await app.cancel_child_execution(
                parent_thread_id="thread-parent",
                execution_id="execution-1",
            )
        ).accepted

    assert [name for name, _arguments in calls] == ["query", "wait", "steer", "cancel"]
    assert all(arguments["parent_thread_id"] == "thread-parent" for _, arguments in calls)


async def test_application_creates_and_runs_root_thread(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        app._threads._agents = _CompletedReconstructor()
        thread = await app.create_thread(title="Example")
        assert thread.parent_thread_id is None
        assert thread.configuration.project_id == "project-main"
        assert thread.configuration.agent_source.id == "agent-assistant"

        outcome = await app.run_thread(thread_id=thread.thread_id, prompt="hello")
        assert outcome.result.output_or_raise() == "root complete"
        assert outcome.continuation.status == "selected"
        selected = await app.get_thread(thread.thread_id)
        assert selected.continuation is not None
        assert selected.continuation == outcome.continuation.reference


async def test_root_control_targets_live_run_and_thread_is_readmitted_after_cancel(
    tmp_path: Path,
) -> None:
    root = _write_configuration(tmp_path)
    started = Event()
    outcomes = []

    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        app._threads._agents = _SlowReconstructor(started)
        thread = await app.create_thread()

        async def run_root() -> None:
            outcomes.append(await app.run_thread(thread_id=thread.thread_id, prompt="wait"))

        async with create_task_group() as tasks:
            tasks.start_soon(run_root)
            await started.wait()
            steering = await app.steer_thread(thread_id=thread.thread_id, message="focus")
            assert steering.accepted
            assert steering.enqueue_id is not None
            cancellation = await app.cancel_thread(thread_id=thread.thread_id)
            assert cancellation.accepted

        assert outcomes[0].result.status == "cancelled"
        assert outcomes[0].continuation.status == "not_available"
        assert not (await app.cancel_thread(thread_id=thread.thread_id)).accepted

        app._threads._agents = _CompletedReconstructor()
        retried = await app.run_thread(thread_id=thread.thread_id, prompt="retry")
        assert retried.result.output_or_raise() == "root complete"


async def test_shutdown_stops_new_admissions_and_cancels_stalled_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_ready = Event()
    stop = Event()
    operation_started = Event()
    captured = []

    async def app_lifetime() -> None:
        async with open_agent_ui_app(_settings(tmp_path / "state", shutdown_timeout_seconds=0.01)) as app:
            captured.append(app)
            app_ready.set()
            await stop.wait()

    async with create_task_group() as tasks:
        tasks.start_soon(app_lifetime)
        await app_ready.wait()
        app = captured[0]

        async def stalled_count() -> int:
            operation_started.set()
            await sleep_forever()

        errors: list[AppStateError] = []

        async def status() -> None:
            try:
                await app.status()
            except AppStateError as exc:
                errors.append(exc)

        monkeypatch.setattr(app._store, "object_count", stalled_count)
        tasks.start_soon(status)
        await operation_started.wait()
        stop.set()

    assert app.state is AppState.closed
    assert [item.code for item in errors] == ["app_stopping"]


async def _candidate_error(path: Path):
    from a13n_ui.errors import ConfigurationError

    try:
        await load_agent_ui_configuration(path)
    except ConfigurationError as exc:
        return exc
    raise AssertionError("candidate unexpectedly valid")


class _CompletedReconstructor:
    def reconstruct(self, composition, *, subagent_operator, root_capabilities=(), subscription_sources=None):
        del composition, subagent_operator, subscription_sources

        async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            del messages, info
            yield "root complete"

        return _reconstructed(model, root_capabilities)


class _SlowReconstructor:
    def __init__(self, started: Event) -> None:
        self._started = started

    def reconstruct(self, composition, *, subagent_operator, root_capabilities=(), subscription_sources=None):
        del composition, subagent_operator, subscription_sources

        async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            del messages, info
            self._started.set()
            await sleep_forever()
            yield "unreachable"

        return _reconstructed(model, root_capabilities)


def _reconstructed(model, root_capabilities) -> ReconstructedAgent:
    definition = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="agent-ui:agent:agent-assistant",
        model=FunctionModel(stream_function=model),
        capabilities=tuple(root_capabilities),
    )
    executable = HarnessBuilder().build(definition)
    return ReconstructedAgent(
        executable=executable,
        model_resolver=AgentUiModelResolver({}),
        definition_capability_ids=frozenset(item.id for item in definition.capabilities if item.id is not None),
    )
