"""Model-facing inline delegation with complete child execution semantics."""

from __future__ import annotations

import asyncio
import re
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import UsageLimits

from a13n_harness._json import dump_json_bytes
from a13n_harness.capabilities.context import restored_history_summary
from a13n_harness.context import AgentContext, BuiltSubagent, RunBindings
from a13n_harness.environment.providers import BoundEnvironment, EnvironmentRuntime, EnvironmentRuntimeMount
from a13n_harness.environment.sources import EnvironmentEntry
from a13n_harness.errors import DefinitionError, HarnessError, RunCleanupError, RunError, StateError
from a13n_harness.events import (
    HarnessEvent,
    HarnessRunResultEvent,
    InlineDelegationPayload,
    emit_harness_event,
)
from a13n_harness.identity import AgentInstanceContext
from a13n_harness.input import RunInputValue
from a13n_harness.observation import observe_operation, observe_output, record_span_metadata
from a13n_harness.providers.environment.models import EnvironmentChange, EnvironmentError
from a13n_harness.result import HarnessRunResult
from a13n_harness.state import HarnessState
from a13n_harness.tools.deferred import DeferredToolResume, preflight_deferred_resume
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from a13n_harness.usage import intersect_usage_limits

from ._instructions import InstructionFunctionToolset, tool_instruction

if TYPE_CHECKING:
    from a13n_harness.capabilities.subagents import InlineSubagentCollectionState

_SUBAGENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_JSON_ADAPTER = TypeAdapter(JsonValue)
_INLINE_RESULT_POLICY = ToolOutputPolicy(
    max_inline_bytes=256 * 1024,
    max_output_bytes=4 * 1024 * 1024,
    overflow="fail",
    redact=True,
)


class DelegateResult(BaseModel):
    """Bounded ordinary result returned to the parent model."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    execution_id: str
    subagent: str
    output: JsonValue


class DelegationToolset:
    """Own inline child dispatch, state advancement, event forwarding, and result projection."""

    def __init__(
        self,
        *,
        owner: AbstractCapability[AgentContext],
        context: AgentContext,
        state: InlineSubagentCollectionState,
    ) -> None:
        self._owner = owner
        self._context = context
        self._state = state.model_copy(deep=True)
        self._state_lock = asyncio.Lock()
        self._active_lock = asyncio.Lock()
        self._active_children: set[str] = set()

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        delegate_tool = HarnessTool(
            self.delegate,
            harness_metadata=HarnessToolMetadata(
                tool_id="delegation.inline",
                effects=frozenset({"execute"}),
                credential_audiences=(),
                idempotency="none",
                output_policy=_INLINE_RESULT_POLICY,
            ),
            name="delegate",
            description="Run one declared subagent inline and wait for its complete result.",
        )
        resume_tool = HarnessTool(
            self.resume_subagent,
            harness_metadata=HarnessToolMetadata(
                tool_id="delegation.inline.resume",
                effects=frozenset({"execute"}),
                credential_audiences=(),
                idempotency="none",
                output_policy=_INLINE_RESULT_POLICY,
            ),
            name="resume_subagent",
            description="Continue one retained inline child and wait for its complete result.",
        )
        children = tuple(self._context.subagents.values())
        available = (
            "; ".join(f"{child.declaration.name}: {child.declaration.description[:512]}" for child in children[:64])
            if children
            else "none"
        )
        instruction = tool_instruction("delegate").format(available_subagents=available)
        return InstructionFunctionToolset(
            tools=[delegate_tool, resume_tool],
            id="a13n-delegation-tools",
            instructions=instruction,
        )

    async def delegate(
        self,
        ctx: RunContext[AgentContext],
        subagent: Annotated[str, Field(description="Stable declared subagent name")],
        prompt: Annotated[str, Field(min_length=1, max_length=1024 * 1024)],
    ) -> JsonValue:
        return await self._execute(ctx, subagent=subagent, prompt=prompt, child_instance_id=None)

    async def resume_subagent(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[str, Field(min_length=1, max_length=68)],
        prompt: Annotated[str, Field(min_length=1, max_length=1024 * 1024)],
    ) -> JsonValue:
        record = self._state.children.get(execution_id)
        if record is None:
            raise ToolFailed("Unknown inline child instance ID.")
        return await self._execute(
            ctx,
            subagent=record.subagent_name,
            prompt=prompt,
            child_instance_id=execution_id,
        )

    async def _execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        subagent: str,
        prompt: str,
        child_instance_id: str | None,
    ) -> JsonValue:
        self._require_context(ctx)
        invocation_id = f"delegation-{secrets.token_urlsafe(9)}"
        try:
            child = self._context.subagents.require(subagent)
        except KeyError as exc:
            raise ToolFailed("Unknown inline subagent.") from exc

        from a13n_harness.capabilities.subagents import _inline_subagent_records

        deferred_resume: DeferredToolResume | None = None
        continuation = child_instance_id is not None
        baseline: HarnessState | None = None
        selected_state: HarnessState | None = None
        reserved_id: str
        async with self._active_lock:
            if continuation:
                assert child_instance_id is not None
                record = self._state.children.get(child_instance_id)
                if record is None:
                    raise ToolFailed("Unknown inline child instance ID.")
                if record.subagent_name != subagent or record.child_definition_id != child.definition.definition_id:
                    raise ToolFailed("Inline child instance does not match the selected subagent.")
                if child_instance_id in self._active_children:
                    raise ToolFailed("Inline child instance is already active.")
                if record.deferred_requests is not None:
                    submission = next(
                        (
                            item
                            for item in self._context.inline_subagent_results
                            if item.child_thread_id == record.state.thread_id
                            and item.pending_run_id == record.pending_run_id
                        ),
                        None,
                    )
                    if submission is None:
                        raise ToolFailed(
                            "Inline child is waiting for Host-submitted deferred results; a prompt cannot resolve it."
                        )
                    deferred_resume = preflight_deferred_resume(
                        DeferredToolResume(record.deferred_requests, submission.results), previous_state=record.state
                    )
                reserved_id = child_instance_id
                selected_state = record.state.model_copy(deep=True)
            else:
                reserved_id = self._allocate_child_id(subagent)
                baseline = HarnessState.new()
                selected_state = baseline.model_copy(deep=True)
            self._active_children.add(reserved_id)

        try:
            try:
                child_input = None if deferred_resume is not None else _build_child_input(ctx, child, prompt)
                limits = intersect_usage_limits(
                    child.executable.definition_usage_limits(),
                    child.declaration.usage_limits,
                )
                with observe_operation(
                    "delegation",
                    capability_id=self._owner.id,
                    operation_id=invocation_id,
                ) as span:
                    record_span_metadata(
                        span,
                        {
                            "delegation.role": subagent,
                            "delegation.child_id": reserved_id,
                            "delegation.continuation": continuation,
                        },
                    )
                    descendants = _inline_subagent_records(selected_state)
                    bindings = _create_inline_child_bindings(ctx, child, reserved_id)
                    bindings = replace(
                        bindings,
                        inline_subagent_results=tuple(
                            item
                            for item in self._context.inline_subagent_results
                            if (pending := descendants.get(item.child_thread_id)) is not None
                            and pending.deferred_requests is not None
                            and pending.pending_run_id == item.pending_run_id
                        ),
                    )
                    bindings = await _finalize_child_bindings(ctx, child, bindings)
                    result = await self._run_child(
                        ctx,
                        child,
                        child_input,
                        reserved_id,
                        invocation_id,
                        bindings,
                        selected_state,
                        limits,
                        deferred_resume,
                    )
                    record_span_metadata(
                        span,
                        {
                            "delegation.result_status": result.status,
                            "delegation.state_available": result.state is not None,
                        },
                    )
                    observe_output(
                        span,
                        {"child_id": reserved_id, "status": result.status, "state_available": result.state is not None},
                        status="returned",
                    )
            except asyncio.CancelledError:
                raise
            except RunCleanupError as exc:
                # Cleanup failure withholds terminal delivery, not an already
                # validated checkpoint. Retain it without reporting tool success.
                outcome = exc.outcome
                if outcome is not None and outcome.state is not None:
                    await self._store_child_or_fail(
                        ctx,
                        reserved_id,
                        child,
                        outcome.state,
                        subagent=subagent,
                        invocation_id=invocation_id,
                        child_run_id=outcome.run_id,
                        deferred_requests=outcome.deferred,
                    )
                await _emit_delegation_event_best_effort(
                    ctx,
                    "failed",
                    reserved_id,
                    subagent,
                    "cleanup_failed",
                    invocation_id=invocation_id,
                    child_run_id=outcome.run_id if outcome is not None else None,
                )
                raise ToolFailed(f"Inline child {reserved_id} cleanup failed.") from exc
            except ToolFailed as exc:
                await _emit_delegation_event(
                    ctx,
                    "failed",
                    reserved_id,
                    subagent,
                    "dispatch_rejected",
                    invocation_id=invocation_id,
                )
                raise ToolFailed(f"Inline child {reserved_id} did not start successfully.") from exc
            except Exception as exc:
                await _emit_delegation_event_best_effort(
                    ctx,
                    "failed",
                    reserved_id,
                    subagent,
                    "dispatch_failed",
                    invocation_id=invocation_id,
                )
                raise ToolFailed("Inline delegation failed before a complete child result.") from exc

            state_to_store = result.state
            if state_to_store is None and not continuation:
                state_to_store = baseline

            if result.status != "completed":
                if state_to_store is not None:
                    await self._store_child_or_fail(
                        ctx,
                        reserved_id,
                        child,
                        state_to_store,
                        subagent=subagent,
                        invocation_id=invocation_id,
                        child_run_id=result.run_id,
                        deferred_requests=result.deferred,
                    )
                await _emit_delegation_event(
                    ctx,
                    "failed",
                    reserved_id,
                    subagent,
                    result.status,
                    invocation_id=invocation_id,
                    child_run_id=result.run_id,
                )
                raise ToolFailed(_child_failure_message(result, reserved_id))
            try:
                output = _project_output(result.output_or_raise())
            except ToolFailed as exc:
                await _emit_delegation_event(
                    ctx,
                    "failed",
                    reserved_id,
                    subagent,
                    "output_rejected",
                    invocation_id=invocation_id,
                    child_run_id=result.run_id,
                )
                if state_to_store is not None:
                    await self._store_child_or_fail(
                        ctx,
                        reserved_id,
                        child,
                        state_to_store,
                        subagent=subagent,
                        invocation_id=invocation_id,
                        child_run_id=result.run_id,
                    )
                raise ToolFailed(f"Inline child {reserved_id} returned an unusable result.") from exc
            if state_to_store is not None:
                await self._store_child_or_fail(
                    ctx,
                    reserved_id,
                    child,
                    state_to_store,
                    subagent=subagent,
                    invocation_id=invocation_id,
                    child_run_id=result.run_id,
                )
            await _emit_delegation_event(
                ctx,
                "completed",
                reserved_id,
                subagent,
                "completed",
                invocation_id=invocation_id,
                child_run_id=result.run_id,
            )
            return DelegateResult(
                execution_id=reserved_id,
                subagent=subagent,
                output=output,
            ).model_dump(mode="json")
        finally:
            async with self._active_lock:
                self._active_children.discard(reserved_id)

    async def _run_child(
        self,
        ctx: RunContext[AgentContext],
        child: BuiltSubagent,
        child_input: RunInputValue | None,
        child_instance_id: str,
        invocation_id: str,
        bindings: RunBindings,
        previous_state: HarnessState | None,
        limits: UsageLimits | None,
        deferred_resume: DeferredToolResume | None = None,
    ) -> HarnessRunResult[Any]:
        stream = child.executable.stream(
            child_input,
            bindings=bindings,
            previous_state=previous_state,
            deferred_resume=deferred_resume,
            usage_limits=limits,
        )
        forwarder = stream._bind_parent_event_forwarder(ctx.deps.events)
        terminal: HarnessRunResult[Any] | None = None
        try:
            async with stream:
                await emit_harness_event(
                    stream.context.events,
                    kind="delegation",
                    payload=InlineDelegationPayload(
                        invocation_id=invocation_id,
                        action="started",
                        child_instance_id=child_instance_id,
                        subagent=child.declaration.name,
                        status="running",
                        parent_run_id=self._context.run_id,
                        parent_agent_instance_id=self._context.instance.agent_instance_id,
                        parent_tool_call_id=ctx.tool_call_id,
                        child_run_id=stream.run_id,
                    ),
                )
                async for item in stream:
                    if isinstance(item, HarnessEvent):
                        await forwarder.forward(item)
                    elif isinstance(item, HarnessRunResultEvent):
                        terminal = item.result
        finally:
            forwarder.close()
        if terminal is None:
            raise RunError("Inline child ended without a terminal result.", code="child_result_missing")
        return terminal

    async def _store_child_or_fail(
        self,
        ctx: RunContext[AgentContext],
        child_instance_id: str,
        child: BuiltSubagent,
        state: HarnessState,
        *,
        subagent: str,
        invocation_id: str,
        child_run_id: str | None = None,
        deferred_requests: DeferredToolRequests | None = None,
    ) -> None:
        try:
            await self._store_child(
                child_instance_id, child, state, deferred_requests=deferred_requests, child_run_id=child_run_id
            )
        except (StateError, ValueError) as exc:
            await _emit_delegation_event(
                ctx,
                "failed",
                child_instance_id,
                subagent,
                "state_rejected",
                invocation_id=invocation_id,
                child_run_id=child_run_id,
            )
            raise ToolFailed(f"Inline child {child_instance_id} state could not be retained.") from exc

    async def _store_child(
        self,
        child_instance_id: str,
        child: BuiltSubagent,
        state: HarnessState,
        *,
        deferred_requests: DeferredToolRequests | None = None,
        child_run_id: str | None = None,
    ) -> None:
        from a13n_harness.capabilities.subagents import (
            _INLINE_SUBAGENT_STATE_VERSION,
            SUBAGENT_CAPABILITY_ID,
            InlineSubagentCollectionState,
            InlineSubagentState,
            _validate_inline_subagent_state,
        )

        pending_run_id = child_run_id if deferred_requests is not None else None
        previous = self._state.children.get(child_instance_id)
        if deferred_requests is None and previous is not None and previous.deferred_requests is not None:
            submission = next(
                (
                    item
                    for item in self._context.inline_subagent_results
                    if item.child_thread_id == state.thread_id and item.pending_run_id == previous.pending_run_id
                ),
                None,
            )
            if submission is not None:
                try:
                    preflight_deferred_resume(
                        DeferredToolResume(previous.deferred_requests, submission.results), previous_state=state
                    )
                except RunError:
                    pass  # The pending batch is no longer outstanding in this checkpoint.
                else:
                    deferred_requests = previous.deferred_requests
                    pending_run_id = previous.pending_run_id
        record = InlineSubagentState(
            child_instance_id=child_instance_id,
            subagent_name=child.declaration.name,
            child_definition_id=child.definition.definition_id,
            state=_without_borrowed_environment_state(state),
            deferred_requests=deferred_requests,
            pending_run_id=pending_run_id,
        )
        async with self._state_lock:
            children = dict(self._state.children)
            children[child_instance_id] = record
            candidate = InlineSubagentCollectionState(children=children)
            _validate_inline_subagent_state(candidate, self._context)
            await self._context.state.write(
                SUBAGENT_CAPABILITY_ID,
                candidate,
                version=_INLINE_SUBAGENT_STATE_VERSION,
            )
            self._state = candidate

    def _allocate_child_id(self, subagent: str) -> str:
        if _SUBAGENT_NAME_PATTERN.fullmatch(subagent) is None:
            raise ToolFailed("Subagent name cannot be represented as an inline child ID.")
        occupied = {*self._state.children, *self._active_children}
        start = int.from_bytes(secrets.token_bytes(2), "big")
        for offset in range(1 << 16):
            candidate = f"{subagent}-{(start + offset) % (1 << 16):04x}"
            if candidate not in occupied:
                return candidate
        raise ToolFailed("Inline child ID space is exhausted.")

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        from a13n_harness.capabilities.subagents import SUBAGENT_CAPABILITY_ID

        if ctx.deps is not self._context:
            raise DefinitionError(
                "Delegation Toolset cannot cross logical runs.",
                code="capability_scope_invalid",
            )
        owner = ctx.capabilities.get(SUBAGENT_CAPABILITY_ID)
        if owner is not self._owner:
            raise DefinitionError(
                "The finalized Delegation owner has an incompatible identity.",
                code="capability_scope_invalid",
            )


class _BorrowedEnvironmentRuntime(EnvironmentRuntime):
    """Single-use child runtime that borrows the parent's already entered facade."""

    def __init__(self, environment: BoundEnvironment) -> None:
        self._environment = environment
        self._used = False
        self._active = asyncio.Event()
        self._closed = False

    @asynccontextmanager
    async def bind(
        self,
        *,
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        host_refs,
    ) -> AsyncGenerator[BoundEnvironment]:
        del thread_id, run_id, instance, host_refs
        if self._used:
            raise EnvironmentError("Borrowed Environment runtime is single-use.", code="environment_runtime_reused")
        self._used = True
        try:
            yield self._environment
        finally:
            self._closed = True
            self._active.set()

    async def wait_until_active(self) -> None:
        await self._active.wait()
        if self._closed:
            raise EnvironmentError("Borrowed Environment runtime is closed.", code="run_not_active")

    async def mount(
        self,
        name: str,
        mount: EnvironmentEntry | EnvironmentRuntimeMount,
        *,
        make_default: bool = False,
    ) -> EnvironmentChange:
        del name, mount, make_default
        raise EnvironmentError("Inline children cannot mutate Environment mounts.", code="environment_denied")

    async def replace(self, name: str, mount: EnvironmentEntry | EnvironmentRuntimeMount) -> EnvironmentChange:
        del name, mount
        raise EnvironmentError("Inline children cannot mutate Environment mounts.", code="environment_denied")

    async def unmount(self, name: str) -> EnvironmentChange:
        del name
        raise EnvironmentError("Inline children cannot mutate Environment mounts.", code="environment_denied")

    async def set_default(self, name: str | None) -> EnvironmentChange:
        del name
        raise EnvironmentError("Inline children cannot mutate Environment mounts.", code="environment_denied")

    async def _activate(self) -> None:
        if self._closed:
            raise EnvironmentError("Borrowed Environment runtime is closed.", code="run_not_active")
        self._active.set()

    def _begin_close(self) -> None:
        self._closed = True
        self._active.set()


def _create_inline_child_bindings(
    ctx: RunContext[AgentContext],
    child: BuiltSubagent,
    child_instance_id: str,
) -> RunBindings:
    from a13n_harness.builder import derive_child_identity
    from a13n_harness.tools.policy import INVOCATION_POLICY_CAPABILITY_ID, InvocationPolicyCapability

    parent = ctx.deps
    identity = derive_child_identity(
        parent.identity,
        child.definition.definition_id,
        child.declaration.identity,
    )
    invocation_policy = ctx.capabilities.get(INVOCATION_POLICY_CAPABILITY_ID)
    if invocation_policy is not None and type(invocation_policy) is not InvocationPolicyCapability:
        raise DefinitionError(
            "Inline delegation found an incompatible invocation policy.",
            code="capability_type_mismatch",
        )
    bindings = RunBindings(
        instance=AgentInstanceContext(
            identity=identity,
            agent_instance_id=f"agent-{secrets.token_urlsafe(12)}",
            parent_agent_instance_id=parent.instance.agent_instance_id,
            delegation_id=child_instance_id,
            actor=parent.instance.actor,
            host_refs=parent.instance.host_refs,
        ),
        environment=_BorrowedEnvironmentRuntime(parent.environment),
        tool_result_directory=parent.tool_result_directory,
        model_resolver=parent.model_resolver,
        toolset_instructions=parent._toolset_instructions_override,
        deferred_tools_supported=parent.deferred_tools_supported,
        capabilities=(invocation_policy,) if invocation_policy is not None else (),
        metadata=parent.metadata,
    )
    factory = child.declaration.run_bindings_factory
    if factory is None:
        return bindings
    resolved = factory(bindings)
    if not isinstance(resolved, RunBindings):
        raise DefinitionError("Child run bindings factory must return RunBindings.", code="subagent_binding_invalid")
    if resolved.instance != bindings.instance or resolved.environment is not bindings.environment:
        raise DefinitionError(
            "Child run bindings factory cannot replace the child instance or borrowed Environment.",
            code="subagent_binding_invalid",
        )
    if not bindings.deferred_tools_supported and resolved.deferred_tools_supported:
        raise DefinitionError(
            "Child bindings cannot enable unsupported deferred tools.", code="subagent_binding_invalid"
        )
    if invocation_policy is not None and not any(item is invocation_policy for item in resolved.capabilities):
        raise DefinitionError(
            "Child run bindings factory cannot remove or replace the inherited invocation policy.",
            code="subagent_binding_invalid",
        )
    return resolved


def _without_borrowed_environment_state(state: HarnessState) -> HarnessState:
    return HarnessState(
        schema_version=state.schema_version,
        thread_id=state.thread_id,
        message_history=state.message_history,
        agent_context_state=state.agent_context_state,
        environment_states={},
    )


def _build_child_input(
    ctx: RunContext[AgentContext],
    child: BuiltSubagent,
    task: JsonValue,
) -> str:
    policy = child.declaration.context
    payload: dict[str, JsonValue] = {
        "delegated_task": _JSON_ADAPTER.validate_python(task),
    }
    if policy.include_task and isinstance(ctx.prompt, str) and ctx.prompt:
        payload["parent_task"] = ctx.prompt
    if policy.history == "summary":
        summary = restored_history_summary(ctx.messages)
        if summary:
            payload["parent_history_summary"] = summary
    elif policy.history == "selected":
        payload["parent_history"] = ModelMessagesTypeAdapter.dump_json(ctx.messages).decode("utf-8")
    return dump_json_bytes(payload, sort_keys=True).decode("utf-8")


async def _finalize_child_bindings(
    ctx: RunContext[AgentContext],
    child: BuiltSubagent,
    bindings: RunBindings,
) -> RunBindings:
    from a13n_harness.pricing import MODEL_COST_CAPABILITY_ID, AbstractModelCostCapability

    inherited = ctx.deps._inherited_model_cost
    if inherited is None:
        selected = ctx.capabilities.get(MODEL_COST_CAPABILITY_ID)
        if not isinstance(selected, AbstractModelCostCapability):
            raise DefinitionError(
                "Inline delegation requires one finalized parent model-cost Capability.",
                code="capability_scope_invalid",
            )
        inherited = selected
    bindings = replace(
        bindings,
        _inherited_model_cost=inherited,
        toolset_instructions=(
            bindings.toolset_instructions
            if bindings.toolset_instructions is not None
            else ctx.deps._toolset_instructions_override
        ),
    )
    return await _finalize_task_bindings(ctx, child, bindings)


async def _finalize_task_bindings(
    ctx: RunContext[AgentContext],
    child: BuiltSubagent,
    bindings: RunBindings,
) -> RunBindings:
    from a13n_harness.capabilities.working_state import (
        WORKING_STATE_CAPABILITY_ID,
        WorkingStateCapability,
    )

    child_working_state = _definition_working_state(child)
    if child_working_state is None or not child_working_state.configuration.tasks_enabled:
        return bindings
    if child_working_state.configuration.task_mode == "provider":
        raise DefinitionError(
            "Inline provider-mode tasks require Host-owned async execution.",
            code="task_state_binding_missing",
        )
    if child.declaration.context.task_state == "isolated":
        return bindings

    parent_owner = ctx.capabilities.get(WORKING_STATE_CAPABILITY_ID)
    if not isinstance(parent_owner, WorkingStateCapability) or not parent_owner.configuration.tasks_enabled:
        raise DefinitionError(
            "Shared child tasks require an active parent WorkingStateCapability.",
            code="task_state_binding_missing",
        )
    if parent_owner.configuration.task_mode != "embedded":
        raise DefinitionError(
            "Shared inline child tasks require embedded parent Working State.",
            code="task_state_mode_mismatch",
        )
    borrowed = await parent_owner.bind_inline_child_task_state(
        ctx,
        owner=bindings.instance.agent_instance_id,
    )
    if borrowed is None:
        raise DefinitionError(
            "The parent embedded task view is unavailable.",
            code="task_state_binding_missing",
        )
    if bindings.task_state is not None:
        raise DefinitionError(
            "Shared child tasks already have a task-state binding.", code="task_state_binding_conflict"
        )
    return replace(bindings, task_state=borrowed)


def _definition_working_state(child: BuiltSubagent):
    from a13n_harness.capabilities.working_state import WorkingStateCapability

    leaves: list[AbstractCapability[AgentContext]] = []
    for capability in child.definition.capabilities:
        capability.apply(leaves.append)
    matches = [capability for capability in leaves if type(capability) is WorkingStateCapability]
    if len(matches) > 1:
        raise DefinitionError(
            "Child definition contains duplicate Working State owners.",
            code="capability_id_duplicate",
        )
    return matches[0] if matches else None


def _project_output(value: Any) -> JsonValue:
    try:
        projected = _JSON_ADAPTER.dump_python(value, mode="json", warnings="error")
        return _JSON_ADAPTER.validate_python(projected, strict=True)
    except Exception as exc:
        raise ToolFailed("Inline child output is not JSON-compatible.") from exc


def _child_failure_message(result: HarnessRunResult[Any], child_instance_id: str) -> str:
    if result.status == "failed":
        failure = result.failure
        code = failure.code if failure is not None else "child_failed"
    else:
        code = f"child_{result.status}"
    return f"Inline child {child_instance_id} did not complete successfully ({code})."


async def _emit_delegation_event(
    ctx: RunContext[AgentContext],
    action: Literal["started", "completed", "failed"],
    child_instance_id: str,
    subagent: str,
    status: str,
    *,
    invocation_id: str,
    child_run_id: str | None = None,
) -> None:
    await emit_harness_event(
        ctx.deps.events,
        kind="delegation",
        payload=InlineDelegationPayload(
            invocation_id=invocation_id,
            action=action,
            child_instance_id=child_instance_id,
            subagent=subagent,
            status=status,
            parent_run_id=ctx.deps.run_id,
            parent_agent_instance_id=ctx.deps.instance.agent_instance_id,
            parent_tool_call_id=ctx.tool_call_id,
            child_run_id=child_run_id,
        ),
    )


async def _emit_delegation_event_best_effort(
    ctx: RunContext[AgentContext],
    action: Literal["started", "completed", "failed"],
    child_instance_id: str,
    subagent: str,
    status: str,
    *,
    invocation_id: str,
    child_run_id: str | None = None,
) -> None:
    try:
        await _emit_delegation_event(
            ctx,
            action,
            child_instance_id,
            subagent,
            status,
            invocation_id=invocation_id,
            child_run_id=child_run_id,
        )
    except (HarnessError, ValueError):
        pass


__all__ = ["DelegateResult", "DelegationToolset"]
