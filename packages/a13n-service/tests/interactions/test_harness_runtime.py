from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path

import pytest
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    AgentSpec,
    DeferredToolResume,
    EnvironmentMount,
    HarnessBuilder,
    HarnessEvent,
    HarnessObservationContext,
    HarnessRunResultEvent,
    RunPreparationContext,
)
from a13n_harness.errors import RunError
from a13n_harness.model_context import (
    ModelContextNext,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL, DirectLocalEnvironment
from a13n_harness.providers.environment.management import Environment
from a13n_harness.providers.environment.models import (
    FILE_READ_ACTIONS,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPermissionSet,
    EnvironmentReadinessRequirement,
)
from a13n_service.interactions.attempts import AttemptMutationReceipt, AttemptPreparationAccepted
from a13n_service.interactions.environment_observation import (
    EnvironmentHookObservation,
    observe_environment_entry,
)
from a13n_service.interactions.harness_control import HarnessContextBinding, HarnessHookBoundary, HarnessRunIdentity
from a13n_service.interactions.harness_runtime import (
    HarnessCollaborators,
    HarnessDriver,
    HarnessInvocation,
    ImmediateHarnessInput,
    MaterializedHarnessInput,
    MountedHarnessEnvironments,
    SingleHarnessEnvironment,
)
from a13n_service.interactions.objects import RunStateStore, StoredRunState
from a13n_service.interactions.state import DeferredContinuationState, HostContinuationState, RunCheckpoint
from pydantic import TypeAdapter
from pydantic_ai import Tool
from pydantic_ai.capabilities import Capability, NodeResult
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolReturnPart
from pydantic_ai.models import Model, ModelRequestContext, ModelResolutionContext
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import RunUsage, UsageLimits

from .conftest import ATTEMPT_ID, NOW, ORGANIZATION_ID, initial_state, progress_state

pytestmark = pytest.mark.anyio


@dataclass
class _RuntimeCoordinator:
    current_state: StoredRunState
    instance: AgentInstanceContext
    trace: list[str]
    boundaries: list[HarnessHookBoundary] = field(default_factory=list)
    planned_handoff: bool = False
    environment: Environment | None = None
    driver: HarnessDriver | None = field(default=None, init=False)

    usage_batches: list[tuple] = field(default_factory=list)

    async def ingest_usage(self, harness_run_id, records):
        self.usage_batches.append((harness_run_id, tuple(records)))

    @property
    def terminal_observation_allowed(self) -> bool:
        return not self.planned_handoff

    async def enter_harness(
        self,
        identity: HarnessRunIdentity,
        preparation: AttemptPreparationAccepted,
    ) -> None:
        assert preparation.run_attempt_id == ATTEMPT_ID
        assert identity.thread_id == self.current_state.envelope.thread_id
        assert identity.run_id
        self.trace.append("coordinator:attach")

    async def after_stream_entry(self) -> None:
        self.trace.append("coordinator:stream-entry")

    async def before_model_node(self, boundary: HarnessHookBoundary) -> None:
        pass

    async def before_nested_model_request(self) -> None:
        raise AssertionError("this test has no nested provider request")

    async def bind_model_attempt(self, binding: HarnessContextBinding) -> None:
        assert isinstance(binding, HarnessContextBinding)
        self.trace.append("coordinator:bind")

    async def before_model_request(
        self,
        boundary: HarnessHookBoundary,
        request_context: ModelRequestContext,
    ) -> None:
        del request_context
        self.boundaries.append(boundary)
        self.trace.append("coordinator:before-model")
        if self.planned_handoff:
            assert self.driver is not None
            await self.driver.cancel()

    async def after_model_response(
        self,
        boundary: HarnessHookBoundary,
        response: ModelResponse,
    ) -> None:
        del response
        self.boundaries.append(boundary)
        self.trace.append("coordinator:after-model")

    async def after_tool_batch(
        self,
        boundary: HarnessHookBoundary,
        result: NodeResult[AgentContext],
        complete_messages: Sequence[ModelMessage],
    ) -> None:
        del result, complete_messages
        self.boundaries.append(boundary)
        self.trace.append("coordinator:after-tools")


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
    events: list[HarnessEvent | HarnessRunResultEvent[object]] = field(default_factory=list)
    environment_events: list[EnvironmentHookObservation] = field(default_factory=list)
    fail: bool = False

    def project(self, event: HarnessEvent | HarnessRunResultEvent[object]) -> None:
        if self.fail:
            raise RuntimeError("presentation unavailable")
        self.events.append(event)

    def project_environment(self, observation: EnvironmentHookObservation) -> None:
        if self.fail:
            raise RuntimeError("presentation unavailable")
        self.environment_events.append(observation)

    async def close(self) -> None:
        pass


async def _stored_state(objects, envelope) -> StoredRunState:
    return await RunStateStore(objects).create(ORGANIZATION_ID, envelope)


def _instance() -> AgentInstanceContext:
    return AgentInstanceContext(
        identity=AgentIdentityRef(issuer="a13n.service", subject="test-user"),
        agent_instance_id="instance-1",
        actor="user:test-user",
        host_refs={"session_id": "session-1"},
    )


def _preparation() -> AttemptPreparationAccepted:
    return AttemptPreparationAccepted(
        run_attempt_id=ATTEMPT_ID,
        attempt_number=1,
        mutation=AttemptMutationReceipt(
            run_version=1,
            attempt_version=1,
            lease_expires_at=NOW + timedelta(minutes=5),
        ),
    )


def _driver(
    coordinator: _RuntimeCoordinator,
    projector: _EventProjector | None = None,
) -> HarnessDriver:
    driver = HarnessDriver(
        HarnessBuilder(instrumentation=None),
        control=coordinator,
        projector=projector or _EventProjector(),
    )
    coordinator.driver = driver
    return driver


def _environment(root: Path, environment_id: str) -> DirectLocalEnvironment:
    root.mkdir(parents=True)
    provider = DIRECT_LOCAL
    configuration = provider.validate_environment(
        DirectLocalEnvironmentConfiguration(
            root=DirectLocalRootConfiguration(path=root),
        ).model_dump(mode="json"),
    )
    environment = provider.construct(
        operation_id="op-test",
        allow_create=True,
        configuration=configuration,
        environment_id=environment_id,
        state=None,
        runtime=None,
    )
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
    result = await _driver(coordinator, projector).run(
        HarnessInvocation(
            definition=AgentDefinition(
                agent=AgentSpec(model="logical:accepted"),
                output_type=str,
            ),
            input=MaterializedHarnessInput(materialize),
            collaborators=HarnessCollaborators(
                instance=instance,
                model_resolver=resolve_model,
                metadata={"run_class": "interactive"},
                model_context=model_context,
                observation=HarnessObservationContext(
                    name="runtime-test",
                    session_id="session-1",
                ),
            ),
            # A mounted aggregate owns its runtime, so observation is declared on the
            # entry before the aggregate is built, exactly as the Worker preparation does.
            environment=MountedHarnessEnvironments(
                entries={
                    "source": observe_environment_entry(
                        EnvironmentMount(
                            environment, permission_ceiling=EnvironmentPermissionSet(operations=FILE_READ_ACTIONS)
                        ),
                        projector,
                    )
                },
                default_environment="source",
            ),
            usage=usage,
            usage_limits=UsageLimits(request_limit=2),
        ),
        preparation=_preparation(),
    )

    assert result.output_or_raise() == "completed"
    assert resolver_calls == [(result.run_id, state.envelope.thread_id, "logical:accepted")]
    assert model_calls
    assert model_context.requests
    assert projector.events
    assert isinstance(projector.events[-1], HarnessRunResultEvent)
    assert tuple(event.event_type for event in projector.environment_events) == ("environment.adapter.closed",)
    assert "runtime-workspace" not in str(projector.environment_events)
    assert str(tmp_path / "workspace") not in str(projector.environment_events)
    assert usage.requests == 1
    assert coordinator.usage_batches
    assert coordinator.usage_batches[-1][1] == result.usage_records
    assert not environment.is_entered
    assert trace.index("input-factory") < trace.index("coordinator:attach")
    assert trace.index("coordinator:attach") < trace.index("model-resolver") < trace.index("model")
    assert "coordinator:bind" in trace
    assert coordinator.boundaries
    with pytest.raises(RunError) as error:
        await coordinator.boundaries[0].enqueue("late input", priority="asap")
    assert error.value.code == "service_control_identity_mismatch"


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

    coordinator = _RuntimeCoordinator(state, instance, trace)
    result = await _driver(coordinator).run(
        HarnessInvocation(
            definition=AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model_stream),
            ),
            input=MaterializedHarnessInput(must_not_replay),
            collaborators=HarnessCollaborators(instance=instance),
        ),
        preparation=_preparation(),
    )

    assert result.status == "completed", result.failure
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
        coordinator = _RuntimeCoordinator(state, instance, [])
        await _driver(coordinator).run(
            HarnessInvocation(
                definition=AgentDefinition(
                    agent=AgentSpec(),
                    output_type=str,
                    model=FunctionModel(lambda messages, info: "unused"),
                ),
                input=ImmediateHarnessInput(),
                collaborators=HarnessCollaborators(instance=instance),
            ),
            preparation=_preparation(),
        )

    assert exc_info.value.code == "service_deferred_resume_required"


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
    pending = RunCheckpoint.model_validate(
        {
            **initial_envelope.model_dump(mode="python"),
            "thread_id": prior.state.thread_id,
            "harness": prior.state,
            "host": HostContinuationState(deferred=DeferredContinuationState(requests=deferred_value)),
        }
    )
    state = await _stored_state(interaction_object_store, pending)
    instance = _instance()
    coordinator = _RuntimeCoordinator(state, instance, [])
    result = await _driver(coordinator).run(
        HarnessInvocation(
            definition=definition(),
            input=ImmediateHarnessInput(),
            collaborators=HarnessCollaborators(instance=instance),
            deferred_resume=DeferredToolResume(
                prior.deferred,
                DeferredToolResults(
                    calls={},
                    approvals={"approval-1": True},
                ),
            ),
        ),
        preparation=_preparation(),
    )

    assert result.output_or_raise() == "resumed"
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
    projector = _EventProjector()
    result = await _driver(coordinator, projector).run(
        HarnessInvocation(
            definition=AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=FunctionModel(lambda messages, info: "must not run"),
            ),
            input=ImmediateHarnessInput("accepted input"),
            collaborators=HarnessCollaborators(instance=instance),
            environment=SingleHarnessEnvironment(environment),
        ),
        preparation=_preparation(),
    )

    assert result.status == "cancelled"
    assert not any(isinstance(event, HarnessRunResultEvent) for event in projector.events)
    assert not environment.is_entered
    assert tuple(event.event_type for event in projector.environment_events) == ("environment.adapter.closed",)
    assert trace[-1] == "coordinator:before-model"


async def test_environment_preparation_failure_emits_only_safe_live_projection(
    interaction_object_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_preparation(**kwargs: object) -> None:
        del kwargs
        raise EnvironmentError("provider-private-body", code="attachment_denied")

    trace: list[str] = []
    instance = _instance()
    state = await _stored_state(interaction_object_store, initial_state())
    environment = _environment(tmp_path / "failure", "failed-workspace")
    monkeypatch.setattr(environment, "_prepare", fail_preparation)
    coordinator = _RuntimeCoordinator(state, instance, trace)
    projector = _EventProjector()

    async def use_environment(context):
        await context.environment.ensure_ready(EnvironmentReadinessRequirement(operations=frozenset({"files"})))
        return "accepted input"

    with pytest.raises(RunError, match="input factory failed") as error:
        await _driver(coordinator, projector).run(
            HarnessInvocation(
                definition=AgentDefinition(
                    agent=AgentSpec(),
                    output_type=str,
                    model=FunctionModel(lambda messages, info: "must not run"),
                ),
                input=MaterializedHarnessInput(use_environment),
                collaborators=HarnessCollaborators(instance=instance),
                environment=SingleHarnessEnvironment(environment),
            ),
            preparation=_preparation(),
        )
    assert isinstance(error.value.__cause__, EnvironmentError)
    assert str(error.value.__cause__) == "provider-private-body"

    assert tuple(event.event_type for event in projector.environment_events) == (
        "environment.preparation.started",
        "environment.preparation.failed",
        "environment.adapter.closed",
    )
    failed = projector.environment_events[1]
    assert failed.payload["failure"] == {
        "code": "attachment_denied",
        "message": "The Environment preparation operation failed.",
    }
    assert "provider-private-body" not in str(projector.environment_events)


async def test_environment_observations_preserve_ceiling_and_effective_permissions(
    interaction_object_store,
    tmp_path: Path,
) -> None:
    ceiling = EnvironmentPermissionSet(
        operations=frozenset(
            {EnvironmentAction.FILE_READ_TEXT, EnvironmentAction.FILE_WRITE_TEXT, EnvironmentAction.PORT_INSPECT}
        )
    )
    instance = _instance()
    state = await _stored_state(interaction_object_store, initial_state())
    environment = _environment(tmp_path / "observed", "observed-workspace")
    (tmp_path / "observed" / "input.txt").write_text("workspace input")
    projector = _EventProjector()
    effective_permissions: list[str] = []

    async def complete(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "completed"

    async def read_input(context: RunPreparationContext) -> str:
        text = (await context.environment.files.read_text("/workspace/input.txt")).text
        effective_permissions.extend(
            sorted(item.value for item in context.environment.snapshot.mounts[0].permission_ceiling.operations)
        )
        return text

    result = await _driver(_RuntimeCoordinator(state, instance, []), projector).run(
        HarnessInvocation(
            definition=AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=complete),
            ),
            input=MaterializedHarnessInput(read_input),
            collaborators=HarnessCollaborators(instance=instance),
            environment=SingleHarnessEnvironment(EnvironmentMount(environment, permission_ceiling=ceiling)),
        ),
        preparation=_preparation(),
    )

    assert result.output_or_raise() == "completed"
    assert tuple(event.event_type for event in projector.environment_events) == (
        "environment.preparation.started",
        "environment.preparation.ready",
        "environment.adapter.closed",
    )
    started, ready, closed = projector.environment_events
    assert started.payload["permission_ceiling"] == [
        "environment.file.read_text",
        "environment.file.write_text",
        "environment.port.inspect",
    ]
    assert ready.payload["permissions"] == effective_permissions
    assert ready.payload["ready_families"] == ["files"]
    assert ready.payload["availability"] == "available"
    assert ready.payload["permissions"] == ["environment.file.read_text", "environment.file.write_text"]
    assert closed.payload["status"] == "closed"
    assert not environment.is_entered
    assert str(tmp_path) not in str(projector.environment_events)


async def test_live_projection_failure_does_not_change_harness_outcome(
    interaction_object_store,
    tmp_path: Path,
) -> None:
    async def complete(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "completed"

    trace: list[str] = []
    instance = _instance()
    state = await _stored_state(interaction_object_store, initial_state())
    coordinator = _RuntimeCoordinator(state, instance, trace)

    result = await _driver(coordinator, _EventProjector(fail=True)).run(
        HarnessInvocation(
            definition=AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=complete),
            ),
            input=ImmediateHarnessInput("accepted input"),
            collaborators=HarnessCollaborators(instance=instance),
            environment=SingleHarnessEnvironment(_environment(tmp_path / "projection-failure", "workspace")),
        ),
        preparation=_preparation(),
    )

    assert result.output_or_raise() == "completed"
