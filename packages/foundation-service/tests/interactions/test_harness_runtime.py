from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironment,
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    Environment,
)
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    AgentSpec,
    DeferredToolResume,
    HarnessBuilder,
    HarnessEvent,
    HarnessObservationContext,
    HarnessRunResult,
    HarnessRunStream,
    RunPreparationContext,
)
from a13n_harness.errors import RunError
from a13n_harness.model_context import (
    ModelContextNext,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from a13n_service.interactions import (
    AttemptMutationReceipt,
    AttemptPreparationAccepted,
    DeferredContinuationState,
    FoundationHarnessCollaborators,
    FoundationHarnessInvocation,
    FoundationHarnessRuntime,
    FoundationHarnessYielded,
    HarnessOutcomeAdapter,
    HarnessOutcomeProjection,
    HarnessStreamConsumer,
    HarnessTerminalConsumption,
    HostContinuationState,
    ImmediateHarnessInput,
    MaterializedHarnessInput,
    MountedHarnessEnvironments,
    RunStateEnvelope,
    RunStateStore,
    RunTerminalCommitter,
    RunTerminalDisposition,
    RunTerminalReceipt,
    SingleHarnessEnvironment,
    StoredRunState,
)
from pydantic import TypeAdapter
from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Capability, NodeResult
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolReturnPart
from pydantic_ai.models import Model, ModelRequestContext, ModelResolutionContext
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import RunUsage, UsageLimits

from .conftest import ATTEMPT_ID, NOW, TENANT_ID, initial_state, progress_state

pytestmark = pytest.mark.anyio


@dataclass
class _RuntimeCoordinator:
    current_state: StoredRunState
    instance: AgentInstanceContext
    trace: list[str]
    planned_handoff: bool = False
    environment: Environment | None = None
    _stream: HarnessRunStream[Any] | None = field(default=None, init=False)

    async def attach_stream(
        self,
        stream: HarnessRunStream[Any],
        preparation: AttemptPreparationAccepted,
    ) -> None:
        assert preparation.run_attempt_id == ATTEMPT_ID
        assert stream.context.instance is self.instance
        self._stream = stream
        self.trace.append("coordinator:attach")

    async def bind_model_attempt(self, ctx: RunContext[AgentContext]) -> None:
        assert ctx.deps.instance is self.instance
        self.trace.append("coordinator:bind")

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> None:
        del ctx, request_context
        self.trace.append("coordinator:before-model")
        if self.planned_handoff:
            assert self._stream is not None
            self._stream.cancel()

    async def after_model_response(
        self,
        ctx: RunContext[AgentContext],
        response: ModelResponse,
    ) -> None:
        del ctx, response
        self.trace.append("coordinator:after-model")

    async def after_tool_batch(
        self,
        ctx: RunContext[AgentContext],
        result: NodeResult[AgentContext],
    ) -> None:
        del ctx, result
        self.trace.append("coordinator:after-tools")

    async def commit_terminal_result(
        self,
        result: HarnessRunResult[Any],
        *,
        adapter: HarnessOutcomeAdapter,
        committer: RunTerminalCommitter,
    ) -> RunTerminalReceipt | None:
        del adapter, committer
        self.trace.append(f"coordinator:terminal:{result.status}")
        if self.planned_handoff:
            assert result.status == "cancelled"
            return None
        return RunTerminalReceipt(
            disposition=RunTerminalDisposition.completed,
            run_version=2,
            attempt_version=2,
            thread_version=2,
        )

    async def complete_handoff(self) -> AttemptMutationReceipt:
        assert self.environment is None or not self.environment.is_entered
        self.trace.append("coordinator:yield")
        return AttemptMutationReceipt(
            run_version=2,
            attempt_version=2,
            lease_expires_at=NOW + timedelta(minutes=5),
        )


@dataclass
class _ModelContext:
    trace: list[str]
    requests: list[ModelContextProjectionRequest] = field(default_factory=list)

    async def wrap_model_context(
        self,
        ctx: AgentContext,
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        del ctx
        self.trace.append("model-context")
        self.requests.append(request)
        return await handler(request)


@dataclass
class _EventProjector:
    events: list[HarnessEvent] = field(default_factory=list)

    async def project(self, event: HarnessEvent) -> None:
        self.events.append(event)


class _UnusedOutcomeAdapter:
    async def project(self, result: HarnessRunResult[Any]) -> HarnessOutcomeProjection:
        del result
        raise AssertionError("stub coordinator must own terminal selection")


class _UnusedTerminalCommitter:
    async def commit_state_outcome(self, *args: object, **kwargs: object) -> RunTerminalReceipt:
        del args, kwargs
        raise AssertionError("stub coordinator must own terminal selection")

    async def commit_failure(self, *args: object, **kwargs: object) -> RunTerminalReceipt:
        del args, kwargs
        raise AssertionError("stub coordinator must own terminal selection")

    async def reconcile_cancelled(self, *args: object, **kwargs: object) -> RunTerminalReceipt:
        del args, kwargs
        raise AssertionError("stub coordinator must own terminal selection")


async def _stored_state(objects, envelope) -> StoredRunState:
    return await RunStateStore(objects).create(TENANT_ID, envelope)


def _instance() -> AgentInstanceContext:
    return AgentInstanceContext(
        identity=AgentIdentityRef(issuer="foundation", subject="test-user"),
        agent_instance_id="instance-1",
        actor="user:test-user",
        host_refs={"session_id": "session-1"},
    )


def _preparation() -> AttemptPreparationAccepted:
    return AttemptPreparationAccepted(
        run_attempt_id=ATTEMPT_ID,
        fence=1,
        mutation=AttemptMutationReceipt(
            run_version=1,
            attempt_version=1,
            lease_expires_at=NOW + timedelta(minutes=5),
        ),
    )


def _consumer(projector: _EventProjector | None = None) -> HarnessStreamConsumer:
    return HarnessStreamConsumer(
        projector=projector or _EventProjector(),
        outcome_adapter=_UnusedOutcomeAdapter(),
        terminal_committer=_UnusedTerminalCommitter(),
    )


def _environment(root: Path, environment_id: str) -> DirectLocalEnvironment:
    root.mkdir(parents=True)
    provider = DirectLocalEnvironmentProvider()
    configuration = provider.validate_configuration(
        schema_version="1",
        value=DirectLocalProviderConfiguration(
            environment_id=environment_id,
            root=DirectLocalRootConfiguration(path=root),
        ).model_dump(mode="json"),
    )
    environment = provider.create_environment(configuration=configuration, state=None)
    assert isinstance(environment, DirectLocalEnvironment)
    return environment


async def test_runtime_wires_factory_environment_model_and_fresh_bindings(
    interaction_object_store,
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    model_calls: list[tuple[ModelMessage, ...]] = []
    resolver_calls: list[tuple[str, str, str]] = []
    instance = _instance()
    state = await _stored_state(interaction_object_store, initial_state())
    environment = _environment(tmp_path / "workspace", "runtime-workspace")

    async def model_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del info
        trace.append("model")
        model_calls.append(tuple(messages))
        yield "completed"

    model = FunctionModel(stream_function=model_stream)

    async def resolve_model(context: ModelResolutionContext[AgentContext], model_id: str) -> Model:
        resolver_calls.append((context.deps.run_id, context.deps.thread_id, model_id))
        trace.append("model-resolver")
        return model

    async def materialize(context: RunPreparationContext) -> str:
        assert context.instance is instance
        assert context.metadata == {"run_class": "interactive"}
        assert context.environment.snapshot.default_mount == "source"
        trace.append("input-factory")
        return "materialized input"

    model_context = _ModelContext(trace)
    usage = RunUsage()
    coordinator = _RuntimeCoordinator(state, instance, trace)
    projector = _EventProjector()
    result = await FoundationHarnessRuntime(HarnessBuilder(instrumentation=None)).execute(
        FoundationHarnessInvocation(
            definition=AgentDefinition(
                agent=AgentSpec(model="logical:accepted"),
                output_type=str,
            ),
            input=MaterializedHarnessInput(materialize),
            collaborators=FoundationHarnessCollaborators(
                instance=instance,
                model_resolver=resolve_model,
                metadata={"run_class": "interactive"},
                model_context=model_context,
                observation=HarnessObservationContext(
                    name="runtime-test",
                    session_id="session-1",
                ),
            ),
            environment=MountedHarnessEnvironments(
                entries={"source": environment},
                default_environment="source",
            ),
            usage=usage,
            usage_limits=UsageLimits(request_limit=2),
        ),
        coordinator=coordinator,
        preparation=_preparation(),
        consumer=_consumer(projector),
    )

    assert isinstance(result, HarnessTerminalConsumption)
    assert result.result.output_or_raise() == "completed"
    assert result.terminal.disposition is RunTerminalDisposition.completed
    assert resolver_calls == [(result.result.run_id, state.envelope.thread_id, "logical:accepted")]
    assert model_calls
    assert model_context.requests
    assert projector.events
    assert usage.requests == 1
    assert not environment.is_entered
    assert trace.index("input-factory") < trace.index("coordinator:attach")
    assert trace.index("coordinator:attach") < trace.index("model-resolver") < trace.index("model")
    assert "coordinator:bind" in trace


async def test_recovery_omits_already_applied_input_factory(
    interaction_object_store,
) -> None:
    trace: list[str] = []
    model_calls: list[tuple[ModelMessage, ...]] = []
    prior = (
        await HarnessBuilder(instrumentation=None)
        .build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(lambda messages, info: "checkpointed"),
        )
        .run("accepted input")
    )
    assert prior.state is not None
    initial_envelope = initial_state()
    initial_envelope = type(initial_envelope).model_validate(
        {
            **initial_envelope.model_dump(mode="python"),
            "thread_id": prior.state.thread_id,
            "harness": prior.state,
        }
    )
    initial = await _stored_state(interaction_object_store, initial_envelope)
    state = replace(
        initial,
        envelope=progress_state(initial.envelope),
        writer_fence=1,
    )
    instance = _instance()

    async def must_not_replay(context: RunPreparationContext) -> str:
        del context
        raise AssertionError("applied input factory was replayed")

    async def model_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del info
        model_calls.append(tuple(messages))
        yield "recovered"

    result = await FoundationHarnessRuntime(HarnessBuilder(instrumentation=None)).execute(
        FoundationHarnessInvocation(
            definition=AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model_stream),
            ),
            input=MaterializedHarnessInput(must_not_replay),
            collaborators=FoundationHarnessCollaborators(instance=instance),
        ),
        coordinator=_RuntimeCoordinator(state, instance, trace),
        preparation=_preparation(),
        consumer=_consumer(),
    )

    assert isinstance(result, HarnessTerminalConsumption)
    assert result.result.status == "completed", result.result.failure
    assert len(model_calls) == 1


async def test_pending_deferred_state_requires_native_resume(
    interaction_object_store,
) -> None:
    pending = initial_state().model_copy(
        update={
            "host": HostContinuationState(
                deferred=DeferredContinuationState(
                    requests={
                        "calls": [],
                        "approvals": [
                            {
                                "tool_name": "review",
                                "args": {},
                                "tool_call_id": "approval-1",
                            }
                        ],
                        "metadata": {},
                    }
                )
            )
        }
    )
    state = await _stored_state(interaction_object_store, pending)
    instance = _instance()

    with pytest.raises(RunError) as exc_info:
        await FoundationHarnessRuntime(HarnessBuilder(instrumentation=None)).execute(
            FoundationHarnessInvocation(
                definition=AgentDefinition(
                    agent=AgentSpec(),
                    output_type=str,
                    model=FunctionModel(lambda messages, info: "unused"),
                ),
                input=ImmediateHarnessInput(),
                collaborators=FoundationHarnessCollaborators(instance=instance),
            ),
            coordinator=_RuntimeCoordinator(state, instance, []),
            preparation=_preparation(),
            consumer=_consumer(),
        )

    assert exc_info.value.code == "foundation_deferred_resume_required"


async def test_runtime_passes_exact_native_deferred_resume(
    interaction_object_store,
) -> None:
    model_calls: list[tuple[ModelMessage, ...]] = []
    tool_calls: list[str] = []

    async def approved_step() -> str:
        tool_calls.append("executed")
        return "approved"

    async def model_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        model_calls.append(tuple(messages))
        if not any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield {
                0: DeltaToolCall(
                    name="approved_step",
                    json_args=json.dumps({}),
                    tool_call_id="approval-1",
                )
            }
            return
        yield "resumed"

    def definition() -> AgentDefinition[str]:
        return AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model_stream),
            capabilities=(
                Capability(
                    id="test.approval",
                    tools=[Tool(approved_step, requires_approval=True)],
                ),
            ),
        )

    prior = await HarnessBuilder(instrumentation=None).build(definition()).run("needs approval")
    assert prior.status == "suspended"
    assert prior.state is not None
    assert prior.deferred is not None
    deferred_value = TypeAdapter(DeferredToolRequests).dump_python(prior.deferred, mode="json")
    initial_envelope = initial_state()
    pending = RunStateEnvelope.model_validate(
        {
            **initial_envelope.model_dump(mode="python"),
            "thread_id": prior.state.thread_id,
            "harness": prior.state,
            "host": HostContinuationState(deferred=DeferredContinuationState(requests=deferred_value)),
        }
    )
    state = await _stored_state(interaction_object_store, pending)
    instance = _instance()
    result = await FoundationHarnessRuntime(HarnessBuilder(instrumentation=None)).execute(
        FoundationHarnessInvocation(
            definition=definition(),
            input=ImmediateHarnessInput(),
            collaborators=FoundationHarnessCollaborators(instance=instance),
            deferred_resume=DeferredToolResume(
                prior.deferred,
                DeferredToolResults(
                    calls={},
                    approvals={"approval-1": True},
                ),
            ),
        ),
        coordinator=_RuntimeCoordinator(state, instance, []),
        preparation=_preparation(),
        consumer=_consumer(),
    )

    assert isinstance(result, HarnessTerminalConsumption)
    assert result.result.output_or_raise() == "resumed"
    assert tool_calls == ["executed"]
    assert len(model_calls) == 2


async def test_planned_handoff_yields_only_after_environment_close(
    interaction_object_store,
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    instance = _instance()
    state = await _stored_state(interaction_object_store, initial_state())
    environment = _environment(tmp_path / "handoff", "handoff-workspace")
    coordinator = _RuntimeCoordinator(
        state,
        instance,
        trace,
        planned_handoff=True,
        environment=environment,
    )
    result = await FoundationHarnessRuntime(HarnessBuilder(instrumentation=None)).execute(
        FoundationHarnessInvocation(
            definition=AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=FunctionModel(lambda messages, info: "must not run"),
            ),
            input=ImmediateHarnessInput("accepted input"),
            collaborators=FoundationHarnessCollaborators(instance=instance),
            environment=SingleHarnessEnvironment(environment),
        ),
        coordinator=coordinator,
        preparation=_preparation(),
        consumer=_consumer(),
    )

    assert isinstance(result, FoundationHarnessYielded)
    assert result.result.status == "cancelled"
    assert result.mutation.run_version == 2
    assert not environment.is_entered
    assert trace[-2:] == ["coordinator:terminal:cancelled", "coordinator:yield"]
