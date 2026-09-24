from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    DefinitionError,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResultEvent,
    HarnessState,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness.capabilities import (
    FileSkillSource,
    SkillCatalogItem,
    SkillManager,
    SkillsCapability,
    SubagentCapability,
)
from a13n_harness.context import SkillPath
from a13n_harness.environment import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import (
    EnvironmentRuntime,
    create_environment_runtime,
)
from a13n_harness.environment.providers import (
    EnvironmentRuntimeMount,
)
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.spec import AgentSpec as HarnessAgentSpec
from a13n_harness.spec import HarnessModelCharacteristics, ModelCapability
from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from a13n_harness.toolsets import (
    FILE_VIEW_RULES,
    FileViewRule,
    MediaUnderstandingResult,
)
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from .environment_helpers import (
    DirectLocalEnvironmentProviderBinding,
    DirectLocalFilePolicy,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def scan_files(tmp_path: Path) -> LocalFileOperator:
    return LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="skill-scan",
        generation="test",
    )


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


class _StaticSource:
    def __init__(self, source_id: str, roots: tuple[str, ...], entries: tuple[SkillCatalogItem, ...]) -> None:
        self.source_id = source_id
        self.roots = roots
        self.entries = entries

    async def catalog(self, *, files) -> tuple[SkillCatalogItem, ...]:
        del files
        return self.entries


class _DirectScanOverrideManager(SkillManager):
    async def scan(self, *, files) -> tuple[SkillCatalogItem, ...]:
        del files
        raise AssertionError("scan_environment must not dispatch through scan")


@dataclass(init=False)
class _ExternalSkillPathsCapability(AbstractCapability[AgentContext]):
    id = "test.external-skill-paths"

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        ctx.deps.skill_paths.publish(
            self.id,
            (
                SkillPath(
                    name="external",
                    source_id="external-source",
                    directory=ctx.deps.environment.resolve_path("/workspace/external-skill"),
                ),
            ),
        )
        return self


@dataclass(init=False)
class _ExternalFileViewRulesCapability(AbstractCapability[AgentContext]):
    id = "test.external-file-view-rules"

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        ctx.deps.tool_metadata.publish(
            FILE_VIEW_RULES,
            self.id,
            FileViewRule(
                roots=(ctx.deps.environment.resolve_path("/workspace/external-files"),),
                suffixes=(".guide",),
                initial_line_limit=800,
                max_line_length=20_000,
                page_bytes=100_000_000,
                semantic_output_chars=100_000_000,
                preserve_complete_lines=True,
            ),
        )
        return self


class _MountChangingSource:
    def __init__(
        self,
        inner: FileSkillSource,
        runtime: EnvironmentRuntime,
        name: str,
        replacement: EnvironmentRuntimeMount,
    ) -> None:
        self._inner = inner
        self._runtime = runtime
        self._name = name
        self._replacement = replacement

    @property
    def source_id(self) -> str:
        return self._inner.source_id

    @property
    def roots(self) -> tuple[str, ...]:
        return self._inner.roots

    async def catalog(self, *, files) -> tuple[SkillCatalogItem, ...]:
        catalog = await self._inner.catalog(files=files)
        await self._runtime.replace(self._name, self._replacement)
        return catalog


class _Materializer:
    materializer_id = "test-materializer"
    target_root = "/workspace/.agents/skills"

    async def materialize(self, *, files) -> None:
        await files.mkdir(self.target_root, parents=True, exist_ok=True)
        path = f"{self.target_root}/review"
        await files.mkdir(path, parents=True, exist_ok=True)
        await files.write_text(
            f"{path}/SKILL.md",
            "---\nname: review\ndescription: Review code carefully.\n---\n\n# Review\n\nFollow the checklist.\n",
            mode="upsert",
        )


def _runtime_mount(root: Path, *, environment_id: str = "skills-test") -> EnvironmentRuntimeMount:
    return EnvironmentRuntimeMount(
        binding=DirectLocalEnvironmentProviderBinding(
            DirectLocalEnvironmentConfiguration(
                root=DirectLocalRootConfiguration(path=root),
            ),
            environment_id=environment_id,
        ),
        permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
        working_directory="/",
    )


def _binding(root: Path) -> EnvironmentRuntime:
    return create_environment_runtime(
        mounts={"local": _runtime_mount(root)},
        default_mount="local",
    )


def _multi_binding(default_root: Path, shared_root: Path) -> EnvironmentRuntime:
    return create_environment_runtime(
        mounts={
            "default": _runtime_mount(default_root, environment_id="skills-default"),
            "shared": _runtime_mount(shared_root, environment_id="skills-shared"),
        },
        default_mount="default",
    )


def _manager(*, materialize: bool = False) -> SkillManager:
    return SkillManager(
        (
            FileSkillSource(
                "workspace",
                ("/workspace/.agents/skills",),
            ),
        ),
        materializers=(_Materializer(),) if materialize else (),
    )


async def _run_single_view(
    tmp_path: Path,
    *,
    capability: AbstractCapability[AgentContext],
    file_path: str,
    view_arguments: dict[str, object] | None = None,
    on_view: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    observed: dict[str, object] = {}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
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
                    name="view",
                    json_args=json.dumps({"file_path": file_path, **(view_arguments or {})}),
                    tool_call_id="external-view",
                )
            }
        else:
            assert isinstance(returns[-1].content, dict)
            observed.update(returns[-1].content)
            if on_view is not None:
                on_view(observed)
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
            capability,
        ),
    )
    result = await executable.run(
        "Read",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            capabilities=(InvocationPolicyCapability(evaluator=_Allow()),),
        ),
    )
    assert result.output_or_raise() == "done"
    return observed


async def test_skill_manager_materializes_into_authorized_root_and_freezes_frontmatter(tmp_path: Path) -> None:
    (tmp_path / ".agents").mkdir()
    seen: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        seen.append(info)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(_manager(materialize=True)),),
    )
    result = await executable.run("Review this", bindings=RunBindings.embedded(environment=_binding(tmp_path)))

    assert result.output_or_raise() == "done"
    assert (tmp_path / ".agents" / "skills" / "review" / "SKILL.md").is_file()
    instructions = str(seen[0].instructions)
    assert "<available-skills>" in instructions
    assert "Review code carefully." in instructions
    assert "<path>/workspace/.agents/skills/review</path>" in instructions
    assert "skill_activate" not in {tool.name for tool in seen[0].function_tools}
    assert "skill_inspect" not in {tool.name for tool in seen[0].function_tools}


async def test_default_skills_capability_scans_only_workspace_agents_skills(tmp_path: Path) -> None:
    selected = tmp_path / ".agents" / "skills" / "default"
    selected.mkdir(parents=True)
    (selected / "SKILL.md").write_text(
        "---\nname: default\ndescription: Default workspace skill.\n---\n",
        encoding="utf-8",
    )
    ambient = tmp_path / "other-skills" / "ambient"
    ambient.mkdir(parents=True)
    (ambient / "SKILL.md").write_text(
        "---\nname: ambient\ndescription: Must not be scanned.\n---\n",
        encoding="utf-8",
    )
    captured: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info)
        yield "done"

    capability = SkillsCapability()
    assert capability.manager.roots == ("/workspace/.agents/skills",)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(capability,),
    )
    result = await executable.run("Use a skill", bindings=RunBindings.embedded(environment=_binding(tmp_path)))

    assert result.output_or_raise() == "done"
    instructions = str(captured[0].instructions)
    assert "Default workspace skill." in instructions
    assert "Must not be scanned." not in instructions


async def test_host_can_scan_skills_through_entered_environment_file_operator(tmp_path: Path) -> None:
    skill = tmp_path / ".agents" / "skills" / "managed"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: managed\ndescription: Managed by the Host.\n---\n",
        encoding="utf-8",
    )
    instance = AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="skill-manager"),
        agent_instance_id="skill-manager-host",
    )

    async with _binding(tmp_path).bind(
        thread_id="thread-skill-scan",
        run_id="host-skill-scan",
        instance=instance,
        host_refs={},
    ) as environment:
        catalog = await _DirectScanOverrideManager.default().scan_environment(environment=environment)
        catalog.require_current(environment)
        expected_mount_id = environment.resolve_path("/workspace").mount_id

    assert [
        (
            item.name,
            item.description,
            item.path,
            item.source_id,
            item.document.mount_id,
            item.document.path,
        )
        for item in catalog.items
    ] == [
        (
            "managed",
            "Managed by the Host.",
            "/workspace/.agents/skills/managed",
            "workspace",
            expected_mount_id,
            "/.agents/skills/managed/SKILL.md",
        )
    ]


async def test_host_can_scan_non_virtual_local_file_operator(tmp_path: Path) -> None:
    skill = tmp_path / ".agents" / "skills" / "direct"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: direct\ndescription: Scanned without virtual paths.\n---\n",
        encoding="utf-8",
    )
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="cli-files",
        generation="generation-1",
    )
    manager = SkillManager((FileSkillSource("local", ("/.agents/skills",)),))

    catalog = await manager.scan(files=files)
    default_catalog = await SkillManager.default().scan(files=files)

    assert [(item.name, item.path, item.source_id) for item in catalog] == [
        ("direct", "/.agents/skills/direct", "local")
    ]
    assert default_catalog == ()


async def test_environment_scan_pins_roots_across_bindings(tmp_path: Path) -> None:
    default_root = tmp_path / "default"
    shared_root = tmp_path / "shared"
    for root, skill_root, name in (
        (default_root, ".agents/skills", "project"),
        (shared_root, "skills", "shared"),
    ):
        skill = root / skill_root / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name.title()} skill.\n---\n",
            encoding="utf-8",
        )
    binding = _multi_binding(default_root, shared_root)
    manager = SkillManager(
        (
            FileSkillSource("project", ("/workspace/.agents/skills",)),
            FileSkillSource("shared", ("/environment/shared/skills",)),
        )
    )
    instance = AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="skill-manager"),
        agent_instance_id="skill-manager-multi-binding",
    )

    async with binding.bind(
        thread_id="thread-multi-scan",
        run_id="host-multi-scan",
        instance=instance,
        host_refs={},
    ) as environment:
        catalog = await manager.scan_environment(environment=environment)
        expected_mounts = {
            "project": environment.resolve_path("/workspace").mount_id,
            "shared": environment.resolve_path("/environment/shared").mount_id,
        }

    assert [(item.name, item.document.mount_id, item.document.path) for item in catalog.items] == [
        ("project", expected_mounts["project"], "/.agents/skills/project/SKILL.md"),
        ("shared", expected_mounts["shared"], "/skills/shared/SKILL.md"),
    ]
    assert expected_mounts["project"] != expected_mounts["shared"]


async def test_bound_catalog_ignores_unrelated_mount_replacement(tmp_path: Path) -> None:
    default_root = tmp_path / "default"
    shared_root = tmp_path / "shared"
    replacement_root = tmp_path / "replacement"
    skill = default_root / ".agents" / "skills" / "project"
    skill.mkdir(parents=True)
    shared_root.mkdir()
    replacement_root.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: project\ndescription: Project skill.\n---\n",
        encoding="utf-8",
    )
    binding = _multi_binding(default_root, shared_root)
    instance = AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="skill-manager"),
        agent_instance_id="skill-manager-unrelated-refresh",
    )
    async with binding.bind(
        thread_id="thread-unrelated-refresh",
        run_id="host-unrelated-refresh",
        instance=instance,
        host_refs={},
    ) as environment:
        await binding._activate()
        catalog = await SkillManager.default().scan_environment(environment=environment)
        await binding.replace(
            "shared",
            _runtime_mount(replacement_root, environment_id="skills-shared-replacement"),
        )

        catalog.require_current(environment)


async def test_skills_capability_rejects_mount_replacement_during_scan(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    for root, description in ((first_root, "Revision A metadata."), (second_root, "Revision B metadata.")):
        skill = root / ".agents" / "skills" / "review"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: review\ndescription: {description}\n---\n",
            encoding="utf-8",
        )

    binding = _binding(first_root)
    source = _MountChangingSource(
        FileSkillSource("workspace", ("/workspace/.agents/skills",)),
        binding,
        "local",
        _runtime_mount(second_root, environment_id="skills-test-replacement"),
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(SkillManager((source,))),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run("Review", bindings=RunBindings.embedded(environment=binding))
    assert exc_info.value.code == "skill_catalog_stale"
    assert exc_info.value.details["root"] == "/workspace/.agents/skills"
    assert isinstance(exc_info.value.details["mount_id"], str)


async def test_environment_scan_rejects_empty_root_replacement_during_scan(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    (first_root / ".agents" / "skills").mkdir(parents=True)
    (second_root / ".agents" / "skills").mkdir(parents=True)
    binding = _binding(first_root)
    source = _MountChangingSource(
        FileSkillSource("workspace", ("/workspace/.agents/skills",)),
        binding,
        "local",
        _runtime_mount(second_root, environment_id="skills-test-replacement"),
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(SkillManager((source,))),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run("Review", bindings=RunBindings.embedded(environment=binding))
    assert exc_info.value.code == "skill_catalog_stale"
    assert exc_info.value.details["root"] == "/workspace/.agents/skills"
    assert isinstance(exc_info.value.details["mount_id"], str)


async def test_default_skills_capability_allows_missing_workspace_root(tmp_path: Path) -> None:
    captured: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(),),
    )
    result = await executable.run("No skills", bindings=RunBindings.embedded(environment=_binding(tmp_path)))

    assert result.output_or_raise() == "done"
    assert "<available-skills>" not in str(captured[0].instructions)


async def test_default_skills_capability_allows_no_environment_binding() -> None:
    captured: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(),),
    )
    result = await executable.run("No environment", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert len(captured) == 1
    assert "<available-skills>" not in str(captured[0].instructions)


async def test_default_skill_manager_appends_host_sources_with_later_precedence(tmp_path: Path) -> None:
    for root, description in (
        (tmp_path / ".agents" / "skills", "Workspace version."),
        (tmp_path / "host-skills", "Host version."),
    ):
        skill = root / "same"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: same\ndescription: {description}\n---\n",
            encoding="utf-8",
        )
    captured: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info)
        yield "done"

    manager = SkillManager.default(additional_sources=(FileSkillSource("host", ("/workspace/host-skills",)),))
    assert manager.roots == ("/workspace/.agents/skills", "/workspace/host-skills")
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(manager),),
    )
    result = await executable.run("Use a skill", bindings=RunBindings.embedded(environment=_binding(tmp_path)))

    assert result.output_or_raise() == "done"
    instructions = str(captured[0].instructions)
    assert "Host version." in instructions
    assert "Workspace version." not in instructions


async def test_optional_file_skill_source_skips_each_unavailable_root(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "available"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: available\ndescription: Available root.\n---\n",
        encoding="utf-8",
    )
    captured: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info)
        yield "done"

    manager = SkillManager(
        (
            FileSkillSource(
                "mixed",
                ("/workspace/skills", "/environment/missing/skills"),
                required=False,
            ),
        )
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(manager),),
    )
    result = await executable.run("Use a skill", bindings=RunBindings.embedded(environment=_binding(tmp_path)))

    assert result.output_or_raise() == "done"
    assert "Available root." in str(captured[0].instructions)


async def test_required_file_skill_source_rejects_each_unavailable_root(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "available"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: available\ndescription: Available root.\n---\n",
        encoding="utf-8",
    )
    manager = SkillManager(
        (
            FileSkillSource(
                "mixed",
                ("/workspace/skills", "/environment/missing/skills"),
                required=True,
            ),
        )
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(manager),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run("Use a skill", bindings=RunBindings.embedded(environment=_binding(tmp_path)))
    assert exc_info.value.code == "skill_source_unavailable"
    assert exc_info.value.details["source_id"] == "mixed"


def test_file_skill_source_rejects_relative_file_operator_root() -> None:
    with pytest.raises(ValueError, match="absolute FileOperator paths"):
        FileSkillSource(
            "invalid",
            ("missing/skills",),
            required=False,
        )


@pytest.mark.parametrize(
    "root",
    (
        "C:/Users/example/.agents/skills",
        "c:/",
        "//server/share/skills",
        "//server/share/",
    ),
)
def test_file_skill_source_accepts_canonical_windows_aggregate_roots(root: str) -> None:
    source = FileSkillSource("windows", (root,), required=False)

    assert source.roots == (root,)


@pytest.mark.parametrize("root", ("missing/skills", "/skills/../escape", "/skills/"))
def test_skill_manager_rejects_invalid_custom_source_root(root: str) -> None:
    source = _StaticSource("invalid", (root,), ())

    with pytest.raises(ValueError, match="skill roots"):
        SkillManager((source,))


async def test_skill_catalog_uses_ordered_later_source_precedence(
    tmp_path: Path, scan_files: LocalFileOperator
) -> None:
    for root, description in (("global", "Global version"), ("project", "Project version")):
        path = tmp_path / root / "same"
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: same\ndescription: {description}\n---\n\n# Same\n",
            encoding="utf-8",
        )
    manager = SkillManager(
        (
            FileSkillSource("global", ("/global",)),
            FileSkillSource("project", ("/project",)),
        )
    )
    catalog = await manager.scan(files=scan_files)
    assert [(item.name, item.description, item.source_id) for item in catalog] == [
        ("same", "Project version", "project")
    ]


async def test_host_skill_selection_injects_only_exact_selected_names(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        skill = tmp_path / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Use {name}.\n---\n\n# {name.title()}\n",
            encoding="utf-8",
        )
    captured: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(SkillManager((FileSkillSource("workspace", ("/workspace/skills",)),))),),
    )
    result = await executable.run(
        "Use a skill",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            skill_selection=frozenset({"alpha"}),
        ),
    )

    assert result.output_or_raise() == "done"
    instructions = str(captured[0].instructions)
    assert 'skill name="alpha"' in instructions
    assert "Use alpha." in instructions
    assert 'skill name="beta"' not in instructions
    assert "Use beta." not in instructions


async def test_empty_host_skill_selection_injects_no_skill_catalog(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "alpha"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Use alpha.\n---\n",
        encoding="utf-8",
    )
    captured: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(SkillManager((FileSkillSource("workspace", ("/workspace/skills",)),))),),
    )
    result = await executable.run(
        "Use a skill",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            skill_selection=frozenset(),
        ),
    )

    assert result.output_or_raise() == "done"
    assert "<available-skills>" not in str(captured[0].instructions)


async def test_resumed_run_reselects_skills_from_fresh_host_bindings(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        skill = tmp_path / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Use {name}.\n---\n",
            encoding="utf-8",
        )
    captured: list[str] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(str(info.instructions))
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(SkillManager((FileSkillSource("workspace", ("/workspace/skills",)),))),),
    )
    first = await executable.run(
        "First",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            skill_selection=frozenset({"alpha"}),
        ),
    )
    assert first.state is not None
    second = await executable.run(
        "Second",
        bindings=RunBindings.embedded(environment=_binding(tmp_path)),
        previous_state=first.state,
    )

    assert first.output_or_raise() == second.output_or_raise() == "done"
    assert 'skill name="alpha"' in captured[0]
    assert 'skill name="beta"' not in captured[0]
    assert 'skill name="alpha"' in captured[1]
    assert 'skill name="beta"' in captured[1]


async def test_child_run_uses_its_own_skill_selection(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        skill = tmp_path / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Use {name}.\n---\n",
            encoding="utf-8",
        )
    parent_instructions: list[str] = []
    child_instructions: list[str] = []

    async def child_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        child_instructions.append(str(info.instructions))
        yield "child-done"

    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        parent_instructions.append(str(info.instructions))
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
                    name="delegate",
                    json_args=json.dumps({"subagent": "worker", "prompt": "inspect"}),
                    tool_call_id="delegate-skill-selection",
                )
            }
            return
        yield "parent-done"

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="skill-child",
        model=FunctionModel(stream_function=child_stream),
        capabilities=(SkillsCapability(SkillManager((FileSkillSource("workspace", ("/workspace/skills",)),))),),
    )

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=parent_stream),
        capabilities=(
            SubagentCapability(),
            SkillsCapability(SkillManager((FileSkillSource("workspace", ("/workspace/skills",)),))),
        ),
        subagents=(
            SubagentDefinition(
                name="worker",
                description="Inspect one task.",
                agent=child,
            ),
        ),
    )

    result = await executable.run(
        "Delegate",
        bindings=RunBindings(
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="test", subject="parent"),
                agent_instance_id="skill-parent",
            ),
            environment=_binding(tmp_path),
            capabilities=(InvocationPolicyCapability(evaluator=_Allow()),),
            skill_selection=frozenset({"alpha"}),
        ),
    )

    assert result.output_or_raise() == "parent-done"
    assert parent_instructions
    assert all('skill name="alpha"' in instructions for instructions in parent_instructions)
    assert all('skill name="beta"' not in instructions for instructions in parent_instructions)
    assert len(child_instructions) == 1
    assert 'skill name="alpha"' in child_instructions[0]
    assert 'skill name="beta"' in child_instructions[0]


async def test_host_skill_selection_rejects_unknown_names(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "alpha"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Use alpha.\n---\n",
        encoding="utf-8",
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(SkillManager((FileSkillSource("workspace", ("/workspace/skills",)),))),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "Use a skill",
            bindings=RunBindings.embedded(
                environment=_binding(tmp_path),
                skill_selection=frozenset({"missing"}),
            ),
        )
    assert exc_info.value.code == "skill_selection_unknown"
    assert exc_info.value.details == {"skills": ["missing"], "truncated": False}


def test_skill_selection_requires_immutable_bounded_exact_names() -> None:
    with pytest.raises(TypeError, match="frozenset"):
        RunBindings.embedded(skill_selection=("alpha",))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="bounded"):
        RunBindings.embedded(skill_selection=frozenset({"x" * 257}))
    with pytest.raises(ValueError, match="bounded"):
        RunBindings.embedded(skill_selection=frozenset({""}))


@pytest.mark.parametrize("content", ["none", "standard", "full"])
async def test_ordinary_environment_skill_read_emits_usage_observation(tmp_path: Path, content: str) -> None:
    from a13n_harness import HarnessInstrumentation, HarnessTraceContent
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    path = tmp_path / ".agents" / "skills" / "review"
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code.\n---\n\n# Review\n",
        encoding="utf-8",
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
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
                    name="view",
                    json_args=json.dumps({"file_path": "/workspace/.agents/skills/review/SKILL.md"}),
                    tool_call_id="skill-read-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent(content))
    ).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
            SkillsCapability(_manager()),
        ),
    )
    events: list[HarnessEvent | HarnessRunResultEvent[str]] = []
    async with executable.stream(
        "Review",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            capabilities=(InvocationPolicyCapability(evaluator=_Allow()),),
        ),
    ) as run:
        async for event in run:
            events.append(event)

    extension_payloads = [
        event.event.payload
        for event in events
        if isinstance(event, HarnessEvent) and isinstance(event.event, HarnessExtensionEvent)
    ]
    assert any(payload.get("type") == "skills_catalog_resolved" for payload in extension_payloads)
    assert any(
        payload.get("type") == "skill_accessed"
        and payload.get("skill_name") == "review"
        and payload.get("source_id") == "workspace"
        for payload in extension_payloads
    )

    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "harness.run")
    assert root.attributes["a13n.skills.available"] == ("review",)
    assert root.attributes["a13n.skills.accessed"] == ("review",)
    assert root.attributes["a13n.skills.access_count"] == 1
    resolution = next(span for span in spans if span.name == "harness.skills.resolve")
    assert resolution.attributes["a13n.skills.selection"] == "all"
    assert resolution.attributes["a13n.skills.discovered_count"] == 1
    assert resolution.attributes["a13n.skills.selected_count"] == 1
    assert resolution.attributes["langfuse.observation.metadata.skills_excluded_count"] == 0
    if content == "none":
        assert "a13n.output" not in resolution.attributes
    else:
        assert json.loads(resolution.attributes["a13n.output"]) == {
            "skills": [{"name": "review", "source_id": "workspace"}],
            "count": 1,
            "omitted": 0,
        }
    assert (
        resolution.end_time
        < next(span for span in spans if span.attributes.get("gen_ai.operation.name") == "chat").start_time
    )
    tool = next(span for span in spans if span.attributes.get("gen_ai.operation.name") == "execute_tool")
    assert tool.attributes["a13n.skill.name"] == "review"
    assert tool.attributes["a13n.skill.source_id"] == "workspace"
    if content == "none":
        assert "a13n.input" not in root.attributes
    provider.shutdown()


@pytest.mark.parametrize("external", [False, True])
async def test_skill_file_views_survive_cold_resume_without_current_skill_catalog(
    tmp_path: Path, external: bool
) -> None:
    root = "external-skill" if external else ".agents/skills/review"
    content = "".join(f"line {index}: {'x' * 90}\n" for index in range(40))
    protected = [f"{root}/{name}" for name in ("SKILL.md", "nested/guide.md", "data.json", "script.py", "README")]
    ordinary = [f"{root}-other/guide.md", "notes.md"]
    for path in (*protected, *ordinary):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (tmp_path / root / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code.\n---\n" + content,
        encoding="utf-8",
    )
    # A discovered but unselected package must not receive retention treatment.
    other = tmp_path / ".agents/skills/unselected"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text(
        "---\nname: unselected\ndescription: Another workflow.\n---\n" + content,
        encoding="utf-8",
    )
    ordinary.append(".agents/skills/unselected/SKILL.md")
    failed = f"{root}/missing.txt"
    paths = [*protected, *ordinary, failed]
    observed: dict[str, ToolReturnPart] = {}

    async def read_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                index: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": f"/workspace/{path}"}),
                    tool_call_id=f"read-{index}",
                )
                for index, path in enumerate(paths)
            }
        else:
            observed.update({part.tool_call_id: part for part in returns})
            yield "read complete"

    reader = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=read_stream),
        capabilities=(
            DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
            _ExternalSkillPathsCapability() if external else SkillsCapability(_manager()),
        ),
    )
    first = await reader.run(
        "Read skill files",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            skill_selection=None if external else frozenset({"review"}),
            capabilities=(InvocationPolicyCapability(evaluator=_Allow()),),
        ),
    )
    assert first.output_or_raise() == "read complete"
    assert first.state is not None
    for index, path in enumerate(paths):
        part = observed[f"read-{index}"]
        assert part.metadata == ({"a13n.cold-start": "preserve"} if path in protected else None)
        assert part.content["ok"] is (path != failed)
        if path != failed:
            assert part.content["content"] == (tmp_path / path).read_text(encoding="utf-8")
            assert "a13n.cold-start" not in part.content

    history = list(first.state.message_history)
    assert isinstance(history[-1], ModelResponse)
    history[-1] = replace(history[-1], timestamp=datetime.now(UTC) - timedelta(hours=2))
    state = HarnessState.new(
        thread_id=first.state.thread_id,
        message_history=history,
        agent_context_state=first.state.agent_context_state,
        environment_states=first.state.environment_states,
    )
    previous = HarnessState.model_validate_json(state.model_dump_json())
    saved = previous.model_dump_json()

    def assert_retention(messages) -> None:
        returns = {
            part.tool_call_id: part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        }
        for index, path in enumerate(paths):
            part = returns[f"read-{index}"]
            if path in ordinary:
                assert "chars removed after cold start" in part.content["content"]
            else:
                assert part.content == observed[f"read-{index}"].content
                assert part.metadata == observed[f"read-{index}"].metadata

    resumed_requests: list[list[ModelMessage]] = []

    async def resume_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        resumed_requests.append(messages)
        yield "continued"

    # No current files, skill owner, or directory lookup is needed to retain old results.
    resumed = (
        await HarnessBuilder()
        .build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=resume_stream),
        )
        .run("Continue", previous_state=previous, bindings=RunBindings.embedded())
    )
    assert resumed.output_or_raise() == "continued"
    assert resumed.state is not None
    assert len(resumed_requests) == 1
    assert_retention(resumed_requests[0])
    assert_retention(resumed.state.message_history)
    assert previous.model_dump_json() == saved


@pytest.mark.parametrize("native", [False, True])
async def test_skill_media_views_mark_only_successful_results(tmp_path: Path, native: bool) -> None:
    skill = tmp_path / "external-skill"
    skill.mkdir()
    for path in (skill / "image.png", tmp_path / "image.png"):
        path.write_bytes(b"\x89PNG")
    paths = ["external-skill/image.png", "image.png", "external-skill/missing.png", "external-skill/manual.pdf"]
    observed: list[ToolReturnPart] = []
    understanding_calls = []

    class UnderstandingProvider:
        async def understand(self, request):
            understanding_calls.append(request)
            return MediaUnderstandingResult(text="Image description. " * 200)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                index: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": f"/workspace/{path}"}),
                    tool_call_id=f"media-{index}",
                )
                for index, path in enumerate(paths)
            }
        else:
            observed.extend(returns)
            yield "done"

    result = (
        await HarnessBuilder()
        .build(
            HarnessAgentSpec(
                model_characteristics=HarnessModelCharacteristics(
                    capabilities=frozenset({ModelCapability.IMAGE_UNDERSTANDING}) if native else frozenset(),
                )
            ),
            output_type=str,
            model=FunctionModel(stream_function=stream),
            capabilities=(
                DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
                _ExternalSkillPathsCapability(),
            ),
        )
        .run(
            "View skill media",
            bindings=RunBindings.embedded(
                environment=_binding(tmp_path),
                capabilities=(InvocationPolicyCapability(evaluator=_Allow()),),
                file_media_understanding=UnderstandingProvider(),
            ),
        )
    )
    assert result.output_or_raise() == "done"
    by_id = {part.tool_call_id: part for part in observed}
    assert by_id["media-0"].metadata == {"a13n.cold-start": "preserve"}
    assert by_id["media-1"].metadata is None
    for call_id in ("media-2", "media-3"):
        assert by_id[call_id].metadata is None
        assert by_id[call_id].content["ok"] is False
    assert len(understanding_calls) == (0 if native else 2)
    if not native:
        assert by_id["media-0"].content == by_id["media-1"].content == "Image description. " * 200


async def test_external_capability_can_publish_skill_paths_for_relaxed_markdown_view(tmp_path: Path) -> None:
    skill = tmp_path / "external-skill"
    skill.mkdir()
    content = "".join(f"line {index}: {'x' * 90}\n" for index in range(160))
    (skill / "guide.md").write_bytes(content.encode("utf-8"))

    viewed = await _run_single_view(
        tmp_path,
        capability=_ExternalSkillPathsCapability(),
        file_path="/workspace/external-skill/guide.md",
    )

    assert viewed["content"] == content
    assert viewed["has_more"] is False
    assert "disclosure" not in viewed


async def test_external_capability_can_publish_file_view_rules_with_hard_limit_clamping(tmp_path: Path) -> None:
    root = tmp_path / "external-files"
    root.mkdir()
    content = "".join(f"line {index}: {'x' * 80}\n" for index in range(520))
    (root / "manual.guide").write_bytes(content.encode("utf-8"))

    viewed = await _run_single_view(
        tmp_path,
        capability=_ExternalFileViewRulesCapability(),
        file_path="/workspace/external-files/manual.guide",
    )

    assert isinstance(viewed["content"], str)
    assert content.startswith(viewed["content"])
    assert viewed["content"] != content
    assert viewed["has_more"] is True
    assert isinstance(viewed["next_line_offset"], int)
    assert viewed["next_line_offset"] > 0
    assert viewed["disclosure"]["truncated"] is True
    assert len(json.dumps(viewed, ensure_ascii=False, separators=(",", ":"))) <= 20_000


async def test_selected_skill_markdown_uses_relaxed_full_read_budget(tmp_path: Path) -> None:
    skill = tmp_path / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    content = "---\nname: review\ndescription: Review code.\n---\n\n" + "".join(
        f"line {index}: {'x' * 90}\n" for index in range(160)
    )
    (skill / "SKILL.md").write_bytes(content.encode("utf-8"))
    (tmp_path / "notes.md").write_bytes(content.encode("utf-8"))
    observed: dict[str, object] = {}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
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
                    name="view",
                    json_args=json.dumps({"file_path": "/workspace/.agents/skills/review/SKILL.md"}),
                    tool_call_id="skill-read",
                ),
                1: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": "/workspace/notes.md"}),
                    tool_call_id="ordinary-read",
                ),
            }
        else:
            observed.update({part.tool_call_id: part.content for part in returns})
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
            SkillsCapability(_manager()),
        ),
    )
    result = await executable.run(
        "Review",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            capabilities=(InvocationPolicyCapability(evaluator=_Allow()),),
        ),
    )

    assert result.output_or_raise() == "done"
    skill_result = observed["skill-read"]
    ordinary_result = observed["ordinary-read"]
    assert isinstance(skill_result, dict)
    assert skill_result["content"] == content
    assert skill_result["has_more"] is False
    assert "disclosure" not in skill_result
    assert isinstance(ordinary_result, dict)
    assert ordinary_result["content"] != content
    assert ordinary_result["disclosure"]["truncated"] is True


async def test_large_selected_skill_markdown_continues_without_skipping_lines(tmp_path: Path) -> None:
    skill = tmp_path / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    content = "---\nname: review\ndescription: Review code.\n---\n\n" + "".join(
        f"line {index}: {'x' * (3_000 if index == 300 else 80)}\n" for index in range(520)
    )
    (skill / "SKILL.md").write_bytes(content.encode("utf-8"))
    pages: list[dict[str, object]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            assert returns[-1].metadata == {"a13n.cold-start": "preserve"}
            latest = returns[-1].content
            assert isinstance(latest, dict)
            if len(pages) < len(returns):
                pages.append(latest)
            if latest["has_more"] is False:
                yield "done"
                return
            next_offset = latest["next_line_offset"]
        else:
            next_offset = None
        arguments = {"file_path": "/workspace/.agents/skills/review/SKILL.md", "line_limit": 1000}
        if next_offset is not None:
            arguments["line_offset"] = next_offset
        yield {
            0: DeltaToolCall(
                name="view",
                json_args=json.dumps(arguments),
                tool_call_id=f"skill-page-{len(returns) + 1}",
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
            SkillsCapability(_manager()),
        ),
    )
    result = await executable.run(
        "Review",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            capabilities=(InvocationPolicyCapability(evaluator=_Allow()),),
        ),
    )

    assert result.output_or_raise() == "done"
    assert len(pages) >= 2
    assert "".join(str(page["content"]) for page in pages) == content
    for previous, current in pairwise(pages):
        assert current["line_offset"] == previous["next_line_offset"]
    assert all(str(page["content"]).endswith("\n") for page in pages[:-1])


async def test_skill_source_cannot_escape_its_declared_roots(tmp_path: Path, scan_files: LocalFileOperator) -> None:
    for directory in ("allowed", "outside"):
        skill = tmp_path / directory / "escape"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: escape\ndescription: Escape root.\n---\n",
            encoding="utf-8",
        )
    source = _StaticSource(
        "static",
        ("/allowed",),
        (
            SkillCatalogItem(
                name="escape",
                description="Escape root.",
                path="/outside/escape",
            ),
        ),
    )
    with pytest.raises(DefinitionError) as exc_info:
        await SkillManager((source,)).scan(files=scan_files)
    assert exc_info.value.code == "skill_path_outside_source"


async def test_skill_catalog_rejects_truncated_frontmatter_lines(tmp_path: Path, scan_files: LocalFileOperator) -> None:
    skill = tmp_path / "skills" / "long"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: long\ndescription: This description is too long for the selected line budget.\n---\n",
        encoding="utf-8",
    )
    manager = SkillManager(
        (
            FileSkillSource(
                "workspace",
                ("/skills",),
                max_line_length=16,
            ),
        )
    )
    with pytest.raises(DefinitionError) as exc_info:
        await manager.scan(files=scan_files)
    assert exc_info.value.code == "skill_catalog_invalid"


async def test_skill_catalog_stops_reading_after_frontmatter(tmp_path: Path, scan_files: LocalFileOperator) -> None:
    skill = tmp_path / "skills" / "valid"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: valid\ndescription: Valid metadata.\n---\n" + "x" * 10_000,
        encoding="utf-8",
    )
    manager = SkillManager(
        (
            FileSkillSource(
                "workspace",
                ("/skills",),
                max_line_length=64,
            ),
        )
    )
    catalog = await manager.scan(files=scan_files)
    assert [(item.name, item.description) for item in catalog] == [("valid", "Valid metadata.")]


async def test_custom_skill_source_requires_existing_regular_document(
    tmp_path: Path, scan_files: LocalFileOperator
) -> None:
    (tmp_path / "skills" / "missing").mkdir(parents=True)
    source = _StaticSource(
        "static",
        ("/skills",),
        (
            SkillCatalogItem(
                name="missing",
                description="Missing document.",
                path="/skills/missing",
            ),
        ),
    )
    with pytest.raises(DefinitionError) as exc_info:
        await SkillManager((source,)).scan(files=scan_files)
    assert exc_info.value.code == "skill_path_unavailable"


async def _text(value: str) -> AsyncIterator[str]:
    yield value


async def test_file_source_can_skip_invalid_plugin_entries_without_hiding_valid_skills(tmp_path: Path, caplog) -> None:
    good = tmp_path / "skills" / "good"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text("---\nname: good\ndescription: A valid Skill.\n---\n")
    bad = tmp_path / "skills" / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("Incomplete frontmatter")
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="plugin-test",
        generation="generation-1",
    )
    tolerant = FileSkillSource("plugin", ("/skills",), skip_invalid=True)
    entries = await tolerant.catalog(files=files)
    assert [entry.name for entry in entries] == ["good"]
    assert "skill_catalog_entry_skipped" in caplog.text
    with pytest.raises(DefinitionError):
        await FileSkillSource("strict", ("/skills",)).catalog(files=files)


@pytest.mark.parametrize("skill_path", [True, False])
@pytest.mark.parametrize("max_line_length", [2000, 262_144])
async def test_large_view_bounds_return_successful_pages(
    tmp_path: Path, skill_path: bool, max_line_length: int
) -> None:
    skill = tmp_path / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    content = "---\nname: review\ndescription: Review code.\n---\n\n" + "line\n" * 46
    (skill / "SKILL.md").write_text(content)
    (tmp_path / "notes.md").write_text(content)
    relative = ".agents/skills/review/SKILL.md" if skill_path else "notes.md"
    viewed = await _run_single_view(
        tmp_path,
        capability=SkillsCapability(_manager()),
        file_path=f"/workspace/{relative}",
        view_arguments={"line_limit": 1000, "max_line_length": max_line_length},
    )
    assert viewed["ok"] is True
    assert "error" not in viewed
    assert isinstance(viewed["content"], str)
    assert content.startswith(viewed["content"])
    assert viewed["truncated_lines"] == []
    assert viewed["content"] == content
    assert viewed["has_more"] is False
    assert "next_line_offset" not in viewed


@pytest.mark.parametrize("requested_lines, expected_lines", [(300, 800), (1000, 905)])
async def test_skill_default_and_larger_agent_line_requests_are_effective(
    tmp_path: Path, requested_lines: int, expected_lines: int
) -> None:
    skill = tmp_path / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    content = "---\nname: review\ndescription: Review code.\n---\n\n" + "line\n" * 900
    (skill / "SKILL.md").write_text(content)
    viewed = await _run_single_view(
        tmp_path,
        capability=SkillsCapability(_manager()),
        file_path="/workspace/.agents/skills/review/SKILL.md",
        view_arguments={"line_limit": requested_lines, "max_line_length": 262_144},
    )
    assert viewed["ok"] is True
    assert viewed["lines_read"] == expected_lines
    assert viewed["content"] == "".join(content.splitlines(keepends=True)[:expected_lines])
    assert viewed["truncated_lines"] == []
    assert viewed["has_more"] is (expected_lines < 905)


@pytest.mark.parametrize("skill_path", [True, False])
@pytest.mark.parametrize("tail", ["", "later\nlast"])
async def test_model_clipped_first_line_does_not_skip_later_source_lines(
    tmp_path: Path, skill_path: bool, tail: str
) -> None:
    skill = tmp_path / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: review\ndescription: Review.\n---\n")
    first_line = "中文𐐀" * 12_000 + "\n"
    path = skill / "guide.md" if skill_path else tmp_path / "notes.md"
    path.write_text(first_line + tail, encoding="utf-8")
    logical_path = f"/workspace/{path.relative_to(tmp_path).as_posix()}"
    viewed = await _run_single_view(
        tmp_path,
        capability=SkillsCapability(_manager()),
        file_path=logical_path,
        view_arguments={"line_limit": 1000, "max_line_length": 262_144},
    )
    assert viewed["ok"] is True
    assert viewed["lines_read"] == 1
    assert viewed["truncated_lines"] == [1]
    assert first_line.startswith(viewed["content"])
    assert viewed["has_more"] is bool(tail)
    assert len(json.dumps(viewed, ensure_ascii=False, separators=(",", ":"))) <= (20_000 if skill_path else 12_000)
    assert "model output limit" in viewed["disclosure"]["hint"]
    if tail:
        assert viewed["next_line_offset"] == 1
        later = await _run_single_view(
            tmp_path,
            capability=SkillsCapability(_manager()),
            file_path=logical_path,
            view_arguments={"line_offset": viewed["next_line_offset"]},
        )
        assert later["content"] == tail
        assert later["has_more"] is False
    else:
        assert "next_line_offset" not in viewed


@pytest.mark.parametrize("requested_width, expected_truncation", [(2000, [1]), (262_144, [])])
async def test_agent_line_width_above_skill_default_reaches_provider(
    tmp_path: Path, requested_width: int, expected_truncation: list[int]
) -> None:
    skill = tmp_path / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: review\ndescription: Review.\n---\n")
    # Redaction is irrelevant here: inspect the unshortened provider page in
    # the output spill because the complete line exceeds the model budget.
    content = "x" * 25_000
    (skill / "guide.md").write_text(content)

    def inspect_spill(viewed: dict[str, object]) -> None:
        # Spill lifetime is Run-local; inspect before executable.run closes it.
        spill = viewed["disclosure"]["output_file_path"]
        assert isinstance(spill, str)
        assert spill.startswith("/environment/local/")
        stored = json.loads((tmp_path / spill.removeprefix("/environment/local/")).read_text())
        assert stored["truncated_lines"] == expected_truncation
        assert stored["content"] == (content[:20_000] if expected_truncation else content)

    await _run_single_view(
        tmp_path,
        capability=SkillsCapability(_manager()),
        file_path="/workspace/.agents/skills/review/guide.md",
        view_arguments={"max_line_length": requested_width},
        on_view=inspect_spill,
    )


@pytest.mark.parametrize("selection", [None, frozenset(), frozenset({"skill-00"}), frozenset({"missing"})])
@pytest.mark.parametrize("content", ["none", "standard"])
async def test_resolution_phase_records_selection_and_bounded_results(tmp_path, selection, content):
    from a13n_harness import HarnessInstrumentation, HarnessTraceContent
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode

    for index in range(20):
        path = tmp_path / "skills" / f"skill-{index:02}"
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: skill-{index:02}\ndescription: Private instruction.\n---\nPrivate skill body."
        )
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent(content))
    ).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(SkillManager((FileSkillSource("workspace", ("/workspace/skills",)),))),),
    )
    bindings = RunBindings.embedded(
        environment=_binding(tmp_path),
        skill_selection=selection,
    )
    try:
        if selection == frozenset({"missing"}):
            with pytest.raises(DefinitionError, match="absent"):
                await executable.run("hello", bindings=bindings)
        else:
            await executable.run("hello", bindings=bindings)
        span = next(span for span in exporter.get_finished_spans() if span.name == "harness.skills.resolve")
        assert span.attributes["a13n.skills.selection"] == ("all" if selection is None else "explicit")
        assert span.attributes["a13n.skills.discovered_count"] == 20
        assert "Private" not in str(span.attributes)
        assert "/workspace" not in str(span.attributes)
        if selection == frozenset({"missing"}):
            assert span.status.status_code is StatusCode.ERROR
            assert span.attributes["a13n.phase.status"] == "failed"
            assert span.attributes["a13n.skills.unknown_count"] == 1
        else:
            count = 20 if selection is None else len(selection)
            assert span.attributes["a13n.skills.selected_count"] == count
            if content != "none":
                output = json.loads(span.attributes["a13n.output"])
                assert output["count"] == count
                assert len(output["skills"]) == min(count, 16)
                assert output["omitted"] == max(0, count - 16)
        if content == "none":
            assert "a13n.output" not in span.attributes
    finally:
        provider.shutdown()
