from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from a13n_harness import (
    AgentContext,
    HarnessBuilder,
    ModelRecoveryPolicy,
    RequestContext,
    RunBindings,
    RunConfiguration,
)
from a13n_harness.context import (
    RunSkillPaths,
    SkillPath,
    ToolMetadataKey,
    ToolRuntimeMetadata,
)
from a13n_harness.environment import EnvironmentPath
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
            execution_id="mount-1",
            mount_id="mount-1",
            path=f"/.agents/skills/{name}",
        ),
    )


def test_run_bindings_metadata_detaches_nested_caller_values() -> None:
    source = {"correlation": {"labels": ["initial"]}}

    bindings = RunBindings.embedded(metadata=source)
    source["correlation"]["labels"].append("mutated")

    assert bindings.metadata == {"correlation": {"labels": ["initial"]}}
    with pytest.raises(TypeError):
        bindings.metadata["other"] = "value"  # type: ignore[index]


def test_run_bindings_preserves_positional_arguments_before_request_context() -> None:
    instance = RunBindings.embedded().instance
    configuration = RunConfiguration()

    bindings = RunBindings(instance, configuration, None, None, False, False)

    assert bindings.instance is instance
    assert bindings.configuration is configuration
    assert bindings.environment is None
    assert bindings.model_resolver is None
    assert bindings.toolset_instructions is False
    assert bindings.deferred_tools_supported is False
    assert bindings.request_context.headers == {}

    context = RequestContext(headers={"x-trace": "private-context"})
    assert RunBindings(instance, configuration, None, request_context=context).request_context is context


def test_request_context_is_detached_readonly_and_not_metadata() -> None:
    source = {"X-Trace": "private-context"}
    context = RequestContext(headers=source)
    source["X-Trace"] = "changed"
    bindings = RunBindings.embedded(request_context=context)
    assert bindings.request_context.headers == {"x-trace": "private-context"}
    assert bindings.metadata == {}
    assert "private-context" not in repr(bindings)
    with pytest.raises(TypeError):
        context.headers["x-trace"] = "changed"  # type: ignore[index]
    with pytest.raises(ValueError, match="unique ignoring case"):
        RequestContext(headers={"X-Trace": "one", "x-trace": "two"})


@pytest.mark.anyio
async def test_request_context_is_runtime_only_and_new_run_defaults_empty() -> None:
    seen: list[dict[str, str]] = []

    class Observe(AbstractCapability[AgentContext]):
        async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
            seen.append(dict(ctx.deps.request_context.headers))
            return self

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        assert "private-context" not in repr(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream), capabilities=(Observe(),)
    )
    result = await executable.run(
        "start", bindings=RunBindings.embedded(request_context=RequestContext(headers={"x-trace": "private-context"}))
    )
    assert result.state is not None
    assert "private-context" not in result.state.model_dump_json()
    await executable.run("next", bindings=RunBindings.embedded(), previous_state=result.state)
    assert seen == [{"x-trace": "private-context"}, {}]


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
            raise ConnectionResetError("stream interrupted")
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
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

    result = await executable.run("start", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert calls == 2
    assert len(capability.metadata_ids) >= 2
    assert len(set(capability.metadata_ids)) == 1
