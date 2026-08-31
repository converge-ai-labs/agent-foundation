from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml
from a13n_harness import HarnessState, RunModelResolver
from a13n_ui.composition import ResolvedAgentSnapshot
from a13n_ui.configuration import ConfigurationSettings, DefinitionRootSettings, LocalDirectorySettings
from a13n_ui.environments import EnvironmentResourceStatus, ProviderRuntimeResolver
from a13n_ui.errors import (
    EnvironmentLifecycleError,
    RunCoordinationError,
    RuntimeResolutionError,
    SessionError,
    StoreError,
)
from a13n_ui.host import open_agent_ui_host
from a13n_ui.sessions import SessionRunStatus, SessionUpdate
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.storage import ObjectKind
from anyio import Event, create_task_group
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.models.test import TestModel

pytestmark = pytest.mark.anyio


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=True))


def _settings(data_root: Path, definitions: Path, workspace: Path) -> AgentUiSettings:
    return AgentUiSettings(
        storage=StorageSettings(data_root=data_root),
        configuration=ConfigurationSettings(
            definition_roots=(DefinitionRootSettings(root_id="root-user", path=definitions, writable=True),),
            local_directories=(LocalDirectorySettings(directory_id="directory-workspace", path=workspace),),
            model_adapter_keys=("a13n.pydantic-ai",),
        ),
    )


async def _test_model_resolver_factory(snapshot: ResolvedAgentSnapshot) -> RunModelResolver:
    model_ids = {node.model.definition.model_id for node in snapshot.resolved_agents}
    model = TestModel(custom_output_text="completed by test model")

    async def resolve(_context, model_id: str):
        assert model_id in model_ids
        return model

    return resolve


async def _deferred_model_resolver_factory(snapshot: ResolvedAgentSnapshot) -> RunModelResolver:
    model_ids = {node.model.definition.model_id for node in snapshot.resolved_agents}

    async def stream(messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="ask_user_question",
                    json_args=json.dumps(
                        {
                            "questions": [
                                {
                                    "header": "Scope",
                                    "question": "Which scope should be used?",
                                    "options": [
                                        {"label": "Focused", "description": "Use focused scope."},
                                        {"label": "Broad", "description": "Use broad scope."},
                                    ],
                                    "multiSelect": False,
                                }
                            ]
                        }
                    ),
                    tool_call_id="question-1",
                )
            }
        else:
            yield f"answer:{returns[-1].content}"

    model = FunctionModel(stream_function=stream)

    async def resolve(_context, model_id: str):
        assert model_id in model_ids
        return model

    return resolve


def _write_composition(
    definitions: Path,
    *,
    provision: str = "on_first_run",
    idle: str = "keep_running",
    user_interaction: bool = False,
) -> None:
    _write_yaml(
        definitions / "models/model-main.yaml",
        {
            "schema_version": "1",
            "model_id": "model-main",
            "display_name": "Main Model",
            "provider_key": "a13n.pydantic-ai",
            "model_name": "test-v1",
        },
    )
    _write_yaml(
        definitions / "prompts/prompt-main.yaml",
        {
            "schema_version": "1",
            "prompt_id": "prompt-main",
            "display_name": "Main Prompt",
            "system_prompt_blocks": [{"content": "Be concise."}],
        },
    )
    agent: dict[str, object] = {
        "schema_version": "1",
        "agent_id": "agent-main",
        "display_name": "Main Agent",
        "model": {"kind": "model", "resource_id": "model-main"},
        "prompt": {"kind": "prompt", "resource_id": "prompt-main"},
        "async_subagents": {"tools": "disabled"},
    }
    if user_interaction:
        agent["capabilities"] = [{"key": "a13n.user-interaction", "schema_version": "1"}]
    _write_yaml(definitions / "agents/agent-main.yaml", agent)
    _write_yaml(
        definitions / "environments/environment-main.yaml",
        {
            "schema_version": "1",
            "environment_id": "environment-main",
            "display_name": "Main Environment",
            "mounts": [
                {
                    "mount_name": "mount-main",
                    "model_alias": "workspace",
                    "provider_key": "a13n.direct-local",
                    "provider_schema_version": "1",
                    "provider_parameters": {
                        "environment_id": "local-main",
                        "root": {"directory_id": "directory-workspace", "read_only": False},
                    },
                    "permission_ceiling": ["files"],
                }
            ],
            "default_mount": "mount-main",
            "lifecycle": {"provision": provision, "idle": idle},
        },
    )


async def _create_session(application):
    return await application.create_session(
        agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
        environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
    )


async def test_local_envd_upstream_gate_is_explicit_and_never_falls_back() -> None:
    with pytest.raises(RuntimeResolutionError) as raised:
        await ProviderRuntimeResolver().resolve("a13n.local-envd")
    assert raised.value.code == "local_envd_provider_unavailable"


async def test_session_metadata_and_continuation_survive_restart(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        baseline = created.continuation
        updated = await application.update_session(
            created.session_id,
            update=SessionUpdate(title="Renamed", pinned=True),
        )
        session_id = created.session_id
        assert updated.title == "Renamed"
        assert updated.continuation == baseline
        availability = await application.session_environment(session_id)
        assert availability.ready is False
        assert availability.resources[0].status is EnvironmentResourceStatus.unprovisioned

    async with open_agent_ui_host(settings) as restarted:
        retained = await restarted.session(session_id)
        state = await restarted._sessions.selected_state(session_id)
        assert retained.title == "Renamed"
        assert retained.pinned is True
        assert retained.continuation == baseline
        assert state.message_history == ()


async def test_eager_environment_persists_latest_provider_state(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, provision="eager")
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        availability = await application.session_environment(created.session_id)
        assert availability.ready is True
        assert availability.resources[0].status is EnvironmentResourceStatus.available
        assert availability.resources[0].provider_state_digest is not None


async def test_run_selects_continuation_and_events_are_process_local(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings, model_resolver_factory=_test_model_resolver_factory) as application:
        created = await _create_session(application)
        result = await application.run_session(created.session_id, input_value="hello")
        retained = await application.session(created.session_id)
        async with application.subscribe_session_events(created.session_id) as subscription:
            buffered = subscription.buffered
        session_id = created.session_id

        assert result.status is SessionRunStatus.completed
        assert result.output == "completed by test model"
        assert result.continuation is not None
        assert retained.continuation == result.continuation
        assert retained.continuation != created.continuation
        assert buffered
        assert [item.event.sequence for item in buffered] == list(range(1, len(buffered) + 1))
        assert {item.origin for item in buffered} == {"buffered"}

    async with open_agent_ui_host(settings) as restarted:
        retained = await restarted.session(session_id)
        assert retained.continuation == result.continuation
        async with restarted.subscribe_session_events(session_id) as subscription:
            assert subscription.buffered == ()


async def test_completed_continuation_is_selected_before_environment_teardown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings, model_resolver_factory=_test_model_resolver_factory) as application:
        created = await _create_session(application)
        original = application._environments.run_environment

        @asynccontextmanager
        async def failing_teardown(*, session_id, snapshot):
            async with original(session_id=session_id, snapshot=snapshot) as runtime:
                yield runtime
            raise RuntimeError("environment teardown failed")

        monkeypatch.setattr(application._environments, "run_environment", failing_teardown)
        with pytest.raises(RunCoordinationError) as raised:
            await application.run_session(created.session_id, input_value="hello")

        retained = await application.session(created.session_id)
        assert raised.value.code == "run_saved_cleanup_failed"
        assert raised.value.details["continuation_object_digest"] == retained.continuation.object_digest
        assert retained.continuation != created.continuation


async def test_suspended_continuation_is_selected_before_environment_teardown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, user_interaction=True)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings, model_resolver_factory=_deferred_model_resolver_factory) as application:
        created = await _create_session(application)
        original = application._environments.run_environment

        @asynccontextmanager
        async def failing_teardown(*, session_id, snapshot):
            async with original(session_id=session_id, snapshot=snapshot) as runtime:
                yield runtime
            raise RuntimeError("environment teardown failed")

        monkeypatch.setattr(application._environments, "run_environment", failing_teardown)
        with pytest.raises(RunCoordinationError) as raised:
            await application.run_session(created.session_id, input_value="clarify")

        retained = await application.session(created.session_id)
        assert raised.value.code == "run_saved_cleanup_failed"
        assert raised.value.details["continuation_object_digest"] == retained.continuation.object_digest
        assert retained.continuation != created.continuation
        assert (await application.session_deferred_requests(created.session_id)).calls


async def test_continuation_publication_failure_keeps_the_selected_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings, model_resolver_factory=_test_model_resolver_factory) as application:
        created = await _create_session(application)
        original = application._store.publish_object
        continuation_attempted = False

        async def fail_continuation_publication(**kwargs):
            nonlocal continuation_attempted
            if kwargs["object_kind"] is ObjectKind.session_continuation:
                continuation_attempted = True
                raise StoreError("continuation publication failed", code="test_publication_failed")
            return await original(**kwargs)

        monkeypatch.setattr(application._store, "publish_object", fail_continuation_publication)
        with pytest.raises(StoreError) as raised:
            await application.run_session(created.session_id, input_value="hello")
        assert raised.value.code == "test_publication_failed"
        assert continuation_attempted is True
        assert (await application.session(created.session_id)).continuation == created.continuation


async def test_continuation_selection_failure_keeps_the_selected_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings, model_resolver_factory=_test_model_resolver_factory) as application:
        created = await _create_session(application)

        async def fail_selection(_session_id, _continuation):
            raise SessionError("continuation selection failed", code="test_selection_failed")

        monkeypatch.setattr(application._sessions, "select_continuation", fail_selection)
        with pytest.raises(SessionError) as raised:
            await application.run_session(created.session_id, input_value="hello")
        assert raised.value.code == "test_selection_failed"
        assert (await application.session(created.session_id)).continuation == created.continuation


async def test_suspended_continuation_resumes_from_embedded_deferred_requests(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, user_interaction=True)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings, model_resolver_factory=_deferred_model_resolver_factory) as application:
        created = await _create_session(application)
        suspended = await application.run_session(created.session_id, input_value="clarify")
        assert suspended.status is SessionRunStatus.suspended
        requests = await application.session_deferred_requests(created.session_id)
        call_id = requests.calls[0].tool_call_id
        completed = await application.resume_session(
            created.session_id,
            results=requests.build_results(calls={call_id: {"answers": {"Which scope should be used?": "Focused"}}}),
        )
        assert completed.status is SessionRunStatus.completed
        assert "Focused" in str(completed.output)
        with pytest.raises(SessionError) as missing:
            await application.session_deferred_requests(created.session_id)
        assert missing.value.code == "deferred_request_missing"


async def test_concurrent_hosts_leave_one_readable_last_write_wins_continuation(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as first, open_agent_ui_host(settings) as second:
        created = await _create_session(first)
        first_continuation = await first._sessions.publish_continuation(HarnessState.new())
        second_continuation = await second._sessions.publish_continuation(HarnessState.new())

        start = Event()

        async def select(application, continuation) -> None:
            await start.wait()
            await application._sessions.select_continuation(created.session_id, continuation)

        async with create_task_group() as tasks:
            tasks.start_soon(select, first, first_continuation)
            tasks.start_soon(select, second, second_continuation)
            start.set()

        retained = await first.session(created.session_id)
        assert retained.continuation in {first_continuation, second_continuation}
        await first._sessions.load_continuation(retained.continuation)


async def test_delete_reports_environment_cleanup_failure_after_removing_the_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)

        async def fail_cleanup(_session_id: str) -> None:
            raise RuntimeError("provider cleanup failed")

        monkeypatch.setattr(application._environments, "release_session", fail_cleanup)
        with pytest.raises(EnvironmentLifecycleError) as raised:
            await application.delete_session(created.session_id)
        assert raised.value.code == "session_deleted_cleanup_failed"
        with pytest.raises(SessionError) as missing:
            await application.session(created.session_id)
        assert missing.value.code == "session_missing"


async def test_fork_uses_selected_continuation_and_updates_are_last_write_wins(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings, model_resolver_factory=_test_model_resolver_factory) as application:
        source = await _create_session(application)
        await application.run_session(source.session_id, input_value="hello")
        source = await application.session(source.session_id)
        forked = await application.fork_session(source.session_id, title="Fork")
        assert forked.parent_fork is not None
        assert forked.parent_fork.source_continuation_digest == source.continuation.object_digest

        await application.update_session(source.session_id, update=SessionUpdate(title="First"))
        latest = await application.update_session(source.session_id, update=SessionUpdate(title="Second"))
        assert latest.title == "Second"

        await application.delete_session(forked.session_id)
        with pytest.raises(SessionError) as missing:
            await application.session(forked.session_id)
        assert missing.value.code == "session_missing"
