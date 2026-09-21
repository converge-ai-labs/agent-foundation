from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import FrozenInstanceError

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, ModelRecoveryPolicy, RunBindings
from a13n_harness.capabilities import (
    DocumentsCapability,
    MediaCapability,
    TaskStateBinding,
    WebBinding,
    WebCapability,
    WebConfiguration,
    WebScrapeConfiguration,
    WebSearchConfiguration,
)
from a13n_harness.environment.advanced import EmptyEnvironmentRuntime
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models.function import FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "field",
    ["web", "media_reader", "document_converter", "file_media_understanding", "task_state", "client_toolsets"],
)
def test_run_binding_fields_reject_incompatible_values(field: str) -> None:
    with pytest.raises(TypeError):
        RunBindings.embedded(**{field: object()})


def test_passive_bindings_are_not_capabilities() -> None:
    assert not issubclass(WebBinding, AbstractCapability)
    assert not issubclass(TaskStateBinding, AbstractCapability)
    bindings = RunBindings.embedded(skill_selection=frozenset(), client_toolsets=())
    assert bindings.capabilities == ()
    with pytest.raises(FrozenInstanceError):
        bindings.skill_selection = frozenset({"changed"})


async def test_concurrent_runs_bind_shared_resource_definitions_to_current_dependencies_through_recovery() -> None:
    current: ContextVar[str] = ContextVar("current-test-run")
    observations = {}
    calls = {}
    barrier = asyncio.Barrier(2)

    class Provider:
        def __init__(self):
            self.closed = False

        async def request(self, request, *, policy):
            raise AssertionError("No tool call expected")

        async def authorize(self, url, *, purpose):
            raise AssertionError("No tool call expected")

        async def read(self, request):
            raise AssertionError("No tool call expected")

        async def convert(self, request):
            raise AssertionError("No tool call expected")

        async def aclose(self):
            self.closed = True

    class Observe(AbstractCapability):
        id = "test.observe-bindings"

        async def before_model_request(self, ctx, request_context):
            run = ctx.deps.instance.agent_instance_id
            current.set(run)
            observations.setdefault(run, []).append(
                (
                    ctx.deps,
                    tuple(ctx.capabilities[key] for key in ("a13n.web", "a13n.media", "a13n.documents")),
                    ctx.run_id,
                    tuple(ctx.capabilities[key]._bind(ctx) for key in ("a13n.web", "a13n.media", "a13n.documents")),
                )
            )
            assert all(ctx.deps._run_capability(key) is None for key in ("a13n.web", "a13n.media", "a13n.documents"))
            return request_context

    async def model(messages, info):
        run = current.get()
        calls[run] = calls.get(run, 0) + 1
        if calls[run] == 1:
            await asyncio.wait_for(barrier.wait(), timeout=5)
            yield "partial"
            raise ConnectionResetError("recoverable interruption")
        yield "done"

    transport = Provider()
    providers = [Provider(), Provider()]
    bindings = [
        RunBindings.embedded(
            environment=EmptyEnvironmentRuntime(),
            web=WebBinding(client=transport, policy=provider),
            media_reader=provider,
            document_converter=provider,
        )
        for provider in providers
    ]
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            WebCapability(
                WebConfiguration(search=WebSearchConfiguration(mode="off"), scrape=WebScrapeConfiguration(mode="off"))
            ),
            MediaCapability(),
            DocumentsCapability(),
            Observe(),
        ),
        model_recovery=ModelRecoveryPolicy(
            enabled=True, max_attempts=2, backoff_initial_seconds=0, backoff_max_seconds=0
        ),
    )
    results = await asyncio.gather(*(executable.run("start", bindings=value) for value in bindings))
    assert [result.output_or_raise() for result in results] == ["done", "done"]
    assert all(result.usage.requests == 2 for result in results)
    active = []
    for value in bindings:
        attempts = observations[value.instance.agent_instance_id]
        assert len(attempts) == 2 and attempts[0][2] != attempts[1][2]
        first_context, first_active, _, first_bound = attempts[0]
        assert all(one is two for one, two in zip(first_bound, attempts[1][3], strict=True))
        assert all(
            one is two
            for one, two in zip(first_bound, (value.web, value.media_reader, value.document_converter), strict=True)
        )
        assert attempts[1][0] is first_context
        assert all(one is two for one, two in zip(first_active, attempts[1][1], strict=True))
        assert all(item is not None for item in first_active)
        assert first_context.web is value.web
        assert first_context.media_reader is value.media_reader
        assert first_context.document_converter is value.document_converter
        active.append(first_active)
    assert all(one is two for one, two in zip(*active, strict=True))
    assert not transport.closed and all(not provider.closed for provider in providers)


async def test_compaction_reuses_the_current_web_binding_and_definition_owner() -> None:
    from a13n_harness import HarnessState
    from a13n_harness.capabilities import CompactionCapability, CompactionPolicy
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.usage import RequestUsage

    observed = []
    calls = []

    class Web:
        async def request(self, request, *, policy):
            raise AssertionError("Compaction must not dispatch tools")

        async def authorize(self, url, *, purpose):
            raise AssertionError("Compaction must not dispatch tools")

    class Observe(AbstractCapability):
        id = "test.compaction-bindings"

        async def before_model_request(self, ctx, request_context):
            observed.append((ctx.deps, ctx.deps.web, ctx.capabilities.get("a13n.web")))
            return request_context

    async def model(messages, info):
        calls.append(messages)
        yield "Compact summary" if len(calls) == 1 else "done"

    provider = Web()
    binding = WebBinding(client=provider, policy=provider)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            WebCapability(
                WebConfiguration(search=WebSearchConfiguration(mode="off"), scrape=WebScrapeConfiguration(mode="off"))
            ),
            Observe(),
            CompactionCapability(CompactionPolicy(trigger_tokens=2_000)),
        ),
    )
    result = await executable.run(
        "Continue",
        bindings=RunBindings.embedded(web=binding),
        previous_state=HarnessState.new(
            message_history=(
                ModelRequest(parts=[UserPromptPart(content="Original task")]),
                ModelResponse(parts=[TextPart(content="Long history")], usage=RequestUsage(input_tokens=2_100)),
            )
        ),
    )
    assert result.output_or_raise() == "done" and len(calls) == 2
    assert "Compact summary" in str(result.all_messages())
    assert len(observed) >= 2
    assert all(
        context is observed[0][0] and web is binding and active is observed[0][2] for context, web, active in observed
    )
    assert observed[0][2] is not None
