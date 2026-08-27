from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from a13n_harness import (
    AgentContext,
    EnvironmentPath,
    HarnessBuilder,
    ModelRecoveryPolicy,
    RunBindings,
    RunSkillPaths,
    SkillPath,
    ToolMetadataKey,
    ToolRuntimeMetadata,
)
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


@dataclass(frozen=True, slots=True)
class _ExternalMetadata:
    value: str


_EXTERNAL_METADATA_KEY = ToolMetadataKey("external.view-hints", _ExternalMetadata)


class _PublishingCapability(AbstractCapability[AgentContext]):
    id = "external.publisher"

    def __init__(self) -> None:
        self.metadata_ids: list[int] = []

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        self.metadata_ids.append(id(ctx.deps.tool_metadata))
        ctx.deps.tool_metadata.publish(
            _EXTERNAL_METADATA_KEY,
            self.id,
            _ExternalMetadata("stable"),
        )
        return self


def _skill_path(name: str = "review") -> SkillPath:
    return SkillPath(
        name=name,
        source_id="external-source",
        directory=EnvironmentPath(
            binding_id="binding-1",
            binding_revision=1,
            path=f"/.agents/skills/{name}",
        ),
    )


def test_skill_paths_publish_immutable_owner_bound_snapshots() -> None:
    paths = RunSkillPaths()
    selected = _skill_path()

    paths.publish("external.skills", (selected,))
    paths.publish("external.skills", (selected,))

    assert paths.values == (selected,)
    with pytest.raises(RuntimeError, match="different value"):
        paths.publish("external.skills", (_skill_path("other"),))
    with pytest.raises(TypeError, match="SkillPath"):
        paths.publish("external.invalid", (object(),))  # type: ignore[arg-type]


def test_tool_runtime_metadata_accepts_external_typed_keys() -> None:
    key = _EXTERNAL_METADATA_KEY
    metadata = ToolRuntimeMetadata()
    value = _ExternalMetadata("wide")

    metadata.publish(key, "external.capability", value)
    metadata.publish(key, "external.capability", value)

    assert metadata.values(key) == (value,)
    with pytest.raises(RuntimeError, match="different value"):
        metadata.publish(key, "external.capability", _ExternalMetadata("changed"))
    with pytest.raises(TypeError, match="requires _ExternalMetadata"):
        metadata.publish(key, "external.other", "wrong")  # type: ignore[arg-type]


def test_tool_runtime_metadata_keeps_none_owner_publication_idempotent() -> None:
    key = ToolMetadataKey("external.optional", object)
    metadata = ToolRuntimeMetadata()

    metadata.publish(key, "external.owner", None)
    metadata.publish(key, "external.owner", None)

    assert metadata.values(key) == (None,)
    with pytest.raises(RuntimeError, match="different value"):
        metadata.publish(key, "external.owner", "changed")


def test_tool_runtime_metadata_rejects_same_named_key_with_another_type() -> None:
    metadata = ToolRuntimeMetadata()
    first = ToolMetadataKey("external.shared-key", _ExternalMetadata)
    conflicting = ToolMetadataKey("external.shared-key", str)
    metadata.publish(first, "external.first", _ExternalMetadata("one"))

    with pytest.raises(TypeError, match="another value type"):
        metadata.publish(conflicting, "external.second", "two")
    with pytest.raises(TypeError, match="another value type"):
        metadata.values(conflicting)


@pytest.mark.anyio
async def test_tool_runtime_metadata_is_reused_across_inner_recovery_attempts() -> None:
    capability = _PublishingCapability()
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            yield "partial"
            raise RuntimeError("stream interrupted")
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(capability,),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )

    result = await executable.run("start", bindings=RunBindings.local())

    assert result.output_or_raise() == "done"
    assert calls == 2
    assert len(capability.metadata_ids) >= 2
    assert len(set(capability.metadata_ids)) == 1
