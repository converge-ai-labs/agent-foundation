from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import pytest
from converge_agent_harness import (
    FILE_VIEW_RULES,
    AgentContext,
    DefinitionError,
    DirectLocalEnvironmentConfiguration,
    DirectLocalEnvironmentProviderBinding,
    DirectLocalRootConfiguration,
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentPermissionSet,
    EnvironmentSkillSource,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    FileViewRule,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResultEvent,
    RunBindings,
    SkillCatalogItem,
    SkillManager,
    SkillPath,
    SkillsCapability,
    create_environment_run_binding,
)
from converge_agent_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


class _StaticSource:
    def __init__(self, source_id: str, roots: tuple[str, ...], entries: tuple[SkillCatalogItem, ...]) -> None:
        self.source_id = source_id
        self.logical_roots = roots
        self.entries = entries

    async def catalog(self, *, environment) -> tuple[SkillCatalogItem, ...]:
        del environment
        return self.entries


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


class _Materializer:
    materializer_id = "test-materializer"
    target_root = "/workspace/.agents/skills"

    async def materialize(self, *, environment) -> None:
        await environment.files.mkdir(self.target_root, parents=True, exist_ok=True)
        path = f"{self.target_root}/review"
        await environment.files.mkdir(path, parents=True, exist_ok=True)
        await environment.files.write_text(
            f"{path}/SKILL.md",
            "---\nname: review\ndescription: Review code carefully.\n---\n\n# Review\n\nFollow the checklist.\n",
            mode="upsert",
        )


def _binding(root: Path):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="skills-test",
            root=DirectLocalRootConfiguration(path=root, ownership="caller_owned"),
        )
    )
    return create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(
                EnvironmentBindingRequest(
                    binding_id="binding-1",
                    binding_revision=1,
                    alias="local",
                    permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                    default_working_directory="/",
                    provider_binding=provider,
                ),
            ),
            default_binding_id="binding-1",
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


def _manager(*, materialize: bool = False) -> SkillManager:
    return SkillManager(
        (
            EnvironmentSkillSource(
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
                    json_args=json.dumps({"file_path": file_path}),
                    tool_call_id="external-view",
                )
            }
        else:
            assert isinstance(returns[-1].content, dict)
            observed.update(returns[-1].content)
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(
                DynamicEnvironmentConfiguration(
                    max_reference_entries=64,
                )
            ),
            capability,
        ),
    )
    result = await executable.run(
        "Read",
        bindings=RunBindings.local(
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

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(_manager(materialize=True)),),
    )
    result = await executable.run("Review this", bindings=RunBindings.local(environment=_binding(tmp_path)))

    assert result.output_or_raise() == "done"
    assert (tmp_path / ".agents" / "skills" / "review" / "SKILL.md").is_file()
    instructions = str(seen[0].instructions)
    assert "<available-skills>" in instructions
    assert "Review code carefully." in instructions
    assert "<path>/workspace/.agents/skills/review</path>" in instructions
    assert "skill_activate" not in {tool.name for tool in seen[0].function_tools}
    assert "skill_inspect" not in {tool.name for tool in seen[0].function_tools}


async def test_skill_catalog_uses_ordered_later_source_precedence(tmp_path: Path) -> None:
    for root, description in (("global", "Global version"), ("project", "Project version")):
        path = tmp_path / root / "same"
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: same\ndescription: {description}\n---\n\n# Same\n",
            encoding="utf-8",
        )
    captured: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info)
        yield "done"

    manager = SkillManager(
        (
            EnvironmentSkillSource("global", ("/workspace/global",)),
            EnvironmentSkillSource("project", ("/workspace/project",)),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(manager),),
    )
    await executable.run("Use a skill", bindings=RunBindings.local(environment=_binding(tmp_path)))

    instructions = str(captured[0].instructions)
    assert "Project version" in instructions
    assert "Global version" not in instructions


async def test_ordinary_environment_skill_read_emits_usage_observation(tmp_path: Path) -> None:
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

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(
                DynamicEnvironmentConfiguration(
                    max_reference_entries=64,
                )
            ),
            SkillsCapability(_manager()),
        ),
    )
    events: list[HarnessEvent | HarnessRunResultEvent[str]] = []
    async with executable.stream(
        "Review",
        bindings=RunBindings.local(
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


async def test_external_capability_can_publish_skill_paths_for_relaxed_markdown_view(tmp_path: Path) -> None:
    skill = tmp_path / "external-skill"
    skill.mkdir()
    content = "".join(f"line {index}: {'x' * 60}\n" for index in range(220))
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
        f"line {index}: {'x' * 60}\n" for index in range(220)
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

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(
                DynamicEnvironmentConfiguration(
                    max_reference_entries=64,
                )
            ),
            SkillsCapability(_manager()),
        ),
    )
    result = await executable.run(
        "Review",
        bindings=RunBindings.local(
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
        arguments = {"file_path": "/workspace/.agents/skills/review/SKILL.md", "line_limit": 800}
        if next_offset is not None:
            arguments["line_offset"] = next_offset
        yield {
            0: DeltaToolCall(
                name="view",
                json_args=json.dumps(arguments),
                tool_call_id=f"skill-page-{len(returns) + 1}",
            )
        }

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(
                DynamicEnvironmentConfiguration(
                    max_reference_entries=64,
                )
            ),
            SkillsCapability(_manager()),
        ),
    )
    result = await executable.run(
        "Review",
        bindings=RunBindings.local(
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


async def test_skill_source_cannot_escape_its_declared_logical_roots(tmp_path: Path) -> None:
    for directory in ("allowed", "outside"):
        skill = tmp_path / directory / "escape"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: escape\ndescription: Escape root.\n---\n",
            encoding="utf-8",
        )
    source = _StaticSource(
        "static",
        ("/workspace/allowed",),
        (
            SkillCatalogItem(
                name="escape",
                description="Escape root.",
                path="/workspace/outside/escape",
            ),
        ),
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(SkillManager((source,))),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "Use a skill",
            bindings=RunBindings.local(environment=_binding(tmp_path)),
        )
    assert exc_info.value.code == "skill_path_outside_source"


async def test_skill_catalog_rejects_truncated_frontmatter_lines(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "long"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: long\ndescription: This description is too long for the selected line budget.\n---\n",
        encoding="utf-8",
    )
    manager = SkillManager(
        (
            EnvironmentSkillSource(
                "workspace",
                ("/workspace/skills",),
                max_line_length=16,
            ),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(manager),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "Use a skill",
            bindings=RunBindings.local(environment=_binding(tmp_path)),
        )
    assert exc_info.value.code == "skill_catalog_invalid"


async def test_skill_catalog_stops_reading_after_frontmatter(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "valid"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: valid\ndescription: Valid metadata.\n---\n" + "x" * 10_000,
        encoding="utf-8",
    )
    manager = SkillManager(
        (
            EnvironmentSkillSource(
                "workspace",
                ("/workspace/skills",),
                max_line_length=64,
            ),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(manager),),
    )

    result = await executable.run(
        "Use a skill",
        bindings=RunBindings.local(environment=_binding(tmp_path)),
    )
    assert result.output_or_raise() == "done"


async def test_custom_skill_source_requires_existing_regular_document(tmp_path: Path) -> None:
    (tmp_path / "skills" / "missing").mkdir(parents=True)
    source = _StaticSource(
        "static",
        ("/workspace/skills",),
        (
            SkillCatalogItem(
                name="missing",
                description="Missing document.",
                path="/workspace/skills/missing",
            ),
        ),
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(SkillManager((source,))),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "Use a skill",
            bindings=RunBindings.local(environment=_binding(tmp_path)),
        )
    assert exc_info.value.code == "skill_path_unavailable"


async def _text(value: str) -> AsyncIterator[str]:
    yield value
