from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

import pytest
from a13n_harness import AgentIdentityRef, AgentInstanceContext
from a13n_harness.capabilities.skills import SkillsCapability
from a13n_harness.environment import EnvironmentAction, EnvironmentError
from a13n_harness.environment.commands import CommandRequest, ShellCommand
from a13n_harness.environment.retention import EnvironmentOutputPolicy
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
                    local_roots=thread.configuration.local_roots,
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
                    "title: Configuration reference"
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
            finalization = plan.finalization
            assert not finalization.cleanup_errors
        assert not (tmp_path / "workspace/.agents").exists()
        assert not (tmp_path / "builtin-skills").exists()


@pytest.mark.parametrize("override_source", ["user", "project"])
@pytest.mark.parametrize("skill_name", [SKILL_NAME, "harness-ui-operations"])
async def test_user_and_project_skills_override_builtin_in_preview_and_runtime(
    tmp_path: Path, override_source: str, caplog: pytest.LogCaptureFixture, skill_name: str
) -> None:
    path = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: skills\n")
    root = Path.home() if override_source == "user" else tmp_path / "workspace"
    document = root / ".agents/skills/custom/SKILL.md"
    document.parent.mkdir(parents=True)
    document.write_text(f"---\nname: {skill_name}\ndescription: My override.\n---\n\nCustom instructions.\n")
    invalid = root / ".agents/skills/invalid/SKILL.md"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("Incomplete frontmatter")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        preview = await app.skill_catalog()
        item = next(item for item in preview.items if item.name == skill_name)
        assert all(entry.logical_path != str(invalid.parent) for entry in preview.items)
        warning = next(record for record in caplog.records if record.message == "skill_catalog_entry_skipped")
        assert warning.levelname == "WARNING"
        assert warning.path == str(invalid.parent)
        assert warning.code == "skill_catalog_invalid"
        expected_source = f"a13n-harness-ui:{'user-skills' if override_source == 'user' else 'project:workspace'}"
        assert item.source_id == warning.source_id == expected_source
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
                    local_roots=thread.configuration.local_roots,
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
                assert next(item for item in catalog if item.name == skill_name).source_id == expected_source
        finally:
            assert not (plan.finalization).cleanup_errors


@pytest.mark.parametrize("linked_component", ["skills", "agents"])
async def test_linked_project_skills_preserve_paths_in_preview_and_root_run(tmp_path, monkeypatch, linked_component):
    import json

    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    path = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(
        agent.read_text()
        + "capabilities:\n  - capability: skills\n"
        + "  - capability: dynamic_environment\n    configuration: {files_enabled: true}\n"
    )
    workspace = tmp_path / "workspace"
    target = workspace / "shared"
    skill_root = target / "skills" if linked_component == "agents" else target
    document = skill_root / "review/SKILL.md"
    document.parent.mkdir(parents=True)
    document.write_text("---\nname: review\ndescription: Review linked code.\n---\n\nLinked review instructions.\n")
    if linked_component == "agents":
        (workspace / ".agents").symlink_to(target, target_is_directory=True)
    else:
        (workspace / ".agents").mkdir()
        (workspace / ".agents/skills").symlink_to(target, target_is_directory=True)
    expected = str(workspace / ".agents/skills/review")
    observed = []

    async def model(messages, info):
        returns = [
            part
            for message in messages
            if message.kind == "request"
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            assert "Linked review instructions." in str(returns[-1].content)
            observed.append(returns[-1])
            yield "Read the linked skill."
        else:
            assert expected in info.instructions
            yield {0: DeltaToolCall(name="view", json_args=json.dumps({"file_path": f"{expected}/SKILL.md"}))}

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        preview = await app.skill_catalog()
        item = next(item for item in preview.items if item.name == "review")
        assert item.logical_path == expected
        assert item.source_id == "a13n-harness-ui:project:workspace"
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Read the review skill.")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed
        assert operation.outcome.execution.output == "Read the linked skill."
    assert len(observed) == 1


@pytest.mark.parametrize("skill_name", [SKILL_NAME, "harness-ui-operations"])
async def test_root_run_reads_builtin_skill_navigation_and_documentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, skill_name: str
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
    documents = (
        ("SKILL.md", "references/navigation.md", "docs/configuration.md")
        if skill_name == SKILL_NAME
        else ("SKILL.md", "harness/files.md", "ui/threads.md")
    )
    expected_content = (
        ("Documentation map", "Documentation navigation", "Configuration reference")
        if skill_name == SKILL_NAME
        else ("Guidance map", "Use brace alternatives", "current_run describes the captured configuration")
    )
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
            assert f"{BUILTIN_SKILLS_PATH}/{skill_name}" in str(messages) + str(info)
            assert any(tool.name == "view" for tool in info.function_tools)
            assert f"{BUILTIN_SKILLS_PATH}/harness-ui-operations/harness/files.md" in info.instructions
            assert "Use brace alternatives" not in info.instructions
            assert "Your current Thread:" in info.instructions
            assert "admission only" in info.instructions
        else:
            content = str(returns[-1].content)
            expected = expected_content[step - 1]
            assert expected in content
            observed.append(documents[step - 1])
        if step < len(documents):
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": f"{BUILTIN_SKILLS_PATH}/{skill_name}/{documents[step]}"}),
                    tool_call_id=f"read-{step}",
                )
            }
        else:
            yield "Read the bundled configuration reference without changing any configuration."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path, host_mode="webui") as app:
        thread = await app.create_thread(defaults=NewThreadDefaults(project_id=None))
        receipt = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt=f"Use {skill_name} to inspect the bundled guidance. Do not edit files.",
        )
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed
        assert operation.outcome.execution.output.startswith("Read the bundled")
    assert observed == list(documents)


async def test_skill_preview_uses_run_only_roots_without_retargeting_thread(tmp_path):
    from a13n_harness_ui.environment_bindings import EnvironmentSelectionPatch

    path = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: skills\n")
    selected = tmp_path / "run-only"
    document = selected / ".agents/skills/run-only/SKILL.md"
    document.parent.mkdir(parents=True)
    document.write_text("---\nname: run-only\ndescription: Selected directory skill.\n---\n\nRun instructions.\n")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread()
        ordinary = await app.skill_catalog(thread_id=thread.thread_id)
        assert "run-only" not in {item.name for item in ordinary.items}
        preview = await app.skill_catalog(
            thread_id=thread.thread_id,
            environment=EnvironmentSelectionPatch(local_roots=(str(selected),)),
        )
        assert "run-only" in {item.name for item in preview.items}
        assert (await app.get_thread(thread.thread_id)).thread.configuration == thread.configuration


@pytest.mark.parametrize("root", [None, "/release-guidance"])
@pytest.mark.parametrize("name", ["threads", "coordinator", "worker"])
def test_ui_guidance_uses_flat_attribute_free_xml_sections(name: str, root: str | None) -> None:
    from a13n_harness_ui.builtin_guidance import ui_guidance

    content = ui_guidance(name, root)
    sections = ElementTree.fromstring(f"<guidance>{content}</guidance>")
    assert sections.text is None
    assert all(not section.attrib and not list(section) for section in sections)
    assert [section.tag for section in sections] == [
        "essential-rules",
        "reference"
        if root
        else {"threads": "best-practices", "coordinator": "worker-lifecycle", "worker": "reporting"}[name],
    ]


@pytest.mark.parametrize(
    "skills,files,file_tools,tools,reference",
    [
        (True, True, None, None, True),
        (False, True, None, None, False),
        (True, False, None, None, False),
        (True, True, ["ls"], None, False),
        (True, True, None, ["get_thread"], False),
    ],
)
async def test_guidance_delivery_requires_selected_skills_and_view(
    tmp_path, monkeypatch, skills, files, file_tools, tools, reference
):
    import yaml
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.models.function import FunctionModel

    path = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    content = yaml.safe_load(agent.read_text())
    content["capabilities"] = [
        {"capability": "dynamic_environment", "configuration": {"files_enabled": files, "file_tools": file_tools}},
    ]
    if skills:
        content["capabilities"].append({"capability": "skills"})
    if tools is not None:
        content["tools"] = tools
    agent.write_text(yaml.safe_dump(content))
    observed = []

    async def model(messages, info):
        instructions = info.instructions or ""
        observed.append(instructions)
        assert ("/harness-ui-operations/ui/threads.md" in instructions) is reference
        assert ("configuration inspection exposes" in instructions) is not reference
        assert "Your current Thread:" in instructions
        yield "done"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path, host_mode="webui") as app:
        thread = await app.create_thread(defaults=NewThreadDefaults(project_id=None))
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Inspect your instructions.")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed
    assert len(observed) == 1


@pytest.mark.parametrize("parent_references", [False, True])
async def test_async_child_selects_and_reads_its_own_guidance_bundle(tmp_path, monkeypatch, parent_references):
    import json

    import yaml
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from .test_builtin_subagents import _source

    path = _source(tmp_path, ())
    monkeypatch.setenv("TEST_KEY", "test-key")
    child_references = not parent_references

    def capabilities(references):
        result = [{"capability": "dynamic_environment", "configuration": {"files_enabled": True}}]
        if references:
            result.append({"capability": "skills"})
        return result

    parent_path = tmp_path / "agents/main.yaml"
    parent = yaml.safe_load(parent_path.read_text())
    parent["capabilities"] = capabilities(parent_references)
    parent["subagents"] = [{"agent": "agent-child"}]
    parent_path.write_text(yaml.safe_dump(parent))
    child = {**parent, "id": "agent-child", "name": "Child", "capabilities": capabilities(child_references)}
    child.pop("subagents")
    (tmp_path / "agents/child.yaml").write_text(yaml.safe_dump(child))
    calls = {"parent": 0, "child": 0}
    guidance_path = f"{BUILTIN_SKILLS_PATH}/harness-ui-operations/harness/files.md"

    async def model(messages, info):
        is_parent = "delegate" in {tool.name for tool in info.function_tools}
        owner = "parent" if is_parent else "child"
        calls[owner] += 1
        references = parent_references if is_parent else child_references
        assert (guidance_path in info.instructions) is references
        if is_parent:
            if calls[owner] == 1:
                yield {
                    0: DeltaToolCall(
                        name="delegate",
                        json_args=json.dumps(
                            {"subagent_name": "agent-child", "prompt": "Read your release guidance if available."}
                        ),
                        tool_call_id="delegate-child",
                    )
                }
            elif calls[owner] == 2:
                yield {
                    0: DeltaToolCall(
                        name="wait_subagent", json_args='{"timeout_seconds": 10}', tool_call_id="wait-child"
                    )
                }
            else:
                yield "Parent completed."
        elif child_references and calls[owner] == 1:
            yield {
                0: DeltaToolCall(
                    name="view", json_args=json.dumps({"file_path": guidance_path}), tool_call_id="read-guidance"
                )
            }
        else:
            if child_references:
                returns = [
                    part
                    for message in messages
                    if message.kind == "request"
                    for part in message.parts
                    if isinstance(part, ToolReturnPart)
                ]
                assert "Use brace alternatives" in str(returns[-1].content)
            yield "Child completed."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread(
            defaults=NewThreadDefaults(
                agent_id="agent-main", project_id=None, environment_profile_id="environment-native"
            )
        )
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Delegate the bounded task.")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed, operation
        executions = await app.query_child_executions(parent_thread_id=thread.thread_id)
        assert len(executions.executions) == 1
        assert executions.executions[0].persisted_status == "succeeded"
    assert calls["child"] == (2 if child_references else 1)


def test_operation_bundle_contains_new_reference_pages() -> None:
    from importlib.resources import files

    bundle = files("a13n_harness_ui").joinpath("assets/builtin_skills/harness-ui-operations")
    index = bundle.joinpath("SKILL.md").read_text()
    for name in ("environment-shell", "tool_search", "environment-routing"):
        relative = f"harness/{name}.md"
        assert relative in index
        assert "<!-- reference -->" in bundle.joinpath(relative).read_text()
