from __future__ import annotations

from pathlib import Path

import pytest
from a13n_harness import AgentIdentityRef, AgentInstanceContext
from a13n_harness.capabilities.skills import SkillsCapability
from a13n_harness.environment import EnvironmentAction, EnvironmentError
from a13n_harness.providers.environment.commands import CommandRequest, ShellCommand
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import AgentReconstructor, ThreadCompositionSelection
from a13n_harness_ui.environment_paths import BUILTIN_SKILLS_PATH, BUILTIN_SKILLS_SOURCE_ID
from a13n_harness_ui.extensions.environment_adapters import NativeProjectAdapter
from a13n_harness_ui.surfaces import NewThreadDefaults

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio
SKILL_NAME = "harness-ui-configuration"


@pytest.mark.parametrize("with_project", [False, True])
@pytest.mark.parametrize("canonical_paths", [False, True])
@pytest.mark.parametrize("skills_enabled", [False, True])
async def test_builtin_skill_discovery_and_read_only_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_project: bool,
    canonical_paths: bool,
    skills_enabled: bool,
) -> None:
    # Exercise virtual-layout composition with the real local file provider, without a remote service.
    monkeypatch.setattr(NativeProjectAdapter, "preserves_host_paths", property(lambda self: canonical_paths))
    path = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    if skills_enabled:
        agent.write_text(agent.read_text() + "capabilities:\n  - capability: skills\n")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        defaults = NewThreadDefaults(project_id="project-main" if with_project else None)
        draft = await app.skill_catalog(defaults=defaults)
        assert any(item.name == SKILL_NAME for item in draft.items) is skills_enabled
        thread = await app.create_thread(defaults=defaults)
        preview = await app.skill_catalog(thread_id=thread.thread_id)
        executor = app._root_runs._executor
        composition = (
            await executor._compositions.publish(
                await app.current_configuration(),
                ThreadCompositionSelection(
                    thread_id=thread.thread_id,
                    version=1,
                    project_id=thread.configuration.project_id,
                    agent_source_kind="agent",
                    agent_source_id="agent-assistant",
                    environment_profile_id="environment-native",
                    harness_plugin_ids=(),
                    environment_run_extension_ids=(),
                    mcp_server_ids=(),
                ),
            )
        ).value
        reconstructed = AgentReconstructor().reconstruct(composition, subagent_operator=None)
        plan = await executor._environments.prepare(composition)
        try:
            assert ("builtin-skills" in plan.environments) is skills_enabled
            assert plan.default_environment == ("workspace" if with_project else "thread-files")
            async with plan.runtime.bind(
                thread_id=thread.thread_id,
                run_id="run-builtin-skills",
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="test", subject="builtin-skills"),
                    agent_instance_id="agent-test",
                ),
                host_refs={},
            ) as environment:
                if not skills_enabled:
                    assert not preview.items
                    return
                skills = next(
                    item
                    for item in reconstructed.executable.definition.capabilities
                    if isinstance(item, SkillsCapability)
                )
                catalog = await skills.manager.scan(files=environment.files)
                selected = next(item for item in catalog if item.name == SKILL_NAME)
                item = next(item for item in preview.items if item.name == SKILL_NAME)
                assert item.source_id == selected.source_id == BUILTIN_SKILLS_SOURCE_ID
                assert item.logical_path == f"{BUILTIN_SKILLS_PATH}/{SKILL_NAME}"
                skill_path = f"{item.logical_path}/SKILL.md"
                skill = await environment.files.read_text(skill_path)
                assert "## Documentation map" in skill.text
                index = await environment.files.read_text(f"{item.logical_path}/references/navigation.md")
                assert "lines " in index.text
                assert (
                    "# Configuration reference"
                    in (await environment.files.read_text(f"{item.logical_path}/docs/configuration.md")).text
                )
                mount = next(mount for mount in plan._mounts if mount.alias == "builtin-skills")
                assert EnvironmentAction.FILE_READ_TEXT in mount.permission_ceiling.operations
                assert EnvironmentAction.FILE_WRITE_TEXT not in mount.permission_ceiling.operations
                assert EnvironmentAction.SHELL_EXEC not in mount.permission_ceiling.operations
                with pytest.raises(EnvironmentError):
                    await environment.files.write_text(skill_path, "overwrite", mode="overwrite")
                with pytest.raises(EnvironmentError):
                    await environment.shell.exec_captured(
                        CommandRequest(
                            command=ShellCommand(profile_id="default", script="echo forbidden"),
                            cwd=item.logical_path,
                            output_policy=EnvironmentOutputPolicy(
                                max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"
                            ),
                        )
                    )
                assert (await environment.files.read_text(skill_path)).text == skill.text
        finally:
            finalization = await plan.finalize()
            assert not finalization.cleanup_errors
        assert not (tmp_path / "workspace/.agents").exists()
        assert not (tmp_path / "builtin-skills").exists()


@pytest.mark.parametrize("override_source", ["user", "project"])
async def test_user_and_project_skills_override_builtin_in_preview_and_runtime(
    tmp_path: Path, override_source: str
) -> None:
    path = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: skills\n")
    root = Path.home() if override_source == "user" else tmp_path / "workspace"
    document = root / ".agents/skills/custom/SKILL.md"
    document.parent.mkdir(parents=True)
    document.write_text(f"---\nname: {SKILL_NAME}\ndescription: My override.\n---\n\nCustom instructions.\n")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        item = next(item for item in (await app.skill_catalog()).items if item.name == SKILL_NAME)
        expected_source = f"a13n-harness-ui:{'user-skills' if override_source == 'user' else 'project:workspace'}"
        assert item.source_id == expected_source
        thread = await app.create_thread()
        executor = app._root_runs._executor
        source = await app.current_configuration()
        composition = (
            await executor._compositions.publish(
                source,
                ThreadCompositionSelection(
                    thread_id=thread.thread_id,
                    version=1,
                    project_id="project-main",
                    agent_source_kind="agent",
                    agent_source_id="agent-assistant",
                    environment_profile_id="environment-native",
                    harness_plugin_ids=(),
                    environment_run_extension_ids=(),
                    mcp_server_ids=(),
                ),
            )
        ).value
        skills = next(
            item
            for item in AgentReconstructor()
            .reconstruct(composition, subagent_operator=None)
            .executable.definition.capabilities
            if isinstance(item, SkillsCapability)
        )
        plan = await executor._environments.prepare(composition)
        try:
            async with plan.runtime.bind(
                thread_id=thread.thread_id,
                run_id="run-override",
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="test", subject="override"), agent_instance_id="agent-test"
                ),
                host_refs={},
            ) as environment:
                catalog = await skills.manager.scan(files=environment.files)
                assert next(item for item in catalog if item.name == SKILL_NAME).source_id == expected_source
        finally:
            assert not (await plan.finalize()).cleanup_errors


async def test_root_run_reads_builtin_skill_navigation_and_documentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    path = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: skills\n")
    agent.write_text(
        agent.read_text() + "  - capability: dynamic_environment\n    configuration: {files_enabled: true}\n"
    )
    documents = ("SKILL.md", "references/navigation.md", "docs/configuration.md")
    observed: list[str] = []

    async def model(messages, info):
        returns = [
            part
            for message in messages
            if message.kind == "request"
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        step = len(returns)
        if step == 0:
            assert f"{BUILTIN_SKILLS_PATH}/{SKILL_NAME}" in str(messages) + str(info)
            assert any(tool.name == "view" for tool in info.function_tools)
        else:
            content = str(returns[-1].content)
            expected = ("Documentation map", "Documentation navigation", "Configuration reference")[step - 1]
            assert expected in content
            observed.append(documents[step - 1])
        if step < len(documents):
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": f"{BUILTIN_SKILLS_PATH}/{SKILL_NAME}/{documents[step]}"}),
                    tool_call_id=f"read-{step}",
                )
            }
        else:
            yield "Read the bundled configuration reference without changing any configuration."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread(defaults=NewThreadDefaults(project_id=None))
        receipt = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt="Use harness-ui-configuration to inspect the configuration documentation. Do not edit files.",
        )
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed
        assert operation.outcome.execution.output.startswith("Read the bundled")
    assert observed == list(documents)
