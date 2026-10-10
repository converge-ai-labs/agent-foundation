import sys
from pathlib import Path

import pytest
from a13n_harness import AgentIdentityRef, AgentInstanceContext
from a13n_harness.capabilities import SkillsCapability
from a13n_harness.environment import FILE_ACTIONS, EnvironmentError
from a13n_harness.providers.environment.commands import CommandRequest, ShellCommand
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import AgentReconstructor, ThreadCompositionSelection
from a13n_harness_ui.configuration.setup import SetupSelection
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.storage import ThreadConfigurationMutation, ThreadConfigurationPatch
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus

from .test_app import _CompletedReconstructor, _settings, _write_configuration

pytestmark = pytest.mark.anyio


def _projectless_configuration(root: Path) -> Path:
    path = _write_configuration(root)
    path.write_text(path.read_text().replace("  project: project-main\n", ""))
    agent = root / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: skills\n")
    return path


async def test_projectless_thread_executes_reopens_and_can_select_or_clear_project(tmp_path: Path) -> None:
    path = _projectless_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        assert not (await app.setup_status()).needed
        thread = await app.create_thread()
        assert thread.configuration.project_id is None
        assert (await app.skill_catalog(thread_id=thread.thread_id)).context_kind == "idle"
        rows = await app.thread_activity(project_id=None)
        assert rows.rows[0].project_name == "No project"
        assert await app._store.threads.project_recency() == {}
        app._root_runs._executor._agents = _CompletedReconstructor()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Work without a project")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        continuation = (await app.get_thread(thread.thread_id)).continuation_id
        assert continuation is not None

    async with open_harness_ui_app(settings, configuration_path=path) as app:
        restored = await app.get_thread(thread.thread_id)
        assert restored.thread.configuration.project_id is None
        assert restored.continuation_id == continuation
        app._root_runs._executor._agents = _CompletedReconstructor()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Continue")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        changed = await app.update_thread_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutation(
                expected_version=1, patch=ThreadConfigurationPatch(project_id="project-main")
            ),
        )
        assert changed.configuration.project_id == "project-main"
        assert ThreadConfigurationPatch().apply(changed.configuration).project_id == "project-main"
        cleared = await app.update_thread_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutation(expected_version=2, patch=ThreadConfigurationPatch(project_id=None)),
        )
        assert cleared.configuration.project_id is None
        assert cleared.configuration.version == 3
        with pytest.raises(ThreadError, match="unavailable"):
            await app.create_thread(defaults=NewThreadDefaults(project_id="missing"))


@pytest.mark.parametrize("with_project", [False, True])
async def test_scratch_cwd_and_selected_configuration_file_mount(tmp_path: Path, with_project: bool) -> None:
    path = _projectless_configuration(tmp_path)
    user_skills = tmp_path / "user-skills"
    skill = user_skills / "global/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: global\ndescription: Global test skill\n---\nRead this skill.\n")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main" if with_project else None)
        )
        source = await app.current_configuration()
        executor = app._root_runs._executor
        executor._environments._user_skills_root = user_skills
        composition = (
            await executor._compositions.publish(
                source,
                ThreadCompositionSelection(
                    thread_id=thread.thread_id,
                    version=1,
                    project_id=thread.configuration.project_id,
                    local_roots=thread.configuration.local_roots,
                    agent_source_kind="agent",
                    agent_source_id="agent-assistant",
                    environment_profile_id="environment-native",
                    harness_plugin_ids=(),
                    environment_run_extension_ids=(),
                    mcp_server_ids=(),
                ),
            )
        ).value
        assert bool(composition.project_roots) is with_project
        reconstructed = AgentReconstructor(user_skills_root=user_skills).reconstruct(
            composition, subagent_operator=None
        )
        skills = next(
            item for item in reconstructed.executable.definition.capabilities if isinstance(item, SkillsCapability)
        )
        assert user_skills.as_posix() in skills.manager.roots
        if not with_project:
            assert not any(".agents/skills" in root for root in skills.manager.roots)
        plan = await executor._environments.prepare(composition)
        try:
            assert plan.default_environment == ("workspace" if with_project else "thread-files")
            assert ("workspace" in plan.environments) is with_project
            config_mount = next(mount for mount in plan._mounts if mount.alias == "configuration")
            assert config_mount.permission_ceiling.operations == FILE_ACTIONS
            async with plan.runtime.bind(
                thread_id=thread.thread_id,
                run_id="run-projectless",
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="test", subject="projectless"), agent_instance_id="agent-test"
                ),
                host_refs={},
            ) as environment:
                scratch = tmp_path / "state/threads" / thread.thread_id / "tmp"
                target = "note.txt" if not with_project else (scratch / "note.txt").as_posix()
                await environment.files.write_text(target, "scratch", mode="create")
                assert (scratch / "note.txt").read_text() == "scratch"
                await environment.files.write_text(
                    (path.parent / "AGENTS.md").as_posix(), "Global guidance", mode="create"
                )
                with pytest.raises(EnvironmentError):
                    await environment.shell.exec_captured(
                        CommandRequest(
                            command=ShellCommand(profile_id="default", script="echo forbidden"),
                            cwd=path.parent.as_posix(),
                            output_policy=EnvironmentOutputPolicy(
                                max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"
                            ),
                        )
                    )
                if not with_project:
                    result = await environment.shell.exec_captured(
                        CommandRequest(
                            command=ShellCommand(
                                profile_id="default", script="(Get-Location).Path" if sys.platform == "win32" else "pwd"
                            ),
                            output_policy=EnvironmentOutputPolicy(
                                max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"
                            ),
                        )
                    )
                    assert result.status.exit_code == 0
                    assert result.output.stdout.inline is not None
                    assert Path(result.output.stdout.inline.decode().strip()).resolve() == scratch.resolve()
        finally:
            finalized = await plan.finalize()
            assert not finalized.cleanup_errors
        await app.reload_configuration()
        assert (await app.current_configuration()).global_guidance[0].endswith("\n\nGlobal guidance")
        assert composition.root.global_guidance == ()


async def test_setup_needs_no_project_and_does_not_publish_one(tmp_path: Path) -> None:
    path = tmp_path / "custom/config.yaml"
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        selection = SetupSelection(environment_profile="environment-native")
        preview = await app.preview_setup(selection)
        assert not any(name.startswith("projects/") for name in preview.files)
        assert "project:" not in preview.files[path.name]
        assert (await app.apply_setup(selection)).completed
        assert not (await app.setup_status()).needed
        assert not (await app.current_configuration()).projects
        assert (await app.create_thread()).configuration.project_id is None


async def test_projectless_migration_preserves_existing_threads_and_refuses_lossy_downgrade(
    tmp_path: Path, before_comment_retirement
) -> None:
    from a13n_harness_ui.storage.migration import DatabaseMigrator
    from alembic import command
    from sqlalchemy import create_engine, inspect, text

    path = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        thread = await app.create_thread(title="Preserved", defaults=NewThreadDefaults(local_roots=()))
        app._root_runs._executor._agents = _CompletedReconstructor()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Retain my history")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        before = await app.get_thread(thread.thread_id)
    database = tmp_path / "state/metadata.sqlite3"
    migrator = DatabaseMigrator(database)
    migrator._run(lambda config: command.downgrade(config, "ecdbb45e3c57"), write=True)
    migrator.upgrade()
    migrator.upgrade()
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        after = await app.get_thread(thread.thread_id)
        assert after.thread.configuration == before.thread.configuration
        assert after.thread.title == "Preserved"
        assert after.continuation_id == before.continuation_id
        await app.update_thread_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutation(expected_version=1, patch=ThreadConfigurationPatch(project_id=None)),
        )
    with pytest.raises(RuntimeError, match="without a Project"):
        migrator._run(lambda config: command.downgrade(config, "ecdbb45e3c57"), write=True)
    migrator.verify_current()
    engine = create_engine(f"sqlite:///{database}")
    try:
        assert next(
            column for column in inspect(engine).get_columns("thread_configuration") if column["name"] == "project_id"
        )["nullable"]
        with engine.connect() as connection:
            assert not connection.execute(text("PRAGMA foreign_key_check")).all()
            assert connection.execute(text("SELECT count(*) FROM thread")).scalar_one() == 1
    finally:
        engine.dispose()


async def test_projectless_sandbox_setup_requires_preflight_and_execution_does_not_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness.providers.environment.local_envd.provider import LocalEnvdEnvironment
    from a13n_harness.providers.environment.local_envd.runtime import (
        LocalEnvdProviderRuntime,
        TemporaryLocalEnvdRuntimeAllocator,
    )
    from a13n_harness_ui.errors import AppStateError
    from a13n_harness_ui.model_authoring import ModelRecipeRequest, prepare_model

    path = tmp_path / "config.yaml"
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        selection = SetupSelection(
            model=prepare_model(ModelRecipeRequest(connection="codex", model_id="gpt-5.6-sol")),
            default_agent="agent-codex",
            environment_profile="environment-sandbox",
        )
        preview = await app.preview_setup(selection)
        assert preview.project_paths == (str(app._store.layout.staging),)
        with pytest.raises(AppStateError, match="preflight"):
            await app.apply_setup(selection)
        assert not path.exists()
        # The setup gate records a successful probe; runtime entry must still prepare independently.
        app._sandbox_ready_paths.add(app._store.layout.staging)
        assert (await app.apply_setup(selection)).completed
        thread = await app.create_thread()
        assert thread.configuration.project_id is None
        executor = app._root_runs._executor
        composition = (
            await executor._compositions.publish(
                await app.current_configuration(),
                ThreadCompositionSelection(
                    thread_id=thread.thread_id,
                    version=1,
                    project_id=None,
                    agent_source_kind="agent",
                    agent_source_id="agent-codex",
                    environment_profile_id="environment-sandbox",
                    harness_plugin_ids=(),
                    environment_run_extension_ids=(),
                    mcp_server_ids=(),
                ),
            )
        ).value

        async def runtime(_roots, **kwargs):
            return LocalEnvdProviderRuntime(
                executable=tmp_path / "envd",
                allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
            )

        prepared = []

        async def unavailable(environment, **scope):
            prepared.append(Path(environment._configuration.working_directory))
            raise RuntimeError("Sandbox unavailable")

        monkeypatch.setattr(executor._environments._reconstructor, "sandbox_runtime", runtime)
        monkeypatch.setattr(LocalEnvdEnvironment, "_prepare", unavailable)
        with pytest.raises(RuntimeError, match="Sandbox unavailable"):
            await executor._environments.prepare(composition)
        assert prepared == [tmp_path / "state/threads" / thread.thread_id]


async def test_explicit_null_project_overrides_default_and_collaboration_inherits_it(tmp_path: Path) -> None:
    from a13n_harness_ui.thread_capability import ThreadToolController

    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        inherited = await app.create_thread(defaults=NewThreadDefaults(agent_id="agent-assistant"))
        assert inherited.configuration.project_id == "project-main"
        projectless = await app.create_thread(defaults=NewThreadDefaults(project_id=None))
        assert projectless.configuration.project_id is None
        await app.skill_catalog(defaults=NewThreadDefaults(project_id=None))
        controller = ThreadToolController(
            threads=app._store.threads,
            projections=app._projections,
            root_runs=app._root_runs,
            create_thread=app.create_thread,
            configurations=app._configurations,
        )
        app._root_runs._executor._agents = _CompletedReconstructor()
        result = await controller.create_thread(
            source_thread_id=projectless.thread_id, prompt="Work separately", title=None, agent_id=None
        )
        assert result["ok"]
        created = await app.get_thread(result["thread_id"])
        assert created.thread.configuration.project_id is None
        receipt = result["receipt"]
        assert (await app.wait_root_operation(receipt["receipt_id"])).status is RootOperationStatus.completed
