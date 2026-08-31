from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from a13n_environment_provider import build_environment_provider_factory_catalog
from a13n_harness import HarnessState
from a13n_ui.configuration import (
    ConfigurationSettings,
    DefinitionRootSettings,
    LocalDirectorySettings,
    LocalExecutableSettings,
)
from a13n_ui.environments import EnvironmentResourceStatus, ProviderRuntimeResolver
from a13n_ui.errors import (
    EnvironmentLifecycleError,
    RuntimeGenerationError,
    RuntimeResolutionError,
    SessionError,
    StoreError,
)
from a13n_ui.host import open_agent_ui_host
from a13n_ui.runtime_generations.execution import _run_environment
from a13n_ui.runtime_generations.wire import RunnerAsyncWorkEvent
from a13n_ui.runtime_settings import RuntimeGenerationSettings
from a13n_ui.sessions import SessionRunStatus, SessionUpdate
from a13n_ui.settings import AgentUiSettings, EnvdRuntimeSettings, StorageSettings
from a13n_ui.storage import ObjectKind
from a13n_ui.storage.layout import StorageLayout
from anyio import Event, create_task_group

pytestmark = pytest.mark.anyio


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=True))


def _settings(
    data_root: Path,
    definitions: Path,
    workspace: Path,
    *,
    executable: Path | None = None,
) -> AgentUiSettings:
    return AgentUiSettings(
        storage=StorageSettings(data_root=data_root),
        configuration=ConfigurationSettings(
            definition_roots=(DefinitionRootSettings(root_id="root-user", path=definitions, writable=True),),
            local_directories=(LocalDirectorySettings(directory_id="directory-workspace", path=workspace),),
            local_executables=(
                (LocalExecutableSettings(executable_id="executable-shell", path=executable),)
                if executable is not None
                else ()
            ),
            model_adapter_keys=("a13n.pydantic-ai",),
        ),
    )


def _write_composition(
    definitions: Path,
    *,
    provision: str = "on_first_run",
    user_interaction: bool = False,
    delayed_input: str | None = None,
) -> None:
    _write_yaml(
        definitions / "models/model-main.yaml",
        {
            "schema_version": "1",
            "model_id": "model-main",
            "display_name": "Main Model",
            "provider_key": "a13n.pydantic-ai",
            "model_name": "test",
            "settings": (
                {"test_deferred": True}
                if user_interaction
                else {
                    "test_output": "completed by test model",
                    **(
                        {"test_delay_seconds": 30, "test_delayed_input": delayed_input}
                        if delayed_input is not None
                        else {}
                    ),
                }
            ),
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
                    "access": "read_write",
                }
            ],
            "default_mount": "mount-main",
            "provision": provision,
        },
    )


def _configure_background_shell(
    definitions: Path,
    *,
    command: str,
) -> None:
    model_path = definitions / "models/model-main.yaml"
    model = yaml.safe_load(model_path.read_text())
    model["settings"] = {
        "test_background_shell": True,
        "test_wait_for_process": False,
        "test_shell_command": command,
    }
    _write_yaml(model_path, model)
    environment_path = definitions / "environments/environment-main.yaml"
    environment = yaml.safe_load(environment_path.read_text())
    environment["mounts"][0]["access"] = "full"
    environment["mounts"][0]["provider_parameters"]["shell_profiles"] = [
        {"profile_id": "default", "executable": {"executable_id": "executable-shell"}}
    ]
    _write_yaml(environment_path, environment)


def _write_async_subagent_composition(
    definitions: Path,
    *,
    wait_for_child: bool = True,
    child_delay_seconds: float | None = None,
    shared_environment: bool = False,
) -> None:
    _write_yaml(
        definitions / "models/model-root.yaml",
        {
            "schema_version": "1",
            "model_id": "model-root",
            "display_name": "Root Model",
            "provider_key": "a13n.pydantic-ai",
            "model_name": "test",
            "settings": {
                "test_async_subagent": True,
                "test_subagent_name": "child-worker",
                "test_wait_for_subagent": wait_for_child,
            },
        },
    )
    _write_yaml(
        definitions / "models/model-child.yaml",
        {
            "schema_version": "1",
            "model_id": "model-child",
            "display_name": "Child Model",
            "provider_key": "a13n.pydantic-ai",
            "model_name": "test",
            "settings": {
                "test_output": "completed by child model",
                **({"test_delay_seconds": child_delay_seconds} if child_delay_seconds is not None else {}),
            },
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
    _write_yaml(
        definitions / "agents/agent-child.yaml",
        {
            "schema_version": "1",
            "agent_id": "agent-child",
            "display_name": "Child Agent",
            "model": {"kind": "model", "resource_id": "model-child"},
            "prompt": {"kind": "prompt", "resource_id": "prompt-main"},
            "async_subagents": {"tools": "disabled"},
        },
    )
    _write_yaml(
        definitions / "agents/agent-main.yaml",
        {
            "schema_version": "1",
            "agent_id": "agent-main",
            "display_name": "Main Agent",
            "model": {"kind": "model", "resource_id": "model-root"},
            "prompt": {"kind": "prompt", "resource_id": "prompt-main"},
            "subagents": [
                {
                    "name": "child-worker",
                    "description": "Complete child work.",
                    "agent": {"kind": "agent", "resource_id": "agent-child"},
                    "environment": (
                        {"mode": "shared_root", "mounts": ["mount-main"]} if shared_environment else {"mode": "none"}
                    ),
                }
            ],
            "async_subagents": {
                "tools": "standard",
                "max_active_tasks": 2,
                "max_tasks_per_run": 2,
                "max_depth": 2,
            },
        },
    )
    _write_yaml(
        definitions / "environments/environment-main.yaml",
        {
            "schema_version": "1",
            "environment_id": "environment-main",
            "display_name": "Main Environment",
            "mounts": (
                [
                    {
                        "mount_name": "mount-main",
                        "model_alias": "workspace",
                        "provider_key": "a13n.direct-local",
                        "provider_schema_version": "1",
                        "provider_parameters": {
                            "environment_id": "local-main",
                            "root": {"directory_id": "directory-workspace", "read_only": False},
                        },
                        "access": "read_write",
                    }
                ]
                if shared_environment
                else []
            ),
            "default_mount": "mount-main" if shared_environment else None,
            "provision": "on_first_run",
        },
    )


async def _create_session(application):
    return await application.create_session(
        agent_snapshot=await application.resolve_agent_snapshot("agent-main"),
        environment_snapshot=await application.resolve_environment_snapshot("environment-main"),
    )


async def _wait_for_runner_execution(application, session_id: str) -> str:
    async with asyncio.timeout(5):
        while True:
            runner = application._runtime._active
            if runner is not None:
                for request_id in runner.executions:
                    if application._runs._active.get(session_id) == request_id:
                        return request_id
            await asyncio.sleep(0.01)


async def _wait_for_new_generation(application, previous_generation_id: str) -> str:
    async with asyncio.timeout(5):
        while True:
            generation_id = (await application.runtime_status()).active_generation_id
            if generation_id is not None and generation_id != previous_generation_id:
                return generation_id
            await asyncio.sleep(0.01)


async def _wait_for_runner_idle(application) -> None:
    async with asyncio.timeout(5):
        while True:
            runner = application._runtime._active
            if runner is not None and not runner.executions:
                return
            await asyncio.sleep(0.01)


async def _wait_for_continuation_change(application, session_id: str, previous_digest: str) -> None:
    async with asyncio.timeout(5):
        while True:
            session = await application.session(session_id)
            if session.continuation.object_digest != previous_digest:
                return
            await asyncio.sleep(0.01)


async def test_local_envd_upstream_gate_is_explicit_and_never_falls_back(tmp_path: Path) -> None:
    resolver = ProviderRuntimeResolver(
        layout=StorageLayout.from_root(tmp_path / "data"),
        settings=EnvdRuntimeSettings(),
        executable_override=None,
    )
    with pytest.raises(RuntimeResolutionError) as raised:
        await resolver.resolve("a13n.local-envd")
    assert raised.value.code == "envd_runtime_manifest_missing"


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
        initial_digest = availability.resources[0].provider_state_digest
        assert initial_digest is not None
        session_id = created.session_id

    async with open_agent_ui_host(settings) as restarted:
        retained = await restarted.session_environment(session_id)
        assert retained.resources[0].provider_state_digest == initial_digest

        destroyed = await restarted.destroy_session_environment(session_id, "mount-main")
        assert destroyed.resources[0].status is EnvironmentResourceStatus.unprovisioned
        assert destroyed.resources[0].provider_state_digest is None

        available = await restarted.provision_session_environment(session_id)
        assert available.ready is True
        assert available.resources[0].status is EnvironmentResourceStatus.available
        assert available.resources[0].provider_state_digest is not None


async def test_runner_rejects_environment_success_when_provider_state_is_not_saved(
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
        persist_state = application._persist_provider_state

        async def reject_state(_update) -> None:
            raise StoreError("provider state publication failed", code="test_provider_state_failed")

        monkeypatch.setattr(application, "_persist_provider_state", reject_state)
        with pytest.raises(EnvironmentLifecycleError) as raised:
            await application.provision_session_environment(created.session_id)
        assert raised.value.code == "environment_operation_failed"
        failed = await application.session_environment(created.session_id)
        assert failed.resources[0].status is EnvironmentResourceStatus.unprovisioned
        assert failed.resources[0].provider_state_digest is None

        monkeypatch.setattr(application, "_persist_provider_state", persist_state)
        recovered = await application.provision_session_environment(created.session_id)
        assert recovered.ready is True
        assert recovered.resources[0].provider_state_digest is not None


async def test_environment_reopen_uses_latest_acknowledged_provider_state(
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
        prepared, deferred = await application._runs._prepare(
            created.session_id,
            input_value=None,
            deferred_results=None,
        )
        assert deferred is None
        snapshot = await application._composition.environment(created.environment_snapshot)
        catalog = build_environment_provider_factory_catalog(builtin_keys=("a13n.direct-local",))
        original_create_provider = type(catalog).create_provider
        lifecycle_calls: list[str] = []

        def create_provider(self, spec, *, runtime):
            provider = original_create_provider(self, spec, runtime=runtime)
            original_create = provider.create
            original_resume = provider.resume
            original_pause = provider.pause
            original_destroy = provider.destroy

            async def create(*, operation):
                lifecycle_calls.append("create")
                return await original_create(operation=operation)

            async def resume(state, *, operation):
                lifecycle_calls.append("resume")
                return await original_resume(state, operation=operation)

            async def pause(environment, *, operation, mode):
                lifecycle_calls.append("pause")
                return await original_pause(environment, operation=operation, mode=mode)

            async def destroy(state, *, operation):
                lifecycle_calls.append("destroy")
                return await original_destroy(state, operation=operation)

            monkeypatch.setattr(provider, "create", create)
            monkeypatch.setattr(provider, "resume", resume)
            monkeypatch.setattr(provider, "pause", pause)
            monkeypatch.setattr(provider, "destroy", destroy)
            return provider

        monkeypatch.setattr(type(catalog), "create_provider", create_provider)
        acknowledged_provider_states = {}

        class UnusedObjects:
            async def provider_state(self, *_args, **_kwargs):
                raise AssertionError("the acknowledged in-memory provider state should be used")

        objects = cast(Any, UnusedObjects())
        runtimes = ProviderRuntimeResolver(
            layout=StorageLayout.from_root(settings.storage.data_root),
            settings=settings.envd_runtime,
            executable_override=settings.configuration.envd_executable_override,
        )

        async def persist_state(_update) -> None:
            return None

        async with _run_environment(
            prepared.request,
            snapshot,
            objects=objects,
            factories=catalog,
            runtimes=runtimes,
            persist_state=persist_state,
            acknowledged_provider_states=acknowledged_provider_states,
        ):
            assert lifecycle_calls == ["create"]
            async with _run_environment(
                prepared.request,
                snapshot,
                objects=objects,
                factories=catalog,
                runtimes=runtimes,
                persist_state=persist_state,
                acknowledged_provider_states=acknowledged_provider_states,
            ):
                assert lifecycle_calls == ["create", "resume"]
        assert lifecycle_calls == ["create", "resume"]


async def test_runner_executes_standard_async_subagent_end_to_end(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_async_subagent_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        result = await application.run_session(created.session_id, input_value="delegate")

        assert result.status is SessionRunStatus.completed
        assert result.output == "child:completed by child model"
        assert result.continuation is not None
        assert (await application.session(created.session_id)).continuation == result.continuation
        await asyncio.sleep(0.1)
        assert application._runs._wake_tasks == {}
        assert (await application.session_async_usage(created.session_id)).requests == 1


async def test_background_child_completion_wakes_inactive_session_and_deduplicates_usage(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_async_subagent_composition(
        definitions,
        wait_for_child=False,
        child_delay_seconds=0.3,
    )
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        result = await application.run_session(created.session_id, input_value="delegate")
        assert result.continuation is not None
        await _wait_for_continuation_change(
            application,
            created.session_id,
            result.continuation.object_digest,
        )
        await _wait_for_runner_idle(application)

        usage = await application.session_async_usage(created.session_id)
        assert usage.requests == 1
        assert application._runs._wake_tasks == {}


async def test_background_shell_outlives_parent_and_wakes_from_latest_continuation(
    tmp_path: Path,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    _configure_background_shell(definitions, command="sleep 0.3; printf async-shell-done")
    settings = _settings(tmp_path / "data", definitions, workspace, executable=Path("/bin/sh"))

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        result = await application.run_session(created.session_id, input_value="start process")
        assert result.output == "process-started:process-1"
        assert result.continuation is not None
        await _wait_for_continuation_change(
            application,
            created.session_id,
            result.continuation.object_digest,
        )
        await _wait_for_runner_idle(application)
        state = await application._sessions.selected_state(created.session_id)
        assert any("process:async-shell-done" in str(message) for message in state.message_history)


async def test_session_delete_closes_background_work_before_destroying_environment(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    _configure_background_shell(definitions, command="sleep 30; printf should-not-complete")
    settings = _settings(tmp_path / "data", definitions, workspace, executable=Path("/bin/sh"))

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        result = await application.run_session(created.session_id, input_value="start process")
        assert result.output == "process-started:process-1"

        await application.delete_session(created.session_id)

        with pytest.raises(SessionError, match="Session does not exist"):
            await application.session(created.session_id)
        assert application._runs._wake_tasks == {}
        assert created.session_id not in application._runs._async_usage


async def test_background_shell_from_draining_generation_is_not_woken_or_restored(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    _configure_background_shell(definitions, command="sleep 30; printf drained-shell")
    settings = _settings(tmp_path / "data", definitions, workspace, executable=Path("/bin/sh"))
    settings = settings.model_copy(
        update={
            "runtime": RuntimeGenerationSettings(
                startup_timeout_seconds=5,
                command_timeout_seconds=2,
                drain_timeout_seconds=0.1,
                terminate_timeout_seconds=1,
                kill_timeout_seconds=1,
            )
        }
    )

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        previous_generation_id = (await application.runtime_status()).active_generation_id
        assert previous_generation_id is not None

        started = await application.run_session(created.session_id, input_value="start process")
        assert started.output == "process-started:process-1"
        assert started.continuation is not None
        restart = asyncio.create_task(application.restart_runtime())
        new_generation_id = await _wait_for_new_generation(application, previous_generation_id)
        assert restart.done() is False
        await asyncio.wait_for(restart, timeout=5)
        await asyncio.sleep(0.1)

        status = await application.runtime_status()
        previous = next(item for item in status.generations if item.generation_id == previous_generation_id)
        assert previous.exit_reason.value == "graceful"
        assert any(item.code == "runtime_drain_failed" for item in status.diagnostics)
        assert not any(item.code == "runtime_force_close_failed" for item in status.diagnostics)
        assert (await application.session(created.session_id)).continuation == started.continuation
        collected = await application.run_session(created.session_id, input_value="collect process")
        assert collected.output == "process-lost"
        assert (await application.runtime_status()).active_generation_id == new_generation_id


async def test_harness_terminal_completion_queues_behind_host_request_cleanup(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        generation_id = (await application.runtime_status()).active_generation_id
        assert generation_id is not None
        initial_continuation = created.continuation.object_digest
        request_id = "request-terminal-cleanup"
        lock = await application._runs._session_lock(created.session_id)
        event = RunnerAsyncWorkEvent(
            generation_id=generation_id,
            session_id=created.session_id,
            harness_active=True,
            source="background_process",
            kind="completion",
            thread_id="thread-parent",
            run_id="run-parent",
            agent_instance_id="agent-parent",
            reference="process-1",
            status="exited",
        )

        async with lock:
            await application._runs._register(created.session_id, request_id)
            await application._runs.handle_async_work(event)
            assert application._runs._wake_tasks == {}

            await application._runs.handle_async_work(
                event.model_copy(update={"harness_active": False, "reference": "process-2"})
            )
            wake = application._runs._wake_tasks[created.session_id]
            await asyncio.sleep(0)
            assert wake.done() is False
            await application._runs._unregister(created.session_id, request_id)

        await _wait_for_continuation_change(application, created.session_id, initial_continuation)
        await _wait_for_runner_idle(application)


async def test_background_child_outlives_parent_and_runner_restart_drains_it(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_async_subagent_composition(
        definitions,
        wait_for_child=False,
        child_delay_seconds=1.0,
        shared_environment=True,
    )
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        previous_generation_id = (await application.runtime_status()).active_generation_id
        assert previous_generation_id is not None

        result = await application.run_session(created.session_id, input_value="delegate")
        assert result.status is SessionRunStatus.completed
        assert isinstance(result.output, str)
        assert result.output.startswith("child-started:subagent-")
        restart = asyncio.create_task(application.restart_runtime())
        new_generation_id = await _wait_for_new_generation(application, previous_generation_id)
        assert restart.done() is False
        restarted = await asyncio.wait_for(restart, timeout=5)

        assert restarted.previous_generation_id == previous_generation_id
        assert restarted.active.generation_id == new_generation_id


async def test_run_selects_continuation_and_events_are_process_local(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
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


async def test_restart_routes_new_runs_while_old_generation_drains_and_remains_cancellable(
    tmp_path: Path,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, delayed_input="slow")
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        slow_session = await _create_session(application)
        fast_session = await _create_session(application)
        previous_generation_id = (await application.runtime_status()).active_generation_id
        assert previous_generation_id is not None

        slow_run = asyncio.create_task(application.run_session(slow_session.session_id, input_value="slow"))
        await _wait_for_runner_execution(application, slow_session.session_id)
        restart = asyncio.create_task(application.restart_runtime())
        new_generation_id = await _wait_for_new_generation(application, previous_generation_id)

        fast_result = await application.run_session(fast_session.session_id, input_value="fast")
        assert fast_result.status is SessionRunStatus.completed
        assert restart.done() is False
        assert await application.cancel_session(slow_session.session_id) is True
        slow_result = await slow_run
        restarted = await restart

        assert slow_result.status is SessionRunStatus.cancelled
        assert restarted.previous_generation_id == previous_generation_id
        assert restarted.active.generation_id == new_generation_id


async def test_cancelled_host_wait_keeps_runner_response_correlation_until_terminal(
    tmp_path: Path,
) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, delayed_input="slow")
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        cancelled_session = await _create_session(application)
        next_session = await _create_session(application)
        run = asyncio.create_task(application.run_session(cancelled_session.session_id, input_value="slow"))
        await _wait_for_runner_execution(application, cancelled_session.session_id)

        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        await _wait_for_runner_idle(application)

        result = await application.run_session(next_session.session_id, input_value="fast")
        assert result.status is SessionRunStatus.completed
        assert (await application.runtime_status()).active_generation_id is not None


async def test_runner_loss_preserves_the_previously_selected_continuation(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, delayed_input="slow")
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        baseline = await application.run_session(created.session_id, input_value="fast")
        assert baseline.continuation is not None

        slow_run = asyncio.create_task(application.run_session(created.session_id, input_value="slow"))
        await _wait_for_runner_execution(application, created.session_id)
        runner = application._runtime._active
        assert runner is not None
        runner.process.kill()

        with pytest.raises(RuntimeGenerationError) as raised:
            await slow_run
        assert raised.value.code == "runtime_runner_lost"
        assert (await application.session(created.session_id)).continuation == baseline.continuation

        await application.restart_runtime()
        recovered = await application.run_session(created.session_id, input_value="fast")
        assert recovered.status is SessionRunStatus.completed


async def test_completed_run_uses_runner_owned_environment_runtime(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)

        assert not hasattr(application._environments, "run_environment")
        result = await application.run_session(created.session_id, input_value="hello")
        retained = await application.session(created.session_id)

        assert result.status is SessionRunStatus.completed
        assert result.continuation == retained.continuation
        assert retained.continuation != created.continuation


async def test_suspended_run_uses_runner_owned_environment_runtime(tmp_path: Path) -> None:
    definitions = tmp_path / "definitions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_composition(definitions, user_interaction=True)
    settings = _settings(tmp_path / "data", definitions, workspace)

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)

        assert not hasattr(application._environments, "run_environment")
        result = await application.run_session(created.session_id, input_value="clarify")
        retained = await application.session(created.session_id)

        assert result.status is SessionRunStatus.suspended
        assert result.continuation == retained.continuation
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

    async with open_agent_ui_host(settings) as application:
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

    async with open_agent_ui_host(settings) as application:
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

    async with open_agent_ui_host(settings) as application:
        created = await _create_session(application)
        suspended = await application.run_session(created.session_id, input_value="clarify")
        assert suspended.status is SessionRunStatus.suspended
        requests = await application.session_deferred_requests(created.session_id)
        call = requests.calls[0]
        question = call.args_as_dict()["questions"][0]["question"]
        completed = await application.resume_session(
            created.session_id,
            results=requests.build_results(calls={call.tool_call_id: {"answers": {question: "Focused"}}}),
        )
        assert completed.status is SessionRunStatus.completed
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

        async def fail_cleanup(*args, **kwargs) -> None:
            assert kwargs["action"] == "destroy"
            raise RuntimeError("provider cleanup failed")

        monkeypatch.setattr(application, "_execute_environment_command", fail_cleanup)
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

    async with open_agent_ui_host(settings) as application:
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
