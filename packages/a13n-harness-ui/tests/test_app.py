from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_environment import (
    CommandRequest,
    EnvironmentOutputPolicy,
    LocalEnvdEnvironment,
    LocalEnvdProviderRuntime,
    ShellCommand,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_harness import (
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    AgentSpec,
    HarnessBuilder,
    HarnessRunResultEvent,
    HarnessRunStream,
)
from a13n_harness.capabilities import SkillsCapability, SubagentCancelResult, SubagentSteerResult, WebCapability
from a13n_harness.environment import EnvironmentAction, EnvironmentError
from a13n_harness.model_auth import GrokCredentials
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
from a13n_harness_ui.errors import AppStateError, StoreConflictError, ThreadError
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
from anyio import Event, create_task_group, fail_after, sleep, sleep_forever
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

    root = _write_configuration(tmp_path)
    calls = 0
    original = app_module.load_harness_ui_configuration

    async def counted(path: Path, *, content_plugin_root: Path | None = None):
        nonlocal calls
        calls += 1
        return await original(path, content_plugin_root=content_plugin_root)

    monkeypatch.setattr(app_module, "load_harness_ui_configuration", counted)
    async with open_harness_ui_app(
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

    async with open_harness_ui_app(_settings(tmp_path / "state")) as app:

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

        async def local_envd_runtime(_provider: object) -> LocalEnvdProviderRuntime:
            return LocalEnvdProviderRuntime(
                executable=(tmp_path / "a13n-envd").resolve(),
                allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
            )

        monkeypatch.setattr(reconstructor, "_runtime_collaborator", local_envd_runtime)
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
        project_root = Path(published.value.project_roots[0]).as_posix()

        assert tuple(plan.environments) == ("workspace", "user-skills", "configuration", "thread-files")
        assert isinstance(plan.environments["workspace"], LocalEnvdEnvironment)
        assert tuple(item.mount_path for item in plan._mounts) == (
            project_root,
            user_skills.resolve().as_posix(),
            root.parent.resolve().as_posix(),
            (tmp_path / "state/threads" / published.value.thread_id).as_posix(),
        )
        local_envd = plan.environments["workspace"]
        assert isinstance(local_envd, LocalEnvdEnvironment)
        assert prepared == [local_envd, plan.environments["thread-files"]]
        assert local_envd._configuration.workspace.path.as_posix() == project_root
        assert local_envd._configuration.execution_network.value == "deny"
        if os.name == "posix":
            assert local_envd._configuration.shell_profiles[0].fixed_arguments == ("-c",)
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

        expected_aliases = ("workspace", "content-plugin-1") + (("user-skills",) if skills_enabled else ())
        assert tuple(plan.environments) == (*expected_aliases, "configuration", "thread-files")
        assert tuple(item.mount_path for item in plan._mounts) == (
            (tmp_path / "workspace").resolve().as_posix(),
            plugin_skills.parent.resolve().as_posix(),
        ) + ((user_skills.resolve().as_posix(),) if skills_enabled else ()) + (
            root.parent.resolve().as_posix(),
            (tmp_path / "state/threads" / composition.thread_id).as_posix(),
        )
        plugin_mount = plan._mounts[1]
        assert plugin_mount.permission_ceiling.operations == frozenset(
            action for action in EnvironmentAction if action.value.startswith("environment.file.")
        )
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


async def test_environment_run_service_adds_only_the_dedicated_user_skill_mount(tmp_path: Path) -> None:
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
            agent_source_kind=stored.configuration.agent_source.kind,
            agent_source_id=stored.configuration.agent_source.id,
            environment_profile_id=stored.configuration.environment_profile_id,
            harness_plugin_ids=stored.configuration.harness_plugin_ids,
            environment_run_extension_ids=stored.configuration.environment_run_extension_ids,
            mcp_server_ids=stored.configuration.mcp_server_ids,
        )
        published = await executor._compositions.publish(source, selection)
        plan = await executor._environments.prepare(published.value)

        assert tuple(plan.environments) == ("workspace", "user-skills", "configuration", "thread-files")
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
                    while (await watch.events.receive()).event_type != "TEXT_MESSAGE_CONTENT":
                        pass
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
        assert failed.outcome.execution.status == "completed"
        assert failed.outcome.continuation.status == "failed"
        retained = await app.get_thread(thread.thread_id)
        assert retained.continuation_id == prior
        assert retained.thread.excerpt.first_input == "first"
        assert retained.thread.excerpt.latest_input == "first"


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


async def _candidate_error(path: Path):
    from a13n_harness_ui.errors import ConfigurationError

    try:
        await load_harness_ui_configuration(path)
    except ConfigurationError as exc:
        return exc
    raise AssertionError("candidate unexpectedly valid")


class _CompletedReconstructor:
    def reconstruct(
        self, composition, *, subagent_operator, root_capabilities=(), subscription_sources=None, pricing_catalog=None
    ):
        del composition, subagent_operator, subscription_sources

        async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            del messages, info
            yield "root complete"

        return _reconstructed(model, root_capabilities)


class _DeferredReconstructor:
    def reconstruct(
        self, composition, *, subagent_operator, root_capabilities=(), subscription_sources=None, pricing_catalog=None
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
        self, composition, *, subagent_operator, root_capabilities=(), subscription_sources=None, pricing_catalog=None
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
            projections=app._projections,
            root_runs=app._root_runs,
            create_thread=app.create_thread,
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
            projections=app._projections,
            root_runs=app._root_runs,
            create_thread=app.create_thread,
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
        assert current is not None and current.continuation is not None
        with pytest.raises(StoreConflictError):
            await app._store.threads.select_continuation(
                thread_id=thread.thread_id,
                expected=previous.continuation,
                replacement=current.continuation,
                excerpt=ConversationExcerpt(first_input="Incorrect", latest_input="Stale write"),
            )
        retained = await app._store.threads.get(thread.thread_id)
        assert retained is not None
        assert retained.excerpt == current.excerpt
        assert retained.activity_at == current.activity_at
