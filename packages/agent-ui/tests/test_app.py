from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder
from a13n_harness.capabilities import SubagentCancelResult, SubagentSteerResult, WebCapability
from a13n_ui.app import AgentUiIntegrations, AppState, open_agent_ui_app
from a13n_ui.composition import ReconstructedAgent, ThreadCompositionSelection
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.environment_runtime import EnvironmentRunService
from a13n_ui.errors import AppStateError, StoreConflictError
from a13n_ui.model_accounts import AccountStoreError, Availability, Provider
from a13n_ui.model_runtime import AgentUiModelResolver
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.storage import ObjectKind
from a13n_ui.surfaces import (
    ChildExecutionPage,
    ExternalToolResult,
    RootOperationStatus,
    ThreadDeferredResponse,
    ThreadMetadataMutation,
    ThreadMetadataPatch,
)
from anyio import Event, create_task_group, fail_after, sleep, sleep_forever
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ExternalToolset

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


async def test_configuration_observer_reloads_only_after_metadata_fingerprint_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import a13n_ui.app as app_module

    root = _write_configuration(tmp_path)
    calls = 0
    original = app_module.load_agent_ui_configuration

    async def counted(path: Path):
        nonlocal calls
        calls += 1
        return await original(path)

    monkeypatch.setattr(app_module, "load_agent_ui_configuration", counted)
    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ):
        await sleep(1.2)
        assert calls == 2
        (tmp_path / "agents/assistant.yaml").write_text(
            (tmp_path / "agents/assistant.yaml").read_text().replace("Help the user.", "Help carefully.")
        )
        with fail_after(3):
            while calls < 3:
                await sleep(0.05)
        stable_calls = calls
        await sleep(0.7)
        assert calls == stable_calls


async def test_configuration_observer_invalidates_diagnostic_changes_without_generation_change(
    tmp_path: Path,
) -> None:
    root = _write_configuration(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    accepted_source = agent_path.read_text()

    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        async with app.summary_events() as summaries:
            agent_path.write_text("invalid: [\n")
            with fail_after(3):
                invalid = await summaries.receive()
            assert invalid.kind == "configuration"
            assert (await app.status()).candidate_error_code is not None

            agent_path.write_text(accepted_source)
            with fail_after(3):
                repaired = await summaries.receive()
            assert repaired.kind == "configuration"
            status = await app.status()
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
    page = ChildExecutionPage(executions=(), total=0)

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


async def test_application_thread_queries_are_detached_keyset_views_with_metadata_cas(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        first = await app.create_thread(title="First")
        second = await app.create_thread(title="Second")
        third = await app.create_thread(title="Third")

        page = await app.list_threads(limit=2)
        assert [item.thread_id for item in page.threads] == [third.thread_id, second.thread_id]
        assert page.next_cursor is not None
        remaining = await app.list_threads(cursor=page.next_cursor, limit=2)
        assert [item.thread_id for item in remaining.threads] == [first.thread_id]

        renamed = await app.update_thread_metadata(
            thread_id=first.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=first.metadata_version,
                patch=ThreadMetadataPatch(title="Renamed"),
            ),
        )
        assert renamed.title == "Renamed"
        assert renamed.metadata_version == first.metadata_version + 1
        with pytest.raises(StoreConflictError) as stale:
            await app.update_thread_metadata(
                thread_id=first.thread_id,
                mutation=ThreadMetadataMutation(
                    expected_version=first.metadata_version,
                    patch=ThreadMetadataPatch(title=None),
                ),
            )
        assert stale.value.code == "thread_metadata_conflict"

        detail = await app.get_thread(first.thread_id)
        dumped = detail.model_dump(mode="json")
        assert detail.available_actions == ("run", "archive")
        assert "initial_state" not in json.dumps(dumped)
        assert "object_kind" not in json.dumps(dumped)

        archived = await app.update_thread_metadata(
            thread_id=first.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=renamed.metadata_version,
                patch=ThreadMetadataPatch(archived=True),
            ),
        )
        assert archived.archived
        assert (await app.get_thread(first.thread_id)).available_actions == ()


async def test_environment_run_service_directly_prepares_and_finalizes_native_project_roots(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        created = await app.create_thread()
        stored = await app._threads.get(created.thread_id)
        source = await app.current_configuration()
        assert source is not None
        executor = app._root_runs._executor
        assert isinstance(executor._environments, EnvironmentRunService)
        selection = ThreadCompositionSelection(
            thread_id=stored.thread_id,
            version=stored.configuration.version,
            project_id=stored.configuration.project_id,
            agent_source_kind=stored.configuration.agent_source.kind,
            agent_source_id=stored.configuration.agent_source.id,
            environment_profile_id=stored.configuration.environment_profile_id,
            harness_plugin_ids=stored.configuration.harness_plugin_ids,
            environment_run_extension_ids=stored.configuration.environment_run_extension_ids,
            mcp_server_ids=stored.configuration.mcp_server_ids,
        )
        published = await executor._compositions.publish(source, selection)
        plan = await executor._environments.prepare(published.value)
        finalization = await plan.finalize(timeout_seconds=1)

    assert finalization.cleanup_errors == ()
    assert len(finalization.state_publications) == 1
    assert finalization.state_publications[0].status == "unchanged"


async def test_application_creates_and_runs_root_thread(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread(title="Example")
        assert thread.parent_thread_id is None
        assert thread.configuration.project_id == "project-main"
        assert thread.configuration.agent_source.id == "agent-assistant"

        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed
        assert operation.outcome is not None
        assert operation.outcome.execution.output == "root complete"
        assert operation.outcome.continuation.status == "selected"
        selected = await app.get_thread(thread.thread_id)
        assert selected.continuation_id is not None
        assert selected.continuation_id == operation.outcome.continuation.continuation_id
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id, limit=1)
        assert transcript.total >= 1
        assert transcript.entries[0].message_kind == "request"
        assert transcript.entries[0].parts


async def test_focused_watch_cuts_over_before_snapshot_and_summary_stream_invalidates(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        async with app.summary_events() as summaries:
            async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
                assert watch.snapshot.thread.thread.thread_id == thread.thread_id
                receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="watch")
                with fail_after(3):
                    event = await watch.events.receive()
                assert event.root_thread_id == thread.thread_id
                assert event.sequence > watch.snapshot.cutover_sequence
                operation = await app.wait_root_operation(receipt.receipt_id)
                assert operation.status is RootOperationStatus.completed
            with fail_after(3):
                invalidation = await summaries.receive()
                invalidation_kinds = {invalidation.kind}
                while "thread" not in invalidation_kinds:
                    invalidation_kinds.add((await summaries.receive()).kind)
            assert invalidation.kind == "root_operation"
            assert invalidation.root_thread_id == thread.thread_id
            assert "thread" in invalidation_kinds


async def test_root_deferred_response_requires_exact_selected_request_batch(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        app._root_runs._executor._agents = _DeferredReconstructor()
        thread = await app.create_thread()

        first_receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="defer")
        first = await app.wait_root_operation(first_receipt.receipt_id)
        assert first.status is RootOperationStatus.suspended
        detail = await app.get_thread(thread.thread_id)
        assert detail.continuation_id is not None
        assert len(detail.deferred_requests) == 1
        request = detail.deferred_requests[0]
        assert request.kind == "external"
        assert request.request_id == "deferred-1"

        incomplete_receipt = await app.respond_thread(
            thread_id=thread.thread_id,
            response=ThreadDeferredResponse(
                expected_continuation_id=detail.continuation_id,
                responses=(ExternalToolResult(request_id="unexpected", result="wrong"),),
            ),
        )
        incomplete = await app.wait_root_operation(incomplete_receipt.receipt_id)
        assert incomplete.status is RootOperationStatus.failed
        assert incomplete.failure is not None
        assert incomplete.failure.code == "thread_deferred_response_incomplete"

        stale_receipt = await app.respond_thread(
            thread_id=thread.thread_id,
            response=ThreadDeferredResponse(
                expected_continuation_id="0" * 64,
                responses=(
                    ExternalToolResult(
                        request_id=request.request_id,
                        result="external result",
                    ),
                ),
            ),
        )
        stale = await app.wait_root_operation(stale_receipt.receipt_id)
        assert stale.status is RootOperationStatus.failed
        assert stale.failure is not None
        assert stale.failure.code == "thread_continuation_conflict"

        response_receipt = await app.respond_thread(
            thread_id=thread.thread_id,
            response=ThreadDeferredResponse(
                expected_continuation_id=detail.continuation_id,
                responses=(
                    ExternalToolResult(
                        request_id=request.request_id,
                        result="external result",
                    ),
                ),
            ),
        )
        resumed = await app.wait_root_operation(response_receipt.receipt_id)
        assert resumed.status is RootOperationStatus.completed
        assert resumed.outcome is not None
        assert "external result" in str(resumed.outcome.execution.output)
        refreshed = await app.get_thread(thread.thread_id)
        assert refreshed.deferred_requests == ()
        assert refreshed.continuation_id != detail.continuation_id


async def test_root_control_targets_live_run_and_thread_is_readmitted_after_cancel(
    tmp_path: Path,
) -> None:
    root = _write_configuration(tmp_path)
    started = Event()
    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        app._root_runs._executor._agents = _SlowReconstructor(started)
        thread = await app.create_thread()

        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="wait")
        await started.wait()
        active = await app.active_root_operation(thread.thread_id)
        assert active is not None
        assert active.receipt.receipt_id == receipt.receipt_id
        steering = await app.steer_root_operation(receipt_id=receipt.receipt_id, message="focus")
        assert steering.accepted
        assert steering.enqueue_id is not None
        cancellation = await app.cancel_root_operation(receipt.receipt_id)
        assert cancellation.accepted

        cancelled = await app.wait_root_operation(receipt.receipt_id)
        assert cancelled.status is RootOperationStatus.cancelled
        assert not (await app.cancel_root_operation(receipt.receipt_id)).accepted

        app._root_runs._executor._agents = _CompletedReconstructor()
        retry_receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="retry")
        retried = await app.wait_root_operation(retry_receipt.receipt_id)
        assert retried.status is RootOperationStatus.completed
        assert retried.outcome is not None
        assert retried.outcome.execution.output == "root complete"


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


async def test_cancelled_application_lifetime_still_closes_owned_coordinators(tmp_path: Path) -> None:
    ready = Event()
    captured = []

    async def app_lifetime() -> None:
        async with open_agent_ui_app(_settings(tmp_path / "state")) as app:
            captured.append(app)
            ready.set()
            await sleep_forever()

    async with create_task_group() as tasks:
        tasks.start_soon(app_lifetime)
        await ready.wait()
        tasks.cancel_scope.cancel()

    app = captured[0]
    assert app.state is AppState.closed
    assert app._root_runs._task_group is None
    assert app._subagent_operator._task_group is None


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


class _DeferredReconstructor:
    def reconstruct(self, composition, *, subagent_operator, root_capabilities=(), subscription_sources=None):
        del composition, subagent_operator, subscription_sources

        async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
            del info
            returns = [
                part
                for message in messages
                if message.kind == "request"
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if not returns:
                yield {
                    0: DeltaToolCall(
                        name="dynamic_action",
                        json_args=json.dumps({"value": 7}),
                        tool_call_id="deferred-1",
                    )
                }
                return
            yield f"handled: {returns[-1].content}"

        definition = AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            definition_id="agent-ui:agent:agent-assistant",
            model=FunctionModel(stream_function=model),
            capabilities=(
                Capability(
                    toolsets=(
                        ExternalToolset(
                            [
                                ToolDefinition(
                                    name="dynamic_action",
                                    parameters_json_schema={
                                        "type": "object",
                                        "properties": {"value": {"type": "integer"}},
                                        "required": ["value"],
                                        "additionalProperties": False,
                                    },
                                )
                            ],
                            id="external-tools",
                        ),
                    ),
                    id="dynamic-tools",
                ),
                *root_capabilities,
            ),
        )
        executable = HarnessBuilder().build(definition)
        return ReconstructedAgent(
            executable=executable,
            model_resolver=AgentUiModelResolver({}),
            definition_capability_ids=frozenset(item.id for item in definition.capabilities if item.id is not None),
        )


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
