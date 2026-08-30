from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
import yaml
from a13n_environment_provider import EnvironmentPauseMode, EnvironmentProviderError
from a13n_harness import RunModelResolver
from a13n_ui.composition import ResolvedAgentSnapshot
from a13n_ui.configuration import ConfigurationSettings, DefinitionRootSettings, LocalDirectorySettings
from a13n_ui.environments import EnvdExecutableResolver, ProviderRuntimeResolver
from a13n_ui.errors import EnvironmentLifecycleError, RuntimeResolutionError, SessionError, StoreIntegrityError
from a13n_ui.host import open_agent_ui_host
from a13n_ui.sessions import SessionLifecycleState, SessionUpdate, TurnState
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.storage.database import transaction
from a13n_ui.storage.models import (
    EventSegmentRecord,
    SessionEnvironmentAssignmentRecord,
    SessionPresentationRecord,
    ThreadCheckpointRecord,
)
from a13n_ui.storage.objects import ObjectKind, ObjectRef
from anyio import create_task_group, fail_after
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy import func, select
from sqlalchemy.pool.impl import AsyncAdaptedQueuePool

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
            orphan_retention_seconds=60,
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
    user_interaction: bool = False,
    release: str = "retain",
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
    agent = {
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
            "lifecycle": {
                "provision": provision,
                "idle": "keep_running",
                "release": release,
            },
        },
    )


async def test_local_envd_upstream_gate_is_explicit_and_never_falls_back() -> None:
    envd_mock = AsyncMock(spec=EnvdExecutableResolver)
    envd_mock.resolve.return_value = object()
    resolver = ProviderRuntimeResolver(cast("EnvdExecutableResolver", envd_mock))

    with pytest.raises(RuntimeResolutionError) as raised:
        await resolver.resolve("a13n.local-envd")

    assert raised.value.code == "local_envd_provider_unavailable"
    envd_mock.resolve.assert_awaited_once_with()


async def test_session_baseline_and_direct_local_environment_survive_restart(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        agent = await application.resolve_agent_snapshot("agent-main")
        environment = await application.resolve_environment_snapshot("environment-main")
        created = await application.create_session(
            agent_snapshot=agent,
            environment_snapshot=environment,
            creation_request_id="create-session-main",
            title="Main",
        )
        assert created.lifecycle_state is SessionLifecycleState.ready
        assert created.root.selected_checkpoint is not None
        assert created.root.selected_checkpoint.thread_id == created.root.thread_id
        before = await application.session_environment(created.session_id)
        assert before.ready is False
        snapshot = await application.environment_snapshot(environment)
        async with application._environments.run_environment(
            session_id=created.session_id,
            snapshot=snapshot,
        ):
            during = await application.session_environment(created.session_id)
            assert during.ready is True
            assert during.resources[0].selected_provider_state_digest is not None
        updated = await application.update_session(
            created.session_id,
            expected_version=created.control_version,
            update=SessionUpdate(title="Renamed"),
        )
        session_id = created.session_id
        checkpoint = updated.root.selected_checkpoint

    async with open_agent_ui_host(settings) as restarted:
        retained = await restarted.session(session_id)
        assert retained.title == "Renamed"
        assert retained.root.selected_checkpoint == checkpoint
        state = await restarted._sessions.load_state(session_id, checkpoint)
        assert state.thread_id == retained.root.thread_id
        availability = await restarted.session_environment(session_id)
        assert availability.ready is True
        snapshot = await restarted.environment_snapshot(retained.environment_snapshot)
        async with restarted._environments.run_environment(session_id=session_id, snapshot=snapshot):
            assert (await restarted.session_environment(session_id)).ready is True


async def test_eager_direct_local_session_is_ready_after_provider_state_selection(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, provision="eager")
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        availability = await application.session_environment(created.session_id)

    assert created.lifecycle_state is SessionLifecycleState.ready
    assert availability.ready is True
    assert availability.resources[0].lifecycle_state.value == "available"


async def test_foreground_turn_persists_checkpoint_and_agui_replay_across_restart(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(
        settings,
        model_resolver_factory=_test_model_resolver_factory,
    ) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        baseline = created.root.selected_checkpoint
        turn = await application.run_session_turn(
            created.session_id,
            thread_id=created.root.thread_id,
            expected_thread_version=created.root.commit_version,
            input_value="hello",
        )
        replay = await application.session_events(created.session_id)
        session_id = created.session_id

        assert turn.state is TurnState.completed
        assert turn.terminal_projection == "completed by test model"
        assert turn.selected_checkpoint is not None
        assert turn.selected_checkpoint != baseline
        assert len(turn.run_ids) == 1
        assert replay
        assert [item.stored.presentation_sequence for item in replay] == list(range(1, len(replay) + 1))
        assert replay[-1].stored.event.type == "RUN_FINISHED"
        assert {item.origin for item in replay} == {"replay"}

    async with open_agent_ui_host(settings) as restarted:
        retained = await restarted.session(session_id)
        replayed = await restarted.session_events(session_id)

        assert retained.root.turns[-1].state is TurnState.completed
        assert retained.root.selected_checkpoint == retained.root.turns[-1].selected_checkpoint
        assert [item.stored.event_id for item in replayed] == [item.stored.event_id for item in replay]


async def test_waiting_turn_resumes_from_exact_deferred_object(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, user_interaction=True)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(
        settings,
        model_resolver_factory=_deferred_model_resolver_factory,
    ) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        waiting = await application.run_session_turn(
            created.session_id,
            thread_id=created.root.thread_id,
            expected_thread_version=created.root.commit_version,
            input_value="clarify",
        )

        assert waiting.state is TurnState.waiting
        assert waiting.pending_deferred is not None
        assert waiting.selected_checkpoint is not None
        requests = await application.session_deferred_requests(
            created.session_id,
            turn_id=waiting.turn_id,
        )
        call_id = requests.calls[0].tool_call_id
        completed = await application.resume_session_turn(
            created.session_id,
            turn_id=waiting.turn_id,
            expected_thread_version=(await application.session(created.session_id)).root.commit_version,
            results=requests.build_results(
                calls={
                    call_id: {
                        "answers": {"Which scope should be used?": "Focused"},
                    }
                }
            ),
        )

        assert completed.state is TurnState.completed
        assert "Focused" in str(completed.terminal_projection)
        assert completed.pending_deferred is None
        assert len(completed.run_ids) == 2
        replay = await application.session_events(created.session_id)
        assert sum(item.stored.event.type == "CUSTOM" for item in replay) >= 1
        assert replay[-1].stored.event.type == "RUN_FINISHED"


async def test_event_subscription_has_gap_free_replay_to_live_cutover(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(
        settings,
        model_resolver_factory=_test_model_resolver_factory,
    ) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        await application.run_session_turn(
            created.session_id,
            thread_id=created.root.thread_id,
            expected_thread_version=created.root.commit_version,
            input_value="first",
        )

        async with application.subscribe_session_events(created.session_id) as subscription:
            replay = await application.session_events(
                created.session_id,
                through_sequence=subscription.replay_through,
            )
            retained = await application.session(created.session_id)
            second = await application.run_session_turn(
                created.session_id,
                thread_id=created.root.thread_id,
                expected_thread_version=retained.root.commit_version,
                input_value="second",
            )
            live = []
            with fail_after(5):
                while not live or live[-1].stored.event.type != "RUN_FINISHED":
                    live.append(await subscription.receive.receive())

        assert second.state is TurnState.completed
        assert replay[-1].stored.presentation_sequence == subscription.replay_through
        assert {item.origin for item in replay} == {"replay"}
        assert {item.origin for item in live} == {"live"}
        combined = (*replay, *live)
        assert [item.stored.presentation_sequence for item in combined] == list(
            range(1, combined[-1].stored.presentation_sequence + 1)
        )
        assert len({item.stored.event_id for item in combined}) == len(combined)


async def test_startup_recovers_file_first_event_segment(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(
        settings,
        model_resolver_factory=_test_model_resolver_factory,
    ) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        await application.run_session_turn(
            created.session_id,
            thread_id=created.root.thread_id,
            expected_thread_version=created.root.commit_version,
            input_value="recover",
        )
        before = await application.session_events(created.session_id)
        session_id = created.session_id

        async with transaction(application._store.database.sessions) as database_session:
            segment = (
                await database_session.execute(
                    select(EventSegmentRecord)
                    .where(EventSegmentRecord.session_id == session_id)
                    .order_by(EventSegmentRecord.first_sequence.desc())
                    .limit(1)
                )
            ).scalar_one()
            presentation = await database_session.get(SessionPresentationRecord, session_id)
            assert presentation is not None
            presentation.next_sequence = segment.first_sequence
            presentation.last_segment_digest = segment.previous_segment_digest
            await database_session.delete(segment)

    async with open_agent_ui_host(settings) as restarted:
        recovered = await restarted.session_events(session_id)
        diagnostics = await restarted.recovery_diagnostics()

        assert [item.stored.event_id for item in recovered] == [item.stored.event_id for item in before]
        assert not any(item.code == "event_segment_unselected" for item in diagnostics)


async def test_terminal_commit_failure_closes_running_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(
        settings,
        model_resolver_factory=_test_model_resolver_factory,
    ) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        monkeypatch.setattr(
            application._sessions,
            "publish_turn_state",
            AsyncMock(
                side_effect=StoreIntegrityError(
                    "Synthetic checkpoint publication failure.",
                    code="synthetic_checkpoint_failure",
                )
            ),
        )

        with pytest.raises(StoreIntegrityError, match="Synthetic checkpoint"):
            await application.run_session_turn(
                created.session_id,
                thread_id=created.root.thread_id,
                expected_thread_version=created.root.commit_version,
                input_value="fail commit",
            )

        retained = await application.session(created.session_id)
        assert retained.root.active_turn_id is None
        assert retained.root.turns[-1].state is TurnState.interrupted
        assert retained.root.turns[-1].failure == {
            "code": "synthetic_checkpoint_failure",
            "message": "Synthetic checkpoint publication failure.",
            "details": {},
        }


async def test_provider_dispatch_then_host_commit_failure_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        snapshot = await application.environment_snapshot(created.environment_snapshot)
        monkeypatch.setattr(
            application._environments.repository,
            "complete_state_operation",
            AsyncMock(
                side_effect=StoreIntegrityError(
                    "Synthetic state commit failure.",
                    code="synthetic_state_commit_failure",
                )
            ),
        )

        with pytest.raises(StoreIntegrityError, match="Synthetic state commit"):
            async with application._environments.run_environment(
                session_id=created.session_id,
                snapshot=snapshot,
            ):
                pass

        availability = await application.session_environment(created.session_id)
        assert availability.resources[0].lifecycle_state.value == "unknown"
        with pytest.raises(EnvironmentLifecycleError) as raised:
            async with application._environments.run_environment(
                session_id=created.session_id,
                snapshot=snapshot,
            ):
                pass
        assert raised.value.code == "environment_reconciliation_required"


async def test_pause_waits_for_active_environment_attachment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        snapshot = await application.environment_snapshot(created.environment_snapshot)
        pause_task: asyncio.Task[object]
        async with application._environments.run_environment(
            session_id=created.session_id,
            snapshot=snapshot,
        ):
            availability = await application.session_environment(created.session_id)
            resource_id = availability.resources[0].host_resource_id
            live = application._environments._live[resource_id]
            pause_mock = AsyncMock(return_value=live.resource.state)
            monkeypatch.setattr(live.provider, "pause", pause_mock)
            pause_task = asyncio.create_task(
                application._environments.pause(resource_id, mode=EnvironmentPauseMode.FULL)
            )
            await asyncio.sleep(0)
            assert pause_task.done() is False
            pause_mock.assert_not_awaited()

        paused = await pause_task
        assert paused.lifecycle_state.value == "paused"
        pause_mock.assert_awaited_once()


async def test_delete_failure_retains_assignments_for_cleanup_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, release="destroy_when_unreferenced")
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        snapshot = await application.environment_snapshot(created.environment_snapshot)
        async with application._environments.run_environment(
            session_id=created.session_id,
            snapshot=snapshot,
        ):
            pass
        original_destroy = application._environments.destroy
        monkeypatch.setattr(
            application._environments,
            "destroy",
            AsyncMock(side_effect=RuntimeError("synthetic destroy failure")),
        )

        with pytest.raises(RuntimeError, match="synthetic destroy"):
            await application.delete_session(
                created.session_id,
                expected_version=created.control_version,
            )

        pending = await application.session(created.session_id)
        assert pending.lifecycle_state is SessionLifecycleState.cleanup_pending
        assert (await application.session_environment(created.session_id)).assignments

        monkeypatch.setattr(application._environments, "destroy", original_destroy)
        await application.delete_session(
            created.session_id,
            expected_version=pending.control_version,
        )
        with pytest.raises(SessionError) as raised:
            await application.session(created.session_id)
        assert raised.value.code == "session_missing"


async def test_hard_delete_removes_baseline_checkpoint_and_assignments(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        await application.delete_session(
            created.session_id,
            expected_version=created.control_version,
        )
        async with transaction(application._store.database.sessions) as database_session:
            checkpoint_count = int(
                (
                    await database_session.execute(
                        select(func.count())
                        .select_from(ThreadCheckpointRecord)
                        .where(ThreadCheckpointRecord.thread_id == created.root.thread_id)
                    )
                ).scalar_one()
            )
            assignment_count = int(
                (
                    await database_session.execute(
                        select(func.count())
                        .select_from(SessionEnvironmentAssignmentRecord)
                        .where(SessionEnvironmentAssignmentRecord.session_id == created.session_id)
                    )
                ).scalar_one()
            )
        assert checkpoint_count == 0
        assert assignment_count == 0


async def test_startup_blocks_session_with_unreadable_selected_checkpoint(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        checkpoint = created.root.selected_checkpoint
        assert checkpoint is not None
        checkpoint_path = application._store.objects._path_for(
            ObjectRef(
                object_kind=ObjectKind.harness_state,
                object_schema_version="1",
                logical_digest=checkpoint.state_object_digest,
            )
        )
        session_id = created.session_id

    checkpoint_path.unlink()

    async with open_agent_ui_host(settings) as restarted:
        retained = await restarted.session(session_id)
        diagnostics = await restarted.recovery_diagnostics()
        assert retained.lifecycle_state is SessionLifecycleState.blocked
        assert retained.lifecycle_failure is not None
        assert any(item.code == "session_authority_invalid" and session_id in item.detail for item in diagnostics)
        with pytest.raises(StoreIntegrityError):
            await restarted.retry_session_provisioning(
                session_id,
                expected_version=retained.control_version,
            )
        assert (await restarted.session(session_id)).lifecycle_state is SessionLifecycleState.blocked


async def test_startup_marks_unreadable_selected_provider_state_unknown(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        snapshot = await application.environment_snapshot(created.environment_snapshot)
        async with application._environments.run_environment(
            session_id=created.session_id,
            snapshot=snapshot,
        ):
            pass
        availability = await application.session_environment(created.session_id)
        resource = availability.resources[0]
        assert resource.selected_provider_state_digest is not None
        provider_state_path = application._store.objects._path_for(
            ObjectRef(
                object_kind=ObjectKind.provider_state,
                object_schema_version="1",
                logical_digest=resource.selected_provider_state_digest,
            )
        )
        session_id = created.session_id

    provider_state_path.unlink()

    async with open_agent_ui_host(settings) as restarted:
        availability = await restarted.session_environment(session_id)
        diagnostics = await restarted.recovery_diagnostics()
        assert availability.resources[0].lifecycle_state.value == "unknown"
        assert any(
            item.code == "provider_state_invalid" and resource.host_resource_id in item.detail for item in diagnostics
        )


async def test_external_task_cancellation_re_raises_after_durable_run_cleanup(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)
    model_started = asyncio.Event()
    never_complete = asyncio.Event()

    async def resolver_factory(snapshot: ResolvedAgentSnapshot) -> RunModelResolver:
        model_ids = {node.model.definition.model_id for node in snapshot.resolved_agents}

        async def stream(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
            model_started.set()
            await never_complete.wait()
            yield "unexpected"

        model = FunctionModel(stream_function=stream)

        async def resolve(_context, model_id: str):
            assert model_id in model_ids
            return model

        return resolve

    async with open_agent_ui_host(settings, model_resolver_factory=resolver_factory) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        task = asyncio.create_task(
            application.run_session_turn(
                created.session_id,
                thread_id=created.root.thread_id,
                expected_thread_version=created.root.commit_version,
                input_value="cancel",
            )
        )
        with fail_after(5):
            await model_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        retained = await application.session(created.session_id)
        assert retained.root.active_turn_id is None
        assert retained.root.turns[-1].state is TurnState.cancelled


async def test_startup_blocks_waiting_turn_with_unreadable_deferred_authority(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, user_interaction=True)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(
        settings,
        model_resolver_factory=_deferred_model_resolver_factory,
    ) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        waiting = await application.run_session_turn(
            created.session_id,
            thread_id=created.root.thread_id,
            expected_thread_version=created.root.commit_version,
            input_value="wait",
        )
        pending = waiting.pending_deferred
        assert pending is not None
        deferred_path = application._store.objects._path_for(
            ObjectRef(
                object_kind=ObjectKind.deferred_requests,
                object_schema_version="1",
                logical_digest=pending.object_digest,
            )
        )
        session_id = created.session_id

    deferred_path.unlink()

    async with open_agent_ui_host(settings) as restarted:
        retained = await restarted.session(session_id)
        diagnostics = await restarted.recovery_diagnostics()
        assert retained.lifecycle_state is SessionLifecycleState.blocked
        assert any(item.code == "session_authority_invalid" and session_id in item.detail for item in diagnostics)


async def test_known_failed_resource_is_not_reused_or_implicitly_recreated(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        snapshot = await application.environment_snapshot(created.environment_snapshot)
        async with application._environments.run_environment(
            session_id=created.session_id,
            snapshot=snapshot,
        ):
            pass
        resource_id = (await application.session_environment(created.session_id)).resources[0].host_resource_id

        with pytest.raises(EnvironmentProviderError):
            await application._environments.pause(resource_id, mode=EnvironmentPauseMode.FULL)

        failed = await application.session_environment(created.session_id)
        assert failed.resources[0].lifecycle_state.value == "failed"
        with pytest.raises(EnvironmentLifecycleError) as raised:
            async with application._environments.run_environment(
                session_id=created.session_id,
                snapshot=snapshot,
            ):
                pass
        assert raised.value.code == "environment_resource_failed"


async def test_anyio_cancellation_restores_environment_borrow_invariants(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)
    model_started = asyncio.Event()
    never_complete = asyncio.Event()

    async def resolver_factory(snapshot: ResolvedAgentSnapshot) -> RunModelResolver:
        model_ids = {node.model.definition.model_id for node in snapshot.resolved_agents}

        async def stream(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
            model_started.set()
            await never_complete.wait()
            yield "unexpected"

        model = FunctionModel(stream_function=stream)

        async def resolve(_context, model_id: str):
            assert model_id in model_ids
            return model

        return resolve

    async with open_agent_ui_host(settings, model_resolver_factory=resolver_factory) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )

        async def run() -> None:
            await application.run_session_turn(
                created.session_id,
                thread_id=created.root.thread_id,
                expected_thread_version=created.root.commit_version,
                input_value="cancel scope",
            )

        async with create_task_group() as tasks:
            tasks.start_soon(run)
            with fail_after(5):
                await model_started.wait()
            tasks.cancel_scope.cancel()

        pool = application._store.database.engine.pool
        assert isinstance(pool, AsyncAdaptedQueuePool)
        assert pool.checkedout() == 0

        availability = await application.session_environment(created.session_id)
        resource_id = availability.resources[0].host_resource_id
        assert application._environments._borrow_counts == {}
        assert application._environments._lifecycle_pending == set()
        with fail_after(5):
            destroyed = await application._environments.destroy(resource_id)
        assert destroyed.lifecycle_state.value == "destroyed"


async def test_cleanup_retry_reconciles_unknown_destroy_before_detach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, release="destroy_when_unreferenced")
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await application.create_session(
            agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
            environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
        )
        snapshot = await application.environment_snapshot(created.environment_snapshot)
        async with application._environments.run_environment(
            session_id=created.session_id,
            snapshot=snapshot,
        ):
            pass
        original_complete = application._environments.repository.complete_absent_operation
        monkeypatch.setattr(
            application._environments.repository,
            "complete_absent_operation",
            AsyncMock(
                side_effect=StoreIntegrityError(
                    "Synthetic destroy completion failure.",
                    code="synthetic_destroy_completion_failure",
                )
            ),
        )

        with pytest.raises(StoreIntegrityError, match="Synthetic destroy completion"):
            await application.delete_session(
                created.session_id,
                expected_version=created.control_version,
            )
        pending = await application.session(created.session_id)
        availability = await application.session_environment(created.session_id)
        assert pending.lifecycle_state is SessionLifecycleState.cleanup_pending
        assert availability.assignments
        assert availability.resources[0].lifecycle_state.value == "unknown"

        monkeypatch.setattr(
            application._environments.repository,
            "complete_absent_operation",
            original_complete,
        )
        await application.delete_session(
            created.session_id,
            expected_version=pending.control_version,
        )
        with pytest.raises(SessionError) as raised:
            await application.session(created.session_id)
        assert raised.value.code == "session_missing"
