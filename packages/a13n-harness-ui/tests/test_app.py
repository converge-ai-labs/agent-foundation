from __future__ import annotations

import json
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_harness import (
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    AgentSpec,
    HarnessBuilder,
    HarnessRunResultEvent,
    HarnessRunStream,
    HarnessState,
)
from a13n_harness.capabilities import SkillsCapability, SubagentCancelResult, SubagentSteerResult, WebCapability
from a13n_harness.environment import FILE_ACTIONS, EnvironmentError
from a13n_harness.providers.environment.commands import CommandRequest, ShellCommand
from a13n_harness.providers.environment.local_envd.provider import LocalEnvdEnvironment
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy
from a13n_harness.providers.model.oauth import GrokCredentials
from a13n_harness_ui.app import AppState, HarnessUiIntegrations, open_harness_ui_app
from a13n_harness_ui.composition import (
    AgentReconstructor,
    ReconstructedAgent,
    ResolvedContentPlugin,
    ThreadCompositionSelection,
)
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.environment_profiles import SANDBOX_PROFILE_ID
from a13n_harness_ui.environment_runtime import EnvironmentRunService
from a13n_harness_ui.errors import (
    AppStateError,
    ConfigurationError,
    LivePresentationError,
    RunCoordinationError,
    StoreConflictError,
    ThreadError,
)
from a13n_harness_ui.model_accounts import (
    DEFAULT_GROK_OAUTH_CLIENT_ID,
    DEFAULT_GROK_OAUTH_ISSUER,
    DEFAULT_GROK_OAUTH_SCOPE,
    AccountStoreError,
    Availability,
    GrokLoginRequest,
    Provider,
)
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage import ObjectKind
from a13n_harness_ui.surfaces import (
    ChildExecutionPage,
    DecisionResponseBatch,
    ExternalToolResult,
    RootOperationStatus,
    ThreadDeferredResponse,
    ThreadMetadataMutation,
    ThreadMetadataPatch,
)
from anyio import CancelScope, Event, create_task_group, fail_after, sleep, sleep_forever
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ExternalToolset

pytestmark = pytest.mark.anyio


def _settings(root: Path, *, shutdown_timeout_seconds: float = 1.0) -> HarnessUiSettings:
    return HarnessUiSettings(
        storage=StorageSettings(data_root=root),
        shutdown_timeout_seconds=shutdown_timeout_seconds,
    )


def _write_configuration(tmp_path: Path, *, instructions: str = "Help the user.") -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "1"\ndefaults:\n  project: project-main\n  agent: agent-assistant\n')
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

    async with open_harness_ui_app(_settings(tmp_path / "state")) as app:
        projection = await app.inspect_model_account(Provider.GROK)

    assert projection.availability is Availability.AVAILABLE


async def test_first_grok_login_uses_default_compatible_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "grok-auth.json"
    monkeypatch.setenv("GROK_AUTH_PATH", str(auth_path))

    async def login(request: object) -> GrokCredentials:
        assert isinstance(request, GrokLoginRequest)
        assert request.scope == DEFAULT_GROK_OAUTH_SCOPE
        return GrokCredentials(
            account_id="account-1",
            auth_mode="oidc",
            create_time=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            issuer=DEFAULT_GROK_OAUTH_ISSUER,
            client_id=DEFAULT_GROK_OAUTH_CLIENT_ID,
            access_token="access-secret",
            refresh_token="refresh-secret",
        )

    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        grok_login=login,
    ) as app:
        before = await app.inspect_model_account(Provider.GROK)
        after = await app.login_model_account(Provider.GROK)

    assert before.availability is Availability.ABSENT
    assert after.availability is Availability.AVAILABLE
    document = json.loads(auth_path.read_text())
    assert set(document) == {DEFAULT_GROK_OAUTH_SCOPE}
    assert document[DEFAULT_GROK_OAUTH_SCOPE]["user_id"] == "account-1"


async def test_broken_unused_grok_store_does_not_block_application_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "grok-auth.json"
    auth_path.write_text("not-json")
    monkeypatch.setenv("GROK_AUTH_PATH", str(auth_path))

    async with open_harness_ui_app(_settings(tmp_path / "state")) as app:
        assert app.state is AppState.ready
        with pytest.raises(AccountStoreError) as failed:
            await app.inspect_model_account(Provider.GROK)

    assert failed.value.code == "account_store_malformed"


async def test_application_starts_persists_objects_and_closes(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "state")

    async with open_harness_ui_app(settings) as app:
        retained = app
        assert app.state is AppState.ready
        assert (await app.active_work_summary()).model_dump() == {
            "root_operations": 0,
            "child_executions": 0,
        }
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

    async with open_harness_ui_app(settings) as reopened:
        assert (await reopened._store.read_object(reference)).payload == {"run": "root"}


async def test_invalid_capabilities_warn_but_allow_real_composition_and_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from a13n_harness_ui.cli import CliRequest, OutputFormat
    from a13n_harness_ui.cli_runtime import _run_management

    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: vendor.missing\n")
    original = agent.read_bytes()

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "conversation still works"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        status = await app.status()
        assert status.candidate_error_code is None
        assert len(status.capability_warnings) == 1
        assert "agent-assistant" in status.capability_warnings[0]
        assert "vendor.missing" in status.capability_warnings[0]
        code = await _run_management(
            app, CliRequest(command="config", action="validate", output_format=OutputFormat.json)
        )
        assert code == 0
        validation = json.loads(capsys.readouterr().out)
        assert validation["valid"] is True
        assert validation["capability_warnings"] == list(status.capability_warnings)
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed
        assert agent.read_bytes() == original
        agent.write_text(agent.read_text().replace("  - capability: vendor.missing\n", "  - capability: web\n"))
        await app.reload_configuration()
        assert (await app.status()).capability_warnings == ()


@pytest.mark.parametrize("previous_generation", [False, True])
@pytest.mark.parametrize("reference", ["agent", "root_review", "agent_review", "agent_review_disabled_root"])
async def test_startup_rejects_missing_model_with_actionable_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    previous_generation: bool,
    reference: str,
) -> None:
    root = _write_configuration(tmp_path, instructions="private-instructions-not-for-logs")
    settings = _settings(tmp_path / "state")
    if previous_generation:
        async with open_harness_ui_app(settings, configuration_path=root):
            pass
    agent = tmp_path / "agents/assistant.yaml"
    if reference == "agent":
        agent.write_text(agent.read_text().replace("model-primary", "model-missing"))
        expected_path = agent
        field = "model"
        code = "configuration_model_missing"
    elif reference == "root_review":
        root.write_text(root.read_text() + "security:\n  shell_review: {enable: true, model: model-missing}\n")
        expected_path = root
        field = "security.shell_review.model"
        code = "capability_model_missing"
    else:
        agent.write_text(
            agent.read_text() + "capabilities:\n"
            "  - capability: ToolPermissionsCapability\n"
            "    configuration:\n"
            "      rules: {environment.shell_exec: deny}\n"
            "      review: {model: model-missing}\n"
        )
        if reference == "agent_review_disabled_root":
            root.write_text(root.read_text() + "security:\n  shell_review: {enable: false}\n")
        expected_path = agent
        field = "capabilities.ToolPermissionsCapability.review.model"
        code = "capability_model_missing"

    caplog.clear()
    with pytest.raises(ConfigurationError) as failed:
        async with open_harness_ui_app(settings, configuration_path=root):
            pytest.fail("A missing Model must prevent startup, not fall back to an accepted generation.")

    error = failed.value
    assert error.code == code
    assert error.details["model_id"] == "model-missing"
    assert error.details["field"] == field
    reported_path = Path(error.details["path"])
    assert (reported_path if reported_path.is_absolute() else root.parent / reported_path) == expected_path
    assert "Startup aborted" in caplog.text
    assert str(expected_path.relative_to(tmp_path)) in caplog.text
    assert field in caplog.text
    assert "model-missing" in caplog.text
    assert code in caplog.text
    assert "private-instructions-not-for-logs" not in caplog.text


async def test_missing_model_during_reload_retains_accepted_generation(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        previous = (await app.status()).accepted_generation_digest
        root.write_text(root.read_text() + "security:\n  shell_review: {enable: true, model: model-missing}\n")
        await app.reload_configuration()
        status = await app.status()
        assert status.accepted_generation_digest == previous
        assert status.candidate_error_code == "capability_model_missing"


async def test_invalid_first_candidate_starts_with_diagnostics_and_observer_accepts_repair(
    tmp_path: Path,
) -> None:
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text("not: [valid\n")

    async with open_harness_ui_app(
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
    first = await load_harness_ui_configuration(root)
    settings = _settings(tmp_path / "state")

    async with open_harness_ui_app(
        settings,
        configuration_path=root,
    ):
        pass

    (tmp_path / "agents/assistant.yaml").write_text("invalid: [\n")
    error = await _candidate_error(root)
    async with open_harness_ui_app(
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
    import a13n_harness_ui.app as app_module
    from anyio import create_memory_object_stream

    root = _write_configuration(tmp_path)
    calls = 0
    original = app_module.load_harness_ui_configuration
    ticks, waiting = create_memory_object_stream[Event](0)

    async def tick(seconds):
        if seconds != 0.5:
            return await sleep(seconds)
        gate = Event()
        await ticks.send(gate)
        await gate.wait()

    async def counted(path: Path, *, content_plugin_root: Path | None = None):
        nonlocal calls
        calls += 1
        return await original(path, content_plugin_root=content_plugin_root)

    monkeypatch.setattr(app_module, "load_harness_ui_configuration", counted)
    monkeypatch.setattr(app_module, "sleep", tick)
    with fail_after(5):
        async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root):
            (await waiting.receive()).set()
            gate = await waiting.receive()  # The initial fingerprint scan has completed.
            assert calls == 2
            gate.set()
            gate = await waiting.receive()
            assert calls == 2
            (tmp_path / "agents/assistant.yaml").write_text(
                (tmp_path / "agents/assistant.yaml").read_text().replace("Help the user.", "Help carefully.")
            )
            gate.set()
            gate = await waiting.receive()
            assert calls == 3
            gate.set()
            await waiting.receive()
            assert calls == 3


async def test_configuration_observer_invalidates_diagnostic_changes_without_generation_change(
    tmp_path: Path,
) -> None:
    root = _write_configuration(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    accepted_source = agent_path.read_text()

    async with open_harness_ui_app(
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
    integrations = HarnessUiIntegrations(capabilities={"host.example": WebCapability})
    async with open_harness_ui_app(
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
    root = _write_configuration(tmp_path)

    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        await app.create_thread(thread_id="thread_parent")

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

        assert await app.query_child_executions(parent_thread_id="thread_parent") == page
        assert (
            await app.wait_child_executions(
                parent_thread_id="thread_parent",
                timeout_seconds=0.1,
            )
            == page
        )
        assert not (
            await app.steer_child_execution(
                parent_thread_id="thread_parent",
                execution_id="execution-1",
                message="focus",
            )
        ).accepted
        assert not (
            await app.cancel_child_execution(
                parent_thread_id="thread_parent",
                execution_id="execution-1",
            )
        ).accepted

    assert [name for name, _arguments in calls] == ["query", "wait", "steer", "cancel"]
    assert all(arguments["parent_thread_id"] == "thread_parent" for _, arguments in calls)


async def test_application_thread_queries_are_detached_keyset_views_with_metadata_cas(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(
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


async def test_application_lists_release_owned_environment_modes(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)

    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        profiles = await app.environment_profiles()

    assert tuple((item.profile_id, item.name, item.mode) for item in profiles) == (
        ("environment-native", "Full Control", "full-control"),
        ("environment-sandbox", "Sandbox", "sandbox"),
    )
    assert all(item.release_owned for item in profiles)
    assert all(item.canonical_host_paths for item in profiles)
    assert "outside Project roots" in profiles[0].description
    assert "denied networking" in profiles[1].description


async def test_environment_run_service_prepares_sandbox_with_canonical_host_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared: list[LocalEnvdEnvironment] = []

    async def prepare_local_envd(environment: LocalEnvdEnvironment, **scope: object) -> None:
        del scope
        prepared.append(environment)

    monkeypatch.setattr(LocalEnvdEnvironment, "_prepare", prepare_local_envd)
    root = _write_configuration(tmp_path)
    root.write_text(f"{root.read_text()}  environment_profile: {SANDBOX_PROFILE_ID}\n")
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(f"{agent.read_text()}capabilities:\n  - capability: skills\n")
    user_skills = tmp_path / "home" / ".agents" / "skills"

    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        created = await app.create_thread()
        assert created.configuration.environment_profile_id == SANDBOX_PROFILE_ID
        stored = await app._threads.get(created.thread_id)
        source = await app.current_configuration()
        assert source is not None
        executor = app._root_runs._executor
        assert isinstance(executor._environments, EnvironmentRunService)
        executor._environments._user_skills_root = user_skills
        reconstructor = executor._environments._reconstructor

        async def local_envd_runtime(_roots, **kwargs) -> LocalEnvdProviderRuntime:
            return LocalEnvdProviderRuntime(
                executable=(tmp_path / "a13n-envd").resolve(),
                allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
            )

        monkeypatch.setattr(reconstructor, "sandbox_runtime", local_envd_runtime)
        selection = ThreadCompositionSelection(
            thread_id=stored.thread_id,
            version=stored.configuration.version,
            project_id=stored.configuration.project_id,
            local_roots=stored.configuration.local_roots,
            agent_source_kind=stored.configuration.agent_source.kind,
            agent_source_id=stored.configuration.agent_source.id,
            environment_profile_id=stored.configuration.environment_profile_id,
            harness_plugin_ids=stored.configuration.harness_plugin_ids,
            environment_run_extension_ids=stored.configuration.environment_run_extension_ids,
            mcp_server_ids=stored.configuration.mcp_server_ids,
        )
        published = await executor._compositions.publish(source, selection)
        plan = await executor._environments.prepare(published.value)
        project_root = Path(published.value.project_roots[0]).as_posix()

        assert tuple(plan.environments) == (
            "workspace",
            "builtin-skills",
            "user-skills",
            "configuration",
            "thread-files",
        )
        assert isinstance(plan.environments["workspace"], LocalEnvdEnvironment)
        assert tuple(item.mount_path for item in plan._mounts) == (
            project_root,
            "/environment/builtin-skills",
            user_skills.resolve().as_posix(),
            root.parent.resolve().as_posix(),
            (tmp_path / "state/threads" / published.value.thread_id).as_posix(),
        )
        local_envd = plan.environments["workspace"]
        assert isinstance(local_envd, LocalEnvdEnvironment)
        assert prepared == [local_envd, plan.environments["thread-files"]]
        assert local_envd._configuration.working_directory == project_root
        assert plan._mounts[0].provider_root == project_root
        assert local_envd._runtime is plan.environments["thread-files"]._runtime
        finalization = await plan.finalize(timeout_seconds=1)

    assert finalization.cleanup_errors == ()
    assert len(finalization.state_publications) == 1
    assert finalization.state_publications[0].status == "unchanged"


async def test_environment_run_service_directly_prepares_and_finalizes_native_project_roots(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(
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
            local_roots=stored.configuration.local_roots,
            agent_source_kind=stored.configuration.agent_source.kind,
            agent_source_id=stored.configuration.agent_source.id,
            environment_profile_id=stored.configuration.environment_profile_id,
            harness_plugin_ids=stored.configuration.harness_plugin_ids,
            environment_run_extension_ids=stored.configuration.environment_run_extension_ids,
            mcp_server_ids=stored.configuration.mcp_server_ids,
        )
        published = await executor._compositions.publish(source, selection)
        plan = await executor._environments.prepare(published.value)
        project_root = Path(published.value.project_roots[0]).as_posix()
        async with plan.runtime.bind(
            thread_id=stored.thread_id,
            run_id="run-native-paths",
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="test", subject="a13n-harness-ui"),
                agent_instance_id="agent-native-paths",
            ),
            host_refs={},
        ) as environment:
            assert environment.snapshot.mounts[0].mount_path == project_root
            assert environment.resolve_path(f"{project_root}/readme.md").path == "/readme.md"
            scratch = tmp_path / "state/threads" / stored.thread_id / "tmp"
            assert environment.resolve_path(f"{scratch.as_posix()}/scratch.txt").path == "/tmp/scratch.txt"
            if os.name == "posix":
                scratch_command = await environment.shell.exec_captured(
                    CommandRequest(
                        command=ShellCommand(profile_id="default", script="printf scratch > scratch.txt; pwd"),
                        cwd=scratch.as_posix(),
                        output_policy=EnvironmentOutputPolicy(
                            max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"
                        ),
                    )
                )
                assert scratch_command.output.stdout.inline is not None
                assert scratch_command.output.stdout.inline.decode().strip() == scratch.as_posix()
                assert (scratch / "scratch.txt").read_text() == "scratch"

            with pytest.raises(EnvironmentError) as legacy:
                environment.resolve_path("/workspace/readme.md")
            assert legacy.value.code == "environment_selection_invalid"
            if os.name == "posix":
                pwd = await environment.shell.exec_captured(
                    CommandRequest(
                        command=ShellCommand(profile_id="default", script="pwd"),
                        cwd=project_root,
                        output_policy=EnvironmentOutputPolicy(
                            max_inline_bytes=4096,
                            max_output_bytes=4096,
                            overflow="truncate",
                        ),
                    )
                )
                assert pwd.output.stdout.inline is not None
                assert pwd.output.stdout.inline.decode().strip() == project_root
                parent = await environment.shell.exec_captured(
                    CommandRequest(
                        command=ShellCommand(profile_id="default", script="cd .. && pwd"),
                        cwd=project_root,
                        output_policy=EnvironmentOutputPolicy(
                            max_inline_bytes=4096,
                            max_output_bytes=4096,
                            overflow="truncate",
                        ),
                    )
                )
                assert parent.output.stdout.inline is not None
                assert parent.output.stdout.inline.decode().strip() == Path(project_root).parent.as_posix()
                with pytest.raises(EnvironmentError) as traversal:
                    await environment.shell.exec_captured(
                        CommandRequest(
                            command=ShellCommand(profile_id="default", script="pwd"),
                            cwd=f"{project_root}/../",
                            output_policy=EnvironmentOutputPolicy(
                                max_inline_bytes=4096,
                                max_output_bytes=4096,
                                overflow="truncate",
                            ),
                        )
                    )
                assert traversal.value.code == "environment_request_invalid"
        finalization = await plan.finalize(timeout_seconds=1)

    assert finalization.cleanup_errors == ()
    assert len(finalization.state_publications) == 1
    assert finalization.state_publications[0].status == "unchanged"


@pytest.mark.parametrize("skills_enabled", [True, False])
async def test_environment_run_service_mounts_plugin_files_read_write(tmp_path: Path, skills_enabled: bool) -> None:
    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents" / "assistant.yaml"
    if skills_enabled:
        agent.write_text(f"{agent.read_text()}capabilities:\n  - capability: skills\n")
    plugin_skills = tmp_path / "state" / "content-plugins" / "plugin-reviewer" / "skills"
    plugin_skill = plugin_skills / "review" / "SKILL.md"
    plugin_skill.parent.mkdir(parents=True)
    subagent = plugin_skills.parent / "subagents" / "explorer.md"
    subagent.parent.mkdir()
    subagent.write_text("Original subagent")
    plugin_skill.write_text("---\nname: review\ndescription: Review a change.\n---\n\nReview carefully.\n")
    user_skills = tmp_path / "home" / ".agents" / "skills"

    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        created = await app.create_thread()
        stored = await app._threads.get(created.thread_id)
        source = await app.current_configuration()
        assert source is not None
        executor = app._root_runs._executor
        assert isinstance(executor._environments, EnvironmentRunService)
        executor._environments._user_skills_root = user_skills
        selection = ThreadCompositionSelection(
            thread_id=stored.thread_id,
            version=stored.configuration.version,
            project_id=stored.configuration.project_id,
            local_roots=stored.configuration.local_roots,
            agent_source_kind=stored.configuration.agent_source.kind,
            agent_source_id=stored.configuration.agent_source.id,
            environment_profile_id=stored.configuration.environment_profile_id,
            harness_plugin_ids=stored.configuration.harness_plugin_ids,
            environment_run_extension_ids=stored.configuration.environment_run_extension_ids,
            mcp_server_ids=stored.configuration.mcp_server_ids,
        )
        published = await executor._compositions.publish(source, selection)
        composition = published.value.model_copy(
            update={
                "content_plugins": (
                    ResolvedContentPlugin(
                        plugin_id="plugin-reviewer",
                        version="1.0.0",
                        commit="2" * 40,
                        path=plugin_skills.parent.as_posix(),
                        skills_path=plugin_skills.as_posix() if skills_enabled else None,
                    ),
                )
            }
        )

        plan = await executor._environments.prepare(composition)

        expected_aliases = ("workspace", "content-plugin-1") + (
            ("builtin-skills", "user-skills") if skills_enabled else ()
        )
        assert tuple(plan.environments) == (*expected_aliases, "configuration", "thread-files")
        assert tuple(item.mount_path for item in plan._mounts) == (
            (tmp_path / "workspace").resolve().as_posix(),
            plugin_skills.parent.resolve().as_posix(),
        ) + (("/environment/builtin-skills", user_skills.resolve().as_posix()) if skills_enabled else ()) + (
            root.parent.resolve().as_posix(),
            (tmp_path / "state/threads" / composition.thread_id).as_posix(),
        )
        plugin_mount = plan._mounts[1]
        assert plugin_mount.permission_ceiling.operations == FILE_ACTIONS
        async with plan.runtime.bind(
            thread_id=stored.thread_id,
            run_id="run-native-plugin-skills",
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="test", subject="a13n-harness-ui"),
                agent_instance_id="agent-native-plugin-skills",
            ),
            host_refs={},
        ) as environment:
            await environment.files.write_text(
                plugin_skill.resolve().as_posix(),
                "---\nname: review\ndescription: Review an updated change.\n---\n\nUpdated by the Agent.\n",
                mode="replace",
            )
            await environment.files.write_text(subagent.resolve().as_posix(), "Edited subagent", mode="replace")
            with pytest.raises(EnvironmentError):
                await environment.files.write_text(
                    (tmp_path.parent / "outside.md").as_posix(),
                    "Not allowed",
                    mode="create",
                )
        finalization = await plan.finalize(timeout_seconds=1)

    assert finalization.cleanup_errors == ()
    assert subagent.read_text() == "Edited subagent"
    assert "Updated by the Agent." in plugin_skill.read_text()


async def test_environment_run_service_adds_dedicated_skill_mounts(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(f"{agent.read_text()}capabilities:\n  - capability: skills\n")
    user_skills = tmp_path / "home" / ".agents" / "skills"

    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        created = await app.create_thread()
        stored = await app._threads.get(created.thread_id)
        source = await app.current_configuration()
        assert source is not None
        executor = app._root_runs._executor
        assert isinstance(executor._environments, EnvironmentRunService)
        executor._environments._user_skills_root = user_skills
        selection = ThreadCompositionSelection(
            thread_id=stored.thread_id,
            version=stored.configuration.version,
            project_id=stored.configuration.project_id,
            local_roots=stored.configuration.local_roots,
            agent_source_kind=stored.configuration.agent_source.kind,
            agent_source_id=stored.configuration.agent_source.id,
            environment_profile_id=stored.configuration.environment_profile_id,
            harness_plugin_ids=stored.configuration.harness_plugin_ids,
            environment_run_extension_ids=stored.configuration.environment_run_extension_ids,
            mcp_server_ids=stored.configuration.mcp_server_ids,
        )
        published = await executor._compositions.publish(source, selection)
        plan = await executor._environments.prepare(published.value)

        assert tuple(plan.environments) == (
            "workspace",
            "builtin-skills",
            "user-skills",
            "configuration",
            "thread-files",
        )
        assert plan.default_environment == "workspace"
        assert user_skills.is_dir()
        async with plan.runtime.bind(
            thread_id=stored.thread_id,
            run_id="run-native-skills",
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="test", subject="a13n-harness-ui"),
                agent_instance_id="agent-native-skills",
            ),
            host_refs={},
        ) as environment:
            assert tuple(item.mount_path for item in environment.snapshot.mounts) == (
                Path(published.value.project_roots[0]).as_posix(),
                "/environment/builtin-skills",
                user_skills.resolve().as_posix(),
                root.parent.resolve().as_posix(),
                (tmp_path / "state/threads" / stored.thread_id).as_posix(),
            )
        finalization = await plan.finalize(timeout_seconds=1)

    assert finalization.cleanup_errors == ()
    assert len(finalization.state_publications) == 1
    assert finalization.state_publications[0].status == "unchanged"


@pytest.mark.parametrize("project_position", ["first", "later"])
async def test_native_skills_reuse_a_project_mount_at_the_user_skill_root(
    tmp_path: Path,
    project_position: str,
) -> None:
    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(f"{agent.read_text()}capabilities:\n  - capability: skills\n")
    user_skills = tmp_path / "home" / ".agents" / "skills"
    user_skills.mkdir(parents=True)

    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=root,
    ) as app:
        created = await app.create_thread()
        stored = await app._threads.get(created.thread_id)
        source = await app.current_configuration()
        assert source is not None
        executor = app._root_runs._executor
        assert isinstance(executor._environments, EnvironmentRunService)
        executor._environments._user_skills_root = user_skills
        selection = ThreadCompositionSelection(
            thread_id=stored.thread_id,
            version=stored.configuration.version,
            project_id=stored.configuration.project_id,
            local_roots=stored.configuration.local_roots,
            agent_source_kind=stored.configuration.agent_source.kind,
            agent_source_id=stored.configuration.agent_source.id,
            environment_profile_id=stored.configuration.environment_profile_id,
            harness_plugin_ids=stored.configuration.harness_plugin_ids,
            environment_run_extension_ids=stored.configuration.environment_run_extension_ids,
            mcp_server_ids=stored.configuration.mcp_server_ids,
        )
        published = await executor._compositions.publish(source, selection)
        project_roots = (
            (user_skills.as_posix(),)
            if project_position == "first"
            else (*published.value.project_roots, user_skills.as_posix())
        )
        composition = published.value.model_copy(update={"project_roots": project_roots})
        plan = await executor._environments.prepare(composition)

        expected_aliases = ("workspace",) if project_position == "first" else ("workspace", "workspace-2")
        expected_aliases = (*expected_aliases, "builtin-skills")
        assert tuple(plan.environments) == (*expected_aliases, "configuration", "thread-files")
        reconstructed = AgentReconstructor(user_skills_root=user_skills).reconstruct(
            composition,
            subagent_operator=None,
        )
        skills = next(
            capability
            for capability in reconstructed.executable.definition.capabilities
            if isinstance(capability, SkillsCapability)
        )
        assert user_skills.as_posix() in skills.manager.roots
        assert f"{user_skills.as_posix()}/.agents/skills" in skills.manager.roots

        async with plan.runtime.bind(
            thread_id=stored.thread_id,
            run_id=f"run-native-user-skills-{project_position}",
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="test", subject="a13n-harness-ui"),
                agent_instance_id=f"agent-native-user-skills-{project_position}",
            ),
            host_refs={},
        ) as environment:
            mount_paths = tuple(item.mount_path for item in environment.snapshot.mounts)
            assert len(mount_paths) == len(set(mount_paths))
            selected = environment.resolve_path(user_skills.as_posix())
            expected_name = "workspace" if project_position == "first" else "workspace-2"
            expected_mount = next(item for item in environment.snapshot.mounts if item.name == expected_name)
            assert selected.path == "/"
            assert expected_mount.mount_path == user_skills.as_posix()
        finalization = await plan.finalize(timeout_seconds=1)

    assert finalization.cleanup_errors == ()


async def test_application_creates_and_runs_root_thread(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(
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
        assert selected.thread.excerpt.first_input == "hello"
        assert selected.thread.excerpt.latest_input == "hello"
        assert selected.thread.excerpt.latest_reply == "root complete"
        assert selected.thread.excerpt.reply_kind == "final"
        assert selected.thread.activity_at is not None
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id, limit=1)
        assert transcript.total >= 1
        assert transcript.entries[0].position == transcript.total - 1
        assert transcript.entries[0].message_kind == "response"
        assert transcript.entries[0].parts


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("restart", [False, True])
async def test_application_retains_failed_and_interrupted_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupted: bool, restart: bool
) -> None:
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    started = Event()
    calls: list[list[ModelMessage]] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(messages)
        if len(calls) == 1:
            yield "partial progress"
            started.set()
            if interrupted:
                await sleep_forever()
            raise UnexpectedModelBehavior("provider failure with private payload")
        yield "continued"

    def reconstruct(self, composition, *, root_capabilities=(), **kwargs):
        del self, composition, kwargs
        return _reconstructed(model, root_capabilities)

    monkeypatch.setattr(AgentReconstructor, "reconstruct", reconstruct)

    async def resume(app, thread_id: str) -> None:
        receipt = await app.submit_thread(thread_id=thread_id, prompt="continue the previous task")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed
        assert "original task" in str(calls[-1])
        assert "partial progress" in str(calls[-1])

    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="original task")
            with fail_after(5):
                await started.wait()
                if interrupted:
                    while True:
                        event = await watch.events.receive()
                        if event.payload and "partial progress" in json.dumps(event.payload):
                            break
                    assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
                operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is (RootOperationStatus.cancelled if interrupted else RootOperationStatus.failed)
        selected = await app.get_thread(thread.thread_id)
        assert selected.continuation_id is not None
        assert selected.thread.excerpt.first_input == "original task"
        assert selected.thread.excerpt.latest_input == "original task"
        assert selected.thread.excerpt.reply_kind != "final"
        if not interrupted:
            assert operation.outcome is not None
            assert operation.outcome.execution.failure is not None
            message = operation.outcome.execution.failure.message
            report = next(tmp_path.glob("a13n-harness-ui-error-*.json"))
            assert str(report) in message
            assert "issues/new" in message
            assert "private payload" not in message
            assert "private payload" in report.read_text()
        if not restart:
            await resume(app, thread.thread_id)

    if restart:
        async with open_harness_ui_app(settings, configuration_path=root) as reopened:
            assert (await reopened.get_thread(thread.thread_id)).continuation_id == selected.continuation_id
            await resume(reopened, thread.thread_id)


async def test_unexpected_consumer_error_saves_state_and_reports_private_dump(tmp_path: Path, monkeypatch) -> None:
    root = _write_configuration(tmp_path)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        observe = app._store.usage.observe

        async def fail_after_terminal(*, thread_id, item):
            await observe(thread_id=thread_id, item=item)
            if isinstance(item, HarnessRunResultEvent):
                raise RuntimeError("private consumer failure")

        monkeypatch.setattr(app._store.usage, "observe", fail_after_terminal)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="original task")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.failed
        assert operation.failure is not None
        assert "issues/new" in operation.failure.message
        assert "private consumer failure" not in operation.failure.message
        assert (await app.get_thread(thread.thread_id)).continuation_id is not None
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert "original task" in str(transcript)
        assert "root complete" in str(transcript)
        assert "private consumer failure" in next(tmp_path.glob("a13n-harness-ui-error-*.json")).read_text()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_cleanup_failure_retains_complete_deferred_checkpoint(tmp_path: Path, monkeypatch, cancelled) -> None:
    root = _write_configuration(tmp_path)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    cleaning = Event()

    async def fail_cleanup(self):
        cleaning.set()
        if cancelled:
            await sleep_forever()
        raise RuntimeError("attachment cleanup failed")

    monkeypatch.setattr(HarnessRunStream, "_close_run_attachments", fail_cleanup)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        app._root_runs._executor._agents = _DeferredReconstructor()
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="defer")
        with fail_after(5):
            await cleaning.wait()
            if cancelled:
                assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
            operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is (RootOperationStatus.cancelled if cancelled else RootOperationStatus.failed)
        detail = await app.get_thread(thread.thread_id)
        assert detail.continuation_id is not None
        assert len(detail.deferred_requests) == 1
        assert detail.deferred_requests[0].kind == "external"


@pytest.mark.parametrize("prior_continuation", [False, True])
async def test_cancel_before_stream_entry_does_not_export_unavailable_state(
    tmp_path: Path, monkeypatch, caplog, prior_continuation: bool
) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        executor = app._root_runs._executor
        executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        if prior_continuation:
            first = await app.submit_thread(thread_id=thread.thread_id, prompt="first")
            assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        prior = (await app.get_thread(thread.thread_id)).continuation_id
        binding = Event()
        exports = []
        export = HarnessRunStream.export_state

        @asynccontextmanager
        async def paused_binding(**kwargs):
            binding.set()
            await sleep_forever()
            yield

        async def record_export(stream):
            exports.append(stream.run_id)
            return await export(stream)

        monkeypatch.setattr(executor, "_bind_subagent_parent", paused_binding)
        monkeypatch.setattr(HarnessRunStream, "export_state", record_export)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="not yet entered")
        with fail_after(5):
            await binding.wait()
            assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
            operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.cancelled
        assert (await app.get_thread(thread.thread_id)).continuation_id == prior
        assert exports == []
        assert "Root continuation save failed" not in caplog.text


@pytest.mark.parametrize("saved_kind", ["version", "position", "mapping", "stale", "absent"])
async def test_display_restore_validates_before_runtime_detachment(
    tmp_path: Path, monkeypatch, saved_kind: str
) -> None:
    from dataclasses import replace

    from a13n_harness.state import AgentContextStateSnapshot, CapabilityState

    root = _write_configuration(tmp_path)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        executor = app._root_runs._executor
        executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="original native history")
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        prior = (await app.get_thread(thread.thread_id)).continuation_id
        capture = executor.capture
        create_context = HarnessRunStream._create_context
        contexts = []

        async def altered_admission(**kwargs):
            admission = await capture(**kwargs)
            state = admission.previous_state
            entries = state.agent_context_state.entries
            key = "a13n.harness-ui.display-history"
            entries.pop(key)
            from a13n_harness_ui.display_history import _message_digest

            data = {
                "messages": state.model_dump(mode="json")["message_history"],
                "model_positions": list(range(len(state.message_history))),
                "pending_response_position": None,
                "model_history_digest": _message_digest(state.message_history_json),
            }
            if saved_kind == "position":
                data["model_positions"] = [9999]
            elif saved_kind == "mapping":
                data["model_positions"] = []
            elif saved_kind == "stale":
                data["model_history_digest"] = "0" * 64
            if saved_kind != "absent":
                entries[key] = CapabilityState(version="future" if saved_kind == "version" else "1", data=data)
            return replace(
                admission,
                previous_state=state.model_copy(
                    update={"agent_context_state": AgentContextStateSnapshot(entries=entries)}
                ),
            )

        async def record_context(stream, environment):
            contexts.append(stream.run_id)
            return await create_context(stream, environment)

        monkeypatch.setattr(executor, "capture", altered_admission)
        monkeypatch.setattr(HarnessRunStream, "_create_context", record_context)
        second = await app.submit_thread(thread_id=thread.thread_id, prompt="next input")
        result = await app.wait_root_operation(second.receipt_id)
        if saved_kind in {"stale", "absent"}:
            assert result.status is RootOperationStatus.completed
            assert len(contexts) == 1
            history = await app.get_thread_transcript(thread_id=thread.thread_id)
            assert "original native history" in str(history) and "next input" in str(history)
        else:
            assert result.status is RootOperationStatus.failed
            assert contexts == []
            assert (await app.get_thread(thread.thread_id)).continuation_id == prior


@pytest.mark.parametrize("failed_entry", [False, True])
async def test_native_run_excludes_display_but_every_saved_checkpoint_retains_it(
    tmp_path: Path, monkeypatch, failed_entry: bool
) -> None:
    from a13n_harness_ui.display_history import saved_display_history
    from a13n_harness_ui.storage import StoredContinuation

    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="display-only old prompt")
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        before = await app.get_thread(thread.thread_id)
        assert before.continuation_id is not None
        await app.clear_thread_context(thread_id=thread.thread_id, expected_continuation_id=before.continuation_id)

    contexts = []
    publications = []
    create_context = HarnessRunStream._create_context

    async def check_context(stream, environment):
        context = await create_context(stream, environment)
        entries = (await context.state.snapshot()).entries
        assert "a13n.harness-ui.display-history" not in entries
        contexts.append(entries)
        assert not stream._previous_state.message_history
        return context

    async def fail_after_context(*args, **kwargs):
        raise RuntimeError("failed entry with independent saved display")

    async with open_harness_ui_app(settings, configuration_path=root) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        publish = app._store.objects.publish_model

        async def check_publication(**kwargs):
            if kwargs["object_kind"] is ObjectKind.continuation:
                value = kwargs["value"]
                assert isinstance(value, StoredContinuation)
                display = saved_display_history(value.harness_state)
                assert display is not None
                assert "display-only old prompt" in str(display.items)
                publications.append(display)
            return await publish(**kwargs)

        monkeypatch.setattr(HarnessRunStream, "_create_context", check_context)
        monkeypatch.setattr(app._store.objects, "publish_model", check_publication)
        if failed_entry:
            monkeypatch.setattr("a13n_harness.execution.bind_run_plugins", fail_after_context)
        second = await app.submit_thread(thread_id=thread.thread_id, prompt="new prompt")
        result = await app.wait_root_operation(second.receipt_id)
        assert result.status is (RootOperationStatus.failed if failed_entry else RootOperationStatus.completed)
        assert len(contexts) == 1
        assert len(publications) == (1 if failed_entry else 2)

    async with open_harness_ui_app(settings, configuration_path=root) as reopened:
        history = await reopened.get_thread_transcript(thread_id=thread.thread_id)
        assert "display-only old prompt" in str(history)
        assert "root complete" in str(history)
        if not failed_entry:
            assert "new prompt" in str(history)


@pytest.mark.parametrize("export_failure", [False, True])
async def test_failed_stream_entry_still_exports_retained_state(
    tmp_path: Path, monkeypatch, caplog, export_failure: bool
) -> None:
    root = _write_configuration(tmp_path)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="first")
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        prior = (await app.get_thread(thread.thread_id)).continuation_id
        exports = []
        export = HarnessRunStream.export_state

        async def fail_after_context(*args, **kwargs):
            raise RuntimeError("stream entry failed after context creation")

        async def record_export(stream):
            exports.append(stream.run_id)
            if export_failure:
                raise RuntimeError("retained state export failed")
            return await export(stream)

        monkeypatch.setattr("a13n_harness.execution.bind_run_plugins", fail_after_context)
        monkeypatch.setattr(HarnessRunStream, "export_state", record_export)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="entry failure")
        with fail_after(5):
            operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.failed
        assert len(exports) == 1
        selected = (await app.get_thread(thread.thread_id)).continuation_id
        assert (selected == prior) is export_failure
        assert ("Root continuation save failed" in caplog.text) is export_failure


@pytest.mark.parametrize(
    ("owner", "name"),
    [
        ("root", "with_goal"),
        ("root", "detach_display_history"),
        ("collector", "__init__"),
        ("collector", "capture"),
        ("root", "with_display_history"),
        ("goal", "with_goal"),
    ],
)
async def test_root_history_preparation_runs_off_loop(tmp_path: Path, monkeypatch, owner: str, name: str) -> None:
    from a13n_harness_ui import goal, root_execution

    root = _write_configuration(tmp_path)
    loop_thread = threading.get_ident()
    target = {"root": root_execution, "collector": root_execution.DisplayHistoryCollector, "goal": goal}[owner]
    original = getattr(target, name)
    calls = []

    def checked(*args, **kwargs):
        calls.append(threading.get_ident())
        assert (threading.get_ident() == loop_thread) is (owner == "collector" and name == "capture")
        return original(*args, **kwargs)

    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        # Seed an actual saved display envelope before instrumenting its restore.
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="first")
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        monkeypatch.setattr(target, name, checked)
        second = await app.submit_thread(thread_id=thread.thread_id, prompt="second")
        with fail_after(5):
            operation = await app.wait_root_operation(second.receipt_id)
        assert calls
        assert operation.status is RootOperationStatus.completed
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert "first" in str(transcript)
        assert "second" in str(transcript)


@pytest.mark.parametrize("stage", ["prepare", "checkpoint", "terminal"])
async def test_root_history_worker_is_joined_before_cancellation_settles(
    tmp_path: Path, monkeypatch, stage: str
) -> None:
    from a13n_harness_ui import root_execution
    from anyio import from_thread

    root = _write_configuration(tmp_path)
    loop_thread = threading.get_ident()
    started = Event()
    release = threading.Event()
    finished = threading.Event()
    captures = []
    model_calls = []
    restore = root_execution.detach_display_history
    capture = root_execution.with_display_history

    def blocked(call):
        assert threading.get_ident() != loop_thread
        from_thread.run_sync(started.set)
        try:
            assert release.wait(10), "History worker was not released"
            return call()
        finally:
            finished.set()

    def restore_history(state):
        return blocked(lambda: restore(state)) if stage == "prepare" else restore(state)

    def capture_history(state, display):
        if captures and started.is_set():
            assert finished.is_set(), "Terminal serialization raced the checkpoint worker"
        captures.append(bool(display.completed))
        if (stage == "checkpoint" and len(captures) == 1) or (stage == "terminal" and display.completed):
            return blocked(lambda: capture(state, display))
        return capture(state, display)

    async def model(messages, info):
        model_calls.append(messages)
        yield "finished response"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    monkeypatch.setattr(root_execution, "detach_display_history", restore_history)
    monkeypatch.setattr(root_execution, "with_display_history", capture_history)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        selections = []
        select = app._store.threads.select_continuation

        async def record_selection(**kwargs):
            selections.append((kwargs["expected"], kwargs["replacement"]))
            return await select(**kwargs)

        monkeypatch.setattr(app._store.threads, "select_continuation", record_selection)
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="retain checkpoint input")
        try:
            with fail_after(10):
                await started.wait()
                assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
                # A bounded wait must not settle while the worker still owns history.
                waiting = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=0.02)
                assert waiting.status in {RootOperationStatus.preparing, RootOperationStatus.running}
                assert not finished.is_set()
                assert len(selections) == (1 if stage == "terminal" else 0)
        finally:
            release.set()
            with fail_after(10):
                operation = await app.wait_root_operation(receipt.receipt_id)
        assert finished.is_set()
        assert operation.status is (
            RootOperationStatus.completed if stage == "terminal" else RootOperationStatus.cancelled
        )
        assert len(model_calls) == (1 if stage == "terminal" else 0)
        assert len(selections) == (0 if stage == "prepare" else 2)
        if selections:
            assert selections[0][0] is None
            assert selections[1][0] == selections[0][1]
            assert (await app.get_thread(thread.thread_id)).continuation_id == selections[-1][1].logical_digest
            history = await app.get_thread_transcript(thread_id=thread.thread_id)
            assert "retain checkpoint input" in str(history)


async def test_checkpoint_save_failure_preserves_prior_selection(tmp_path: Path, monkeypatch) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="first")
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        prior = (await app.get_thread(thread.thread_id)).continuation_id
        publish = app._store.objects.publish_model

        async def fail_continuation(**kwargs):
            if kwargs["object_kind"] == ObjectKind.continuation:
                raise OSError("checkpoint disk unavailable")
            return await publish(**kwargs)

        monkeypatch.setattr(app._store.objects, "publish_model", fail_continuation)
        second = await app.submit_thread(thread_id=thread.thread_id, prompt="second")
        failed = await app.wait_root_operation(second.receipt_id)
        assert failed.status is RootOperationStatus.failed
        assert failed.outcome is not None
        # The first request checkpoint fails before dispatching the model.
        assert failed.outcome.execution.status == "failed"
        assert failed.outcome.continuation.status == "failed"
        retained = await app.get_thread(thread.thread_id)
        assert retained.continuation_id == prior
        assert retained.thread.excerpt.first_input == "first"
        assert retained.thread.excerpt.latest_input == "first"
        async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
            assert watch.root_stream is not None
            assert watch.root_stream.summary.base_continuation_id == prior
            assert watch.root_stream.summary.event_count > 0
        monkeypatch.setattr(app._store.objects, "publish_model", publish)
        third = await app.submit_thread(thread_id=thread.thread_id, prompt="third")
        assert (await app.wait_root_operation(third.receipt_id)).status is RootOperationStatus.completed
        async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
            assert watch.root_stream is None


async def test_focused_watch_cuts_over_before_snapshot_and_summary_stream_invalidates(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(
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
    async with open_harness_ui_app(
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
        decisions = await app.thread_decisions(
            thread_id=thread.thread_id,
            expected_continuation_id=detail.continuation_id,
        )
        assert decisions is not None
        assert decisions.requests[0].kind == "external"
        with pytest.raises(ThreadError) as changed:
            await app.get_thread_transcript(
                thread_id=thread.thread_id,
                expected_continuation_id="0" * 64,
            )
        assert changed.value.code == "thread_history_continuation_changed"
        workbench = await app.thread_activity(project_id="project-main")
        assert workbench.rows[0].pending_decision is not None
        assert workbench.rows[0].pending_decision.count == 1

        with pytest.raises(RunCoordinationError) as incomplete:
            await app.respond_thread(
                thread_id=thread.thread_id,
                response=ThreadDeferredResponse(
                    expected_continuation_id=detail.continuation_id,
                    responses=(ExternalToolResult(request_id="unexpected", result="wrong"),),
                ),
            )
        assert incomplete.value.code == "thread_deferred_response_incomplete"

        with pytest.raises(RunCoordinationError) as stale:
            await app.respond_thread(
                thread_id=thread.thread_id,
                response=ThreadDeferredResponse(
                    expected_continuation_id="0" * 64,
                    responses=(ExternalToolResult(request_id=request.request_id, result="external result"),),
                ),
            )
        assert stale.value.code == "thread_continuation_conflict"

        response_receipt = await app.respond_decisions(
            thread_id=thread.thread_id,
            response=DecisionResponseBatch(
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
    async with open_harness_ui_app(
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
        async with open_harness_ui_app(_settings(tmp_path / "state", shutdown_timeout_seconds=0.01)) as app:
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
        async with open_harness_ui_app(_settings(tmp_path / "state")) as app:
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


@pytest.mark.parametrize("owner", ["root", "child"])
async def test_owned_task_failure_preserves_error_and_closes_coordinators(tmp_path: Path, owner: str) -> None:
    ready = Event()
    storage_available_during_cleanup = False

    async def fail() -> None:
        await ready.wait()
        raise RuntimeError("owned task failed")

    async def sibling() -> None:
        nonlocal storage_available_during_cleanup
        try:
            ready.set()
            await sleep_forever()
        finally:
            with CancelScope(shield=True):
                await sleep(0.01)
                # Joining must finish before the App closes storage.
                await app._store.object_count()
                storage_available_during_cleanup = True

    with pytest.raises(ExceptionGroup, match="unhandled errors") as caught:
        async with open_harness_ui_app(_settings(tmp_path / "state")) as app:
            coordinator = app._root_runs if owner == "root" else app._subagent_operator
            assert coordinator._task_group is not None
            coordinator._task_group.start_soon(sibling)
            coordinator._task_group.start_soon(fail)
            await sleep_forever()

    expected, unexpected = caught.value.split(
        lambda exc: isinstance(exc, RuntimeError) and str(exc) == "owned task failed"
    )
    assert expected is not None and unexpected is None
    assert storage_available_during_cleanup
    assert app.state is AppState.closed
    assert app._root_runs._task_group is None
    assert app._subagent_operator._task_group is None
    # No leaked cancellation scope may poison the caller after cleanup.
    await sleep(0)


async def _candidate_error(path: Path):
    from a13n_harness_ui.errors import ConfigurationError

    try:
        await load_harness_ui_configuration(path)
    except ConfigurationError as exc:
        return exc
    raise AssertionError("candidate unexpectedly valid")


class _CompletedReconstructor:
    def reconstruct(
        self,
        composition,
        *,
        subagent_operator,
        root_capabilities=(),
        subscription_sources=None,
        pricing_catalog=None,
        memory_positions=None,
        organization=None,
    ):
        del composition, subagent_operator, subscription_sources

        async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            del messages, info
            yield "root complete"

        return _reconstructed(model, root_capabilities)


class _DeferredReconstructor:
    def reconstruct(
        self,
        composition,
        *,
        subagent_operator,
        root_capabilities=(),
        subscription_sources=None,
        pricing_catalog=None,
        memory_positions=None,
        organization=None,
    ):
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
            definition_id="a13n-harness-ui:agent:agent-assistant",
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
            model_resolver=HarnessUiModelResolver({}),
            definition_capability_ids=frozenset(item.id for item in definition.capabilities if item.id is not None),
        )


class _SlowReconstructor:
    def __init__(self, started: Event) -> None:
        self._started = started

    def reconstruct(
        self,
        composition,
        *,
        subagent_operator,
        root_capabilities=(),
        subscription_sources=None,
        pricing_catalog=None,
        memory_positions=None,
        organization=None,
    ):
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
        definition_id="a13n-harness-ui:agent:agent-assistant",
        model=FunctionModel(stream_function=model),
        capabilities=tuple(root_capabilities),
    )
    executable = HarnessBuilder().build(definition)
    return ReconstructedAgent(
        executable=executable,
        model_resolver=HarnessUiModelResolver({}),
        definition_capability_ids=frozenset(item.id for item in definition.capabilities if item.id is not None),
    )


@pytest.mark.parametrize("enabled", [False, True])
async def test_app_owns_price_updater_and_releases_it_after_failure(tmp_path, monkeypatch, enabled):
    from contextlib import contextmanager

    from pydantic_ai import prices

    events = []

    @contextmanager
    def updater():
        events.append("start")
        try:
            yield
        finally:
            events.append("stop")

    monkeypatch.setattr(prices, "update_in_background", updater)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": enabled})
    with pytest.RaisesGroup(pytest.RaisesExc(RuntimeError, match="surface failed")):
        async with open_harness_ui_app(settings):
            assert events == (["start"] if enabled else [])
            raise RuntimeError("surface failed")
    assert events == (["start", "stop"] if enabled else [])


@pytest.mark.parametrize("host_mode", ["local", "webui"])
async def test_thread_collaboration_tools_are_only_exposed_by_webui_roots(tmp_path: Path, host_mode: str) -> None:
    tools: list[set[str]] = []

    class InspectReconstructor:
        def reconstruct(self, composition, *, subagent_operator, root_capabilities=(), **kwargs):
            async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
                tools.append({tool.name for tool in info.function_tools})
                yield "inspected"

            return _reconstructed(model, root_capabilities)

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode=host_mode) as app:
        app._root_runs._executor._agents = InspectReconstructor()
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="inspect tools")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed
    expected = {"list_threads", "get_thread", "run_thread", "create_thread", "steer_thread"}
    assert bool(expected & tools[0]) is (host_mode == "webui")
    if host_mode == "webui":
        assert expected <= tools[0]


async def test_webui_create_thread_preserves_project_and_returns_before_completion(tmp_path: Path) -> None:
    from a13n_harness_ui.thread_capability import ThreadToolController

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        source = await app.create_thread(title="Source")
        controller = ThreadToolController(
            threads=app._store.threads,
            projections=app._projections,
            root_runs=app._root_runs,
            create_thread=app.create_thread,
            configurations=app._configurations,
        )
        # No model request is needed to receive an admission receipt.
        with fail_after(2):
            result = await controller.create_thread(
                source_thread_id=source.thread_id,
                prompt="new work",
                title="New work",
                agent_id=None,
            )
        assert result["ok"] is True
        created = await app.get_thread(result["thread_id"])
        assert created.thread.configuration == source.configuration
        assert result["receipt"]["thread_id"] == created.thread.thread_id
        assert created.thread.thread_id != source.thread_id
        assert (await app.list_threads()).total == 2


async def test_webui_create_reports_created_identity_when_admission_fails(tmp_path: Path, monkeypatch) -> None:
    from a13n_harness_ui.errors import HarnessUiError
    from a13n_harness_ui.thread_capability import ThreadToolController

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        source = await app.create_thread()

        async def fail_submit(**kwargs):
            raise HarnessUiError("admission rejected", code="run_rejected")

        monkeypatch.setattr(app._root_runs, "submit_prompt", fail_submit)
        controller = ThreadToolController(
            threads=app._store.threads,
            projections=app._projections,
            root_runs=app._root_runs,
            create_thread=app.create_thread,
            configurations=app._configurations,
        )
        result = await controller.create_thread(
            source_thread_id=source.thread_id, prompt="work", title=None, agent_id=None
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "run_rejected"
        assert (await app.get_thread(result["thread_id"])).thread.thread_id == result["thread_id"]
        assert (await app.list_threads()).total == 2


async def test_resume_search_pages_saved_excerpts_without_loading_history(tmp_path: Path, monkeypatch) -> None:
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        first = await app.create_thread()
        receipt = await app.submit_thread(thread_id=first.thread_id, prompt="Original STRAẞE task 100%")
        await app.wait_root_operation(receipt.receipt_id)
        receipt = await app.submit_thread(thread_id=first.thread_id, prompt="Latest searchable input")
        await app.wait_root_operation(receipt.receipt_id)
        saved = (await app.get_thread(first.thread_id)).thread
        assert saved.excerpt.first_input == "Original STRAẞE task 100%"
        assert saved.excerpt.latest_input == "Latest searchable input"
        newer = await app.create_thread(title="Newer")
        await app.update_thread_metadata(
            thread_id=first.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=first.metadata_version, patch=ThreadMetadataPatch(title="Renamed")
            ),
        )
        assert (await app.get_thread(first.thread_id)).thread.activity_at == saved.activity_at

        async def forbidden_read(*args, **kwargs):
            raise AssertionError("Session list must not hydrate history")

        with monkeypatch.context() as patch:
            patch.setattr(app._store.objects, "read_model", forbidden_read)
            for query in ("strasse", "100%", "latest searchable", "ROOT COMPLETE", "Renamed", first.thread_id):
                page = await app.list_threads(query=query, sort="activity")
                assert [item.thread_id for item in page.threads] == [first.thread_id]
            page = await app.list_threads(project_ids=("project-main",), sort="activity", limit=1)
            assert page.threads[0].thread_id == newer.thread_id
            assert page.total == 2
            assert page.next_cursor is not None
            tail = await app.list_threads(
                project_ids=("project-main",), sort="activity", cursor=page.next_cursor, limit=1
            )
            assert tail.threads[0].thread_id == first.thread_id
            assert tail.next_cursor is None
            assert (await app.list_threads(project_ids=(), sort="activity")).total == 0
            with pytest.raises(ThreadError, match="another query"):
                await app.list_threads(project_ids=("project-main",), sort="updated", cursor=page.next_cursor)

    async with open_harness_ui_app(settings, configuration_path=root) as reopened:
        restored = (await reopened.get_thread(first.thread_id)).thread
        assert restored.excerpt == saved.excerpt
        assert restored.title == "Renamed"
        assert restored.activity_at == saved.activity_at


async def test_conflicting_checkpoint_cannot_overwrite_excerpts(tmp_path: Path, monkeypatch) -> None:
    from a13n_harness_ui.conversation import ConversationExcerpt

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="First question")
        await app.wait_root_operation(first.receipt_id)
        previous = await app._store.threads.get(thread.thread_id)
        assert previous is not None
        second = await app.submit_thread(thread_id=thread.thread_id, prompt="Second question")
        await app.wait_root_operation(second.receipt_id)
        current = await app._store.threads.get(thread.thread_id)
        assert current is not None and current.continuation is not None and current.read_model is not None
        with pytest.raises(StoreConflictError):
            await app._store.threads.select_continuation(
                thread_id=thread.thread_id,
                expected=previous.continuation,
                replacement=current.continuation,
                read_model=current.read_model,
                excerpt=ConversationExcerpt(first_input="Incorrect", latest_input="Stale write"),
            )
        retained = await app._store.threads.get(thread.thread_id)
        assert retained is not None
        assert retained.excerpt == current.excerpt
        assert retained.activity_at == current.activity_at


async def test_focused_watch_resets_when_new_run_saves_during_bootstrap(tmp_path: Path, monkeypatch) -> None:
    from a13n_harness_ui.errors import LivePresentationError

    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        service = app._projections
        query = "detail"
        original = service.detail

        async def complete_before_query(thread_id, **kwargs):
            # No observer existed at cutover. A complete Run now appears in both
            # the selected history and the subscriber's queued live events.
            receipt = await app.submit_thread(thread_id=thread_id, prompt="during bootstrap")
            await app.wait_root_operation(receipt.receipt_id)
            return await original(thread_id=thread_id, **kwargs)

        monkeypatch.setattr(service, query, complete_before_query)
        with pytest.raises(LivePresentationError) as changed:
            async with app.watch_thread(root_thread_id=thread.thread_id):
                pytest.fail("incompatible bootstrap must not be delivered")
        assert changed.value.code == "live_snapshot_changed"
        monkeypatch.setattr(service, query, original)
        async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
            assert watch.snapshot.thread.continuation_id is not None
            assert watch.root_stream is None


async def test_root_input_checkpoint_is_saved_before_model_output_and_advances_at_completion(tmp_path, monkeypatch):
    started = Event()
    release = Event()

    async def model(messages, info):
        started.set()
        await release.wait()
        yield "Checkpointed answer"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "state")
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Save this question now")
        with fail_after(10):
            await started.wait()
        detail = await app.get_thread(thread.thread_id)
        assert detail.continuation_id is not None
        assert detail.thread.excerpt.first_input == "Save this question now"
        assert detail.thread.root_activity.state == "running"
        # The model may enter before the observer publishes the native marker.
        # Retry only that finite cutover race, never substitute the selected head
        # for the original base of the in-flight replay.
        with fail_after(10):
            while True:
                try:
                    async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
                        replay = watch.root_stream
                        if replay is not None:
                            markers = [
                                row["content"]
                                for batch in replay.batches()
                                for item in batch
                                if item.payload is not None and item.payload.get("name") == "a13n.display.snapshot"
                                for row in item.payload["value"]["items"]
                                if row["content"].get("name") == "a13n.harness_ui.checkpoint"
                            ]
                            if markers:
                                assert watch.snapshot.thread.continuation_id == detail.continuation_id
                                assert replay.summary.base_continuation_id is None
                                assert len(markers) == 1
                                payload = markers[0]
                                assert payload is not None
                                value = payload["value"]
                                assert isinstance(value, dict)
                                event = value["event"]
                                assert isinstance(event, dict)
                                assert event["continuation_id"] == detail.continuation_id
                                break
                except LivePresentationError as exc:
                    assert exc.code == "live_snapshot_changed"
                await sleep(0)
        history = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert [
            part.text
            for entry in history.entries
            for part in entry.parts
            if part.kind == "user" and part.metadata.display
        ] == ["Save this question now"]
        # A second reader observes the checkpoint without waiting for the Run.
        async with open_harness_ui_app(settings, configuration_path=root) as reopened:
            saved = await reopened.get_thread_transcript(thread_id=thread.thread_id)
            assert saved == history
        release.set()
        with fail_after(10):
            outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed
        final = await app.get_thread(thread.thread_id)
        assert final.continuation_id != detail.continuation_id
        history = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert [
            part.text
            for entry in history.entries
            for part in entry.parts
            if part.kind == "user" and part.metadata.display
        ] == ["Save this question now"]
        assert any(part.text == "Checkpointed answer" for entry in history.entries for part in entry.parts)
        assert final.thread.excerpt.first_input == "Save this question now"


async def test_consumed_steering_checkpoint_and_terminal_save_use_successive_selected_heads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_started = Event()
    second_started = Event()
    release_first = Event()
    release_second = Event()
    calls = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
            yield "First answer"
        else:
            second_started.set()
            await release_second.wait()
            yield "Steered answer"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        selections = []
        select_continuation = app._store.threads.select_continuation

        async def record_selection(**kwargs):
            result = await select_continuation(**kwargs)
            selections.append((kwargs["expected"], kwargs["replacement"]))
            return result

        monkeypatch.setattr(app._store.threads, "select_continuation", record_selection)
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Initial task")
        try:
            with fail_after(10):
                await first_started.wait()
            initial = await app.get_thread(thread.thread_id)
            assert initial.continuation_id is not None
            steering = await app.steer_root_operation(receipt_id=receipt.receipt_id, message="Focus on correctness")
            assert steering.accepted
            assert steering.enqueue_id is not None
            # Admission is not a durable input journal: only consumed input
            # belongs to a selected complete continuation.
            assert len(selections) == 1
            pending = await app.get_thread_transcript(thread_id=thread.thread_id)
            assert [
                part.text
                for entry in pending.entries
                for part in entry.parts
                if part.kind == "user" and part.metadata.display
            ] == ["Initial task"]

            release_first.set()
            with fail_after(10):
                await second_started.wait()
            consumed = await app.get_thread(thread.thread_id)
            assert consumed.continuation_id != initial.continuation_id
            assert len(selections) == 2
            assert consumed.thread.excerpt.first_input == "Initial task"
            assert consumed.thread.excerpt.latest_input == "Focus on correctness"
            history = await app.get_thread_transcript(thread_id=thread.thread_id)
            assert [
                part.text
                for entry in history.entries
                for part in entry.parts
                if part.kind == "user" and part.metadata.display
            ] == ["Initial task", "Focus on correctness"]
            assert any(part.text == "First answer" for entry in history.entries for part in entry.parts)
            assert not any(part.text == "Steered answer" for entry in history.entries for part in entry.parts)
            assert len(history.turns) == 1
            assert history.turns[0].final_position is None
            assert history.turns[0].steering_count == 1
            release_second.set()
            with fail_after(10):
                outcome = await app.wait_root_operation(receipt.receipt_id)
        finally:
            release_first.set()
            release_second.set()

        assert outcome.status is RootOperationStatus.completed
        assert calls == 2
        assert len(selections) == 3
        assert selections[0][0] is None
        assert selections[1][0] == selections[0][1]
        assert selections[2][0] == selections[1][1]
        final = await app.get_thread(thread.thread_id)
        assert final.continuation_id == selections[2][1].logical_digest
        assert final.continuation_id != consumed.continuation_id
        final_history = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert [
            part.text
            for entry in final_history.entries
            for part in entry.parts
            if part.kind == "user" and part.metadata.display
        ] == ["Initial task", "Focus on correctness"]
        assert any(part.text == "Steered answer" for entry in final_history.entries for part in entry.parts)
        assert len(final_history.turns) == 1
        assert final_history.turns[0].final_position == final_history.entries[-1].position
        directory = await app.get_thread_inputs(thread_id=thread.thread_id)
        assert directory.turns == final_history.turns


@pytest.mark.parametrize("cancel_count", [1, 3])
async def test_checkpoint_cancellation_after_commit_preserves_terminal_head_and_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_count: int
) -> None:
    import asyncio
    from contextvars import ContextVar

    from a13n_harness_ui.root_checkpoint import RootCheckpointCapability, ThreadCheckpointEvent
    from pydantic_ai import RunContext
    from sqlalchemy.ext.asyncio import AsyncSession

    selecting = ContextVar("selecting_continuation", default=False)
    committed = asyncio.Event()
    release = asyncio.Event()
    model_calls = 0
    request_task = None
    markers = []
    commit = AsyncSession.commit
    emit = RunContext.emit
    wrap_model_request = RootCheckpointCapability.wrap_model_request

    async def delayed_commit(self):
        await commit(self)
        if selecting.get() and not committed.is_set():
            # The durable head has changed, but select_continuation has not
            # returned to advance the operation's expected reference yet.
            committed.set()
            await release.wait()

    async def record_emit(self, event):
        result = await emit(self, event)
        if isinstance(event, ThreadCheckpointEvent):
            markers.append(event.continuation_id)
        return result

    async def record_request_task(self, ctx, *, request_context, handler):
        nonlocal request_task
        request_task = asyncio.current_task()
        return await wrap_model_request(self, ctx, request_context=request_context, handler=handler)

    async def model(messages, info):
        nonlocal model_calls
        model_calls += 1
        yield "Model must not be dispatched"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(AsyncSession, "commit", delayed_commit)
    monkeypatch.setattr(RunContext, "emit", record_emit)
    monkeypatch.setattr(RootCheckpointCapability, "wrap_model_request", record_request_task)
    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        selections = []
        select_continuation = app._store.threads.select_continuation

        async def record_selection(**kwargs):
            if selections:
                assert markers == [selections[0][1].logical_digest]
            selections.append((kwargs["expected"], kwargs["replacement"]))
            token = selecting.set(True)
            try:
                return await select_continuation(**kwargs)
            finally:
                selecting.reset(token)

        monkeypatch.setattr(app._store.threads, "select_continuation", record_selection)
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Keep this input after cancellation")
        try:
            with fail_after(10):
                await committed.wait()
                assert request_task is not None
                selected = await app.get_thread(thread.thread_id)
                assert len(selections) == 1
                assert selected.continuation_id == selections[0][1].logical_digest
                assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
                for index in range(cancel_count):
                    if index:
                        # Native cancellation is idempotent at the App boundary;
                        # repeat Task.cancel() to exercise a second interruption
                        # while the first cancellation is joining publication.
                        assert request_task.cancel()
                    while request_task.cancelling() < index + 1:
                        await asyncio.sleep(0)
                    # Cancellation has queued the suspended task. Yield before
                    # releasing commit so it actually receives CancelledError,
                    # rather than merely racing a cancellation request.
                    await asyncio.sleep(0)
                assert not release.is_set()
                assert model_calls == 0
                release.set()
                operation = await app.wait_root_operation(receipt.receipt_id)
        finally:
            release.set()
            with fail_after(10):
                await app.wait_root_operation(receipt.receipt_id)

        assert operation.status is RootOperationStatus.cancelled
        assert operation.failure is None
        assert model_calls == 0
        assert len(selections) == 2
        assert selections[0][0] is None
        assert selections[1][0] == selections[0][1]
        final = await app.get_thread(thread.thread_id)
        assert final.continuation_id == selections[1][1].logical_digest
        assert final.continuation_id != selected.continuation_id
        # App cancellation can stop live consumption before delivery; native
        # marker emission must still complete before terminal saving begins.
        assert markers == [selections[0][1].logical_digest]
        with fail_after(10):
            async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
                assert watch.snapshot.thread.continuation_id == final.continuation_id
                assert watch.root_stream is None
        history = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert [
            part.text
            for entry in history.entries
            for part in entry.parts
            if part.kind == "user" and part.metadata.display
        ] == ["Keep this input after cancellation"]


async def test_summary_preserves_display_history_after_checkpoint_and_app_reopen(tmp_path, monkeypatch):
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "Original answer"
        elif calls == 2:
            yield {
                0: DeltaToolCall(
                    name="summarize", json_args='{"content":"**Keep this decision**"}', tool_call_id="display-summary"
                )
            }
        else:
            yield "Continued answer"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "state")
    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: handoff\n")
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        for prompt in ("Original question", "Summarize now"):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
            with fail_after(10):
                result = await app.wait_root_operation(receipt.receipt_id)
            assert result.status is RootOperationStatus.completed
        history = await app.get_thread_transcript(thread_id=thread.thread_id)
        texts = [part.text for entry in history.entries for part in entry.parts if part.metadata.display and part.text]
        assert texts.count("Original question") == 1
        assert texts.count("Original answer") == 1
        assert texts.count("Summarize now") == 1
        assert texts.count("Continued answer") == 1
        summaries = [
            part
            for entry in history.entries
            for part in entry.parts
            if part.metadata.model_dump().get("a13n.context") == "handoff"
        ]
        assert len(summaries) == 1
        assert "**Keep this decision**" in summaries[0].text
        assert summaries[0].metadata.model_dump().get("operation_id")
        # Pages and entry reads share the independent display positions.
        page = await app.get_thread_transcript(thread_id=thread.thread_id, limit=2)
        assert page.total == history.total
        assert page.next_cursor is not None
        older = await app.get_thread_transcript(thread_id=thread.thread_id, cursor=page.next_cursor, limit=100)
        assert (*older.entries, *page.entries) == history.entries
    async with open_harness_ui_app(settings, configuration_path=root) as reopened:
        assert await reopened.get_thread_transcript(thread_id=thread.thread_id) == history
        receipt = await reopened.submit_thread(thread_id=thread.thread_id, prompt="Continue after reopening")
        with fail_after(10):
            result = await reopened.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.completed
        after = await reopened.get_thread_transcript(thread_id=thread.thread_id)
        assert [part.text for entry in after.entries for part in entry.parts].count("Original answer") == 1


async def test_real_webui_app_resumes_and_restart_does_not_rearm_saved_questions(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "tools:\n  interaction_timeout_seconds: 120\n")
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _DeferredReconstructor()
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="ask")
        await app.wait_root_operation(receipt.receipt_id)
        first = await app.thread_decisions(thread_id=thread.thread_id)
        assert first is not None and first.expires_at is not None and first.server_time is not None
        second = await app.thread_decisions(thread_id=thread.thread_id)
        assert second is not None and second.expires_at == first.expires_at
        pending_wait = app._root_runs._interaction_waits[thread.thread_id]
        with monkeypatch.context() as clock:
            clock.setattr("a13n_harness_ui.root_run.monotonic", lambda: pending_wait.deadline + 1)
            await app._root_runs._expire_interaction(thread.thread_id, pending_wait)
        latest = await app._root_runs.active(thread.thread_id) or await app._root_runs.latest(thread.thread_id)
        assert latest is not None and latest.receipt.receipt_id != receipt.receipt_id
        resumed = await app.wait_root_operation(latest.receipt.receipt_id)
        assert resumed.status is RootOperationStatus.completed
        assert resumed.outcome is not None
        assert "timed out" in str(resumed.outcome.execution.output)
        assert await app.thread_decisions(thread_id=thread.thread_id) is None

        pending_thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=pending_thread.thread_id, prompt="ask")
        await app.wait_root_operation(receipt.receipt_id)
        pending = await app.thread_decisions(thread_id=pending_thread.thread_id)
        assert pending is not None and pending.expires_at is not None
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as reopened:
        retained = await reopened.thread_decisions(thread_id=pending_thread.thread_id)
        assert retained is not None
        assert retained.continuation_id == pending.continuation_id
        assert retained.expires_at is None
        assert await reopened.active_root_operation(pending_thread.thread_id) is None


async def test_input_directory_and_bidirectional_history_use_saved_turn_boundaries(tmp_path, monkeypatch):
    async def model(messages, info):
        yield "Final answer"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "state")
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        for prompt in ("First question", "Second question", "Third question"):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
            outcome = await app.wait_root_operation(receipt.receipt_id)
            assert outcome.status is RootOperationStatus.completed
        index = await app.get_thread_inputs(thread_id=thread.thread_id, limit=2)
        assert [turn.preview for turn in index.turns] == ["First question", "Second question"]
        assert all(turn.final_position is not None for turn in index.turns)
        assert all(turn.final_position == turn.output_position for turn in index.turns)
        assert [turn.output_preview for turn in index.turns] == ["Final answer", "Final answer"]
        assert index.next_cursor is not None
        remaining = await app.get_thread_inputs(thread_id=thread.thread_id, cursor=index.next_cursor)
        assert [turn.preview for turn in remaining.turns] == ["Third question"]
        assert remaining.next_cursor is None
        middle = await app.get_thread_transcript(thread_id=thread.thread_id, turn_id=index.turns[1].turn_id, limit=1)
        assert middle.turns == (index.turns[1],)
        assert middle.entries[0].position == index.turns[1].final_position
        assert middle.boundary_entries[0].position == index.turns[1].input_position
        assert middle.next_cursor and middle.newer_cursor
        earlier = await app.get_thread_transcript(thread_id=thread.thread_id, cursor=middle.next_cursor, limit=1)
        later = await app.get_thread_transcript(thread_id=thread.thread_id, cursor=middle.newer_cursor, limit=1)
        assert earlier.entries[0].position + 1 == middle.entries[0].position
        assert later.entries[0].position == middle.entries[0].position + 1
        assert middle.earlier_turns_cursor and middle.later_turns_cursor
        previous_turn = await app.get_thread_transcript(
            thread_id=thread.thread_id, cursor=middle.earlier_turns_cursor, limit=1
        )
        next_turn = await app.get_thread_transcript(
            thread_id=thread.thread_id, cursor=middle.later_turns_cursor, limit=1
        )
        assert previous_turn.turns == (index.turns[0],)
        assert previous_turn.entries[0].position == index.turns[0].end_position - 1
        assert previous_turn.earlier_turns_cursor is None
        assert next_turn.turns == remaining.turns
        assert next_turn.entries[0].position == remaining.turns[0].input_position
        assert next_turn.later_turns_cursor is None
        assert middle.next_cursor != middle.earlier_turns_cursor
        async with open_harness_ui_app(settings, configuration_path=root) as reopened:
            assert await reopened.get_thread_inputs(thread_id=thread.thread_id, limit=2) == index
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Fourth question")
        await app.wait_root_operation(receipt.receipt_id)
        with pytest.raises(ThreadError, match="another history"):
            await app.get_thread_inputs(thread_id=thread.thread_id, cursor=index.next_cursor)


async def test_oversized_history_is_indexed_once_and_hot_inspections_never_decode_native_state(tmp_path, monkeypatch):
    from a13n_harness_ui.storage import StoredContinuation
    from a13n_harness_ui.storage.inspection import InspectionData
    from a13n_harness_ui.storage.read_models import project_continuation
    from a13n_harness_ui.thread_projection import ThreadProjectionService
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        created = await app.create_thread()
        receipt = await app.submit_thread(thread_id=created.thread_id, prompt="Seed composition")
        await app.wait_root_operation(receipt.receipt_id)
        thread = await app._store.threads.get(created.thread_id)
        assert thread is not None and thread.continuation is not None
        saved = await app._store.objects.read_model(thread.continuation, StoredContinuation)
        # The old 16 MiB cache inserted and immediately evicted this history.
        history = tuple(
            message
            for index in range(80)
            for message in (
                ModelRequest(parts=[UserPromptPart(f"Input {index}")]),
                ModelRequest(
                    parts=[ToolReturnPart(tool_name="view", tool_call_id=f"call-{index}", content="x" * (256 * 1024))]
                ),
                ModelResponse(parts=[TextPart(f"Answer {index}")]),
            )
        )
        value = saved.model_copy(
            update={"harness_state": HarnessState.new(thread_id=thread.thread_id, message_history=history)}
        )
        replacement = (await app._store.objects.publish_model(object_kind=ObjectKind.continuation, value=value)).ref
        selected = await app._store.threads.select_continuation(
            thread_id=thread.thread_id,
            expected=thread.continuation,
            replacement=replacement,
            read_model=project_continuation(value),
        )
        # Directly replacing the head bypasses checkpoint publication of identity-bound work.
        assert await app._store.publish_work(thread.thread_id, replacement, value.harness_state)
        native_reads = 0
        read = app._store.objects.read_model

        async def counted(reference, model):
            nonlocal native_reads
            if model is StoredContinuation:
                native_reads += 1
            return await read(reference, model)

        monkeypatch.setattr(app._store.objects, "read_model", counted)
        page = await app.get_thread_transcript(thread_id=thread.thread_id, limit=5)
        assert page.total == 240 and len(page.entries) == 5
        assert page.entries[-1].parts[0].text == "Answer 79"
        assert page.next_cursor is not None
        earlier = await app.get_thread_transcript(thread_id=thread.thread_id, cursor=page.next_cursor, limit=5)
        assert earlier.entries[-1].position < page.entries[0].position
        inputs = await app.get_thread_inputs(thread_id=thread.thread_id, limit=3)
        assert [turn.preview for turn in inputs.turns] == ["Input 0", "Input 1", "Input 2"]
        await app._terminal_projections.task_page(thread_id=thread.thread_id)
        await app._terminal_projections.note_page(thread_id=thread.thread_id)
        await app.context_usage(thread.thread_id)
        await app.inspect_thread_configuration(thread.thread_id)
        # A fresh projection owner reuses the database, not an in-memory history.
        fresh = ThreadProjectionService(store=app._store, configurations=app._configurations)
        assert (await fresh.transcript(thread_id=thread.thread_id, limit=1)).entries == page.entries[-1:]
        assert native_reads == 1
        assert not app._projections._inspection_loads
        # A delayed rebuild must not replace the index of a newer selected head.
        assert not await app._store.inspections.publish(
            thread.thread_id, thread.continuation.logical_digest, InspectionData("{}", (), ())
        )
        assert await app._store.inspections.header(thread.thread_id, replacement.logical_digest) is not None
        assert selected.continuation == replacement


async def test_history_rebuilds_share_only_their_thread_and_focus_does_not_load_inspectors(tmp_path, monkeypatch):
    from a13n_harness_ui.storage import StoredThreadInitialState

    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        first, second = await app.create_thread(), await app.create_thread()
        first_head = await app._store.threads.get(first.thread_id)
        assert first_head is not None
        entered, release = Event(), Event()
        first_reads = 0
        read = app._store.objects.read_model

        async def blocked(reference, model):
            nonlocal first_reads
            if reference == first_head.initial_state and model is StoredThreadInitialState:
                first_reads += 1
                entered.set()
                await release.wait()
            return await read(reference, model)

        async def unreadable(*args, **kwargs):
            pytest.fail("focused bootstrap must not load lazy inspectors")

        monkeypatch.setattr(app._store.objects, "read_model", blocked)
        monkeypatch.setattr(app._subagent_operator, "query_child_executions", unreadable)
        monkeypatch.setattr(app._terminal_projections, "task_page", unreadable)
        results = []

        async def load_first():
            results.append(await app.get_thread_transcript(thread_id=first.thread_id))

        async with create_task_group() as group:
            group.start_soon(load_first)
            await entered.wait()
            group.start_soon(load_first)
            try:
                with fail_after(2):
                    assert (await app.get_thread_transcript(thread_id=second.thread_id)).total == 0
                    async with app.watch_thread(root_thread_id=first.thread_id) as watch:
                        assert watch.snapshot.thread.thread.thread_id == first.thread_id
            finally:
                release.set()
        assert len(results) == 2 and first_reads == 1
        assert not app._projections._inspection_loads


async def test_old_inspection_counts_rebuild_from_saved_metadata_across_history_pages(tmp_path, monkeypatch):
    from a13n_harness_ui.storage import StoredContinuation, inspection
    from a13n_harness_ui.storage.read_models import project_continuation
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextContent, TextPart, UserPromptPart

    settings = _settings(tmp_path / "state")
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        created = await app.create_thread()
        receipt = await app.submit_thread(thread_id=created.thread_id, prompt="Seed composition")
        await app.wait_root_operation(receipt.receipt_id)
        thread = await app._store.threads.get(created.thread_id)
        assert thread is not None and thread.continuation is not None
        stored = await app._store.objects.read_model(thread.continuation, StoredContinuation)
        history = [ModelRequest(parts=[UserPromptPart("Question")])]
        for source in ("background_process", "async_subagent", "external"):
            history.append(
                ModelRequest(
                    parts=[UserPromptPart([TextContent(source, metadata={"a13n.steering-source": source})])],
                    metadata={"a13n.steering-run": "run-one", "a13n.steering-source": source},
                )
            )
        history.append(ModelResponse(parts=[TextPart("Answer")]))
        value = stored.model_copy(
            update={"harness_state": HarnessState.new(thread_id=thread.thread_id, message_history=history)}
        )
        replacement = (await app._store.objects.publish_model(object_kind=ObjectKind.continuation, value=value)).ref
        await app._store.threads.select_continuation(
            thread_id=thread.thread_id,
            expected=thread.continuation,
            replacement=replacement,
            read_model=project_continuation(value),
        )
        from a13n_harness_ui import display_projection

        project = display_projection.display_turns

        def legacy_counts(display):
            return tuple(turn.model_copy(update={"steering_count": 3}) for turn in project(display))

        with monkeypatch.context() as old:
            old.setattr(inspection, "INSPECTION_VERSION", 1)
            old.setattr(display_projection, "display_turns", legacy_counts)
            page = await app.get_thread_transcript(thread_id=thread.thread_id, limit=1)
            assert page.turns[0].steering_count == 3
        assert await app._store.inspections.header(thread.thread_id, replacement.logical_digest) is None

    async with open_harness_ui_app(settings, configuration_path=root) as reopened:
        page = await reopened.get_thread_transcript(thread_id=thread.thread_id, limit=1)
        assert page.turns[0].steering_count == 1
        assert page.entries[0].parts[0].text == "Answer"
        assert page.next_cursor is not None
        earlier = await reopened.get_thread_transcript(thread_id=thread.thread_id, cursor=page.next_cursor, limit=1)
        assert earlier.turns == page.turns
        directory = await reopened.get_thread_inputs(thread_id=thread.thread_id)
        assert directory.turns == page.turns
        assert await reopened._store.inspections.header(thread.thread_id, replacement.logical_digest) is not None


async def test_preparation_failure_after_handoff_saves_intact_display_and_reopens(tmp_path, monkeypatch):
    from pydantic_ai.capabilities import AbstractCapability

    fail_instructions = False
    calls = 0

    class Instructions(AbstractCapability):
        def get_instructions(self):
            async def instructions(ctx):
                if fail_instructions:
                    raise RuntimeError("instruction backend unavailable")
                return "Continue the task."

            return instructions

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {0: DeltaToolCall(name="summarize", json_args='{"content":"Keep this decision"}', tool_call_id="s")}
        else:
            yield "Saved answer"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    settings = _settings(tmp_path / "state")
    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: handoff\n")
    reconstruct = AgentReconstructor.reconstruct

    def with_instructions(self, composition, *, root_capabilities=(), **kwargs):
        return reconstruct(self, composition, root_capabilities=(*root_capabilities, Instructions()), **kwargs)

    monkeypatch.setattr(AgentReconstructor, "reconstruct", with_instructions)
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Original input")
        with fail_after(10):
            completed = await app.wait_root_operation(receipt.receipt_id)
        assert completed.status is RootOperationStatus.completed
        before = await app.get_thread(thread.thread_id)
        before_history = await app.get_thread_transcript(thread_id=thread.thread_id)
        before_turns = await app.get_thread_inputs(thread_id=thread.thread_id)
        assert before_turns.turns[0].final_position is not None

        fail_instructions = True
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="New input")
        with fail_after(10):
            failed = await app.wait_root_operation(receipt.receipt_id)
        assert failed.status is RootOperationStatus.failed
        assert failed.outcome is not None and failed.outcome.continuation.status == "selected"
        selected = await app.get_thread(thread.thread_id)
        assert selected.continuation_id != before.continuation_id
        history = await app.get_thread_transcript(thread_id=thread.thread_id)

        # Output targets pin the newly selected continuation; saved content does not change.
        def saved_content(entry):
            return entry.model_dump(exclude={"parts": {"__all__": {"comment_target"}}})

        assert [saved_content(entry) for entry in history.entries[: len(before_history.entries)]] == [
            saved_content(entry) for entry in before_history.entries
        ]
        texts = [part.text for entry in history.entries for part in entry.parts if part.metadata.display and part.text]
        assert texts.count("Saved answer") == 1
        assert texts.count("New input") == 1
        turns = await app.get_thread_inputs(thread_id=thread.thread_id)
        assert turns.turns[0].final_position == before_turns.turns[0].final_position
        assert turns.turns[-1].final_position is None
        assert calls == 2

    async with open_harness_ui_app(settings, configuration_path=root) as reopened:
        assert (await reopened.get_thread(thread.thread_id)).continuation_id == selected.continuation_id
        assert await reopened.get_thread_transcript(thread_id=thread.thread_id) == history


async def test_reopen_recovers_accepted_external_fact_without_replaying_approval(tmp_path: Path) -> None:
    from a13n_harness_ui.surfaces import ApprovalDecision
    from pydantic_ai import Tool

    root = _write_configuration(tmp_path)
    started = Event()
    effects = []
    observed = []

    async def change() -> str:
        effects.append("changed")
        started.set()
        await sleep_forever()
        return "unreachable"

    async def model(messages, info):
        returns = [
            part
            for message in messages
            if message.kind == "request"
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(name="change", json_args="{}", tool_call_id="call_local"),
                1: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="call_external"),
            }
        else:
            observed.extend(returns)
            yield "recovered"

    class Reconstructor:
        def reconstruct(self, composition, *, root_capabilities=(), **kwargs):
            return _reconstructed(
                model,
                (
                    *root_capabilities,
                    Capability(
                        id="mixed",
                        tools=[Tool(change, requires_approval=True)],
                        toolsets=[
                            ExternalToolset(
                                [ToolDefinition(name="lookup", parameters_json_schema={"type": "object"})],
                                id="external",
                            ),
                        ],
                    ),
                ),
            )

    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        app._root_runs._executor._agents = Reconstructor()
        thread = await app.create_thread()
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="go")
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.suspended
        detail = await app.get_thread(thread.thread_id)
        assert detail.continuation_id is not None
        receipt = await app.respond_thread(
            thread_id=thread.thread_id,
            response=ThreadDeferredResponse(
                expected_continuation_id=detail.continuation_id,
                responses=(
                    ApprovalDecision(request_id="call_local", approved=True),
                    ExternalToolResult(request_id="call_external", result={"fact": "accepted once"}),
                ),
            ),
        )
        with fail_after(5):
            await started.wait()
        await app.cancel_root_operation(receipt.receipt_id)
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.cancelled
    assert effects == ["changed"]

    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        app._root_runs._executor._agents = Reconstructor()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="continue")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
    assert effects == ["changed"]
    external = [part for part in observed if part.tool_call_id == "call_external"]
    assert len(external) == 1 and external[0].content == {"fact": "accepted once"}
    local = [part for part in observed if part.tool_call_id == "call_local"]
    assert len(local) == 1 and local[0].outcome == "failed"
