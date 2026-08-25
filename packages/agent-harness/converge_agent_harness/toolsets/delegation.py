"""Model-facing inline delegation with complete child execution semantics."""

from __future__ import annotations

import asyncio
import re
import secrets
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, UserPromptPart
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import UsageLimits

from converge_agent_harness._json import dump_json_bytes
from converge_agent_harness.context import AgentContext, BuiltSubagent, RunBindings
from converge_agent_harness.errors import DefinitionError, HarnessError, RunError, StateError
from converge_agent_harness.events import (
    HarnessEvent,
    HarnessRunResultEvent,
    InlineDelegationPayload,
    emit_harness_event,
)
from converge_agent_harness.input import RunInputValue
from converge_agent_harness.result import HarnessRunResult
from converge_agent_harness.state import HarnessState
from converge_agent_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy

if TYPE_CHECKING:
    from converge_agent_harness.capabilities.delegation import (
        DelegationConfiguration,
        DelegationState,
    )

_SUBAGENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_JSON_ADAPTER = TypeAdapter(JsonValue)
_LIMIT_FIELDS = (
    "cost_limit",
    "request_limit",
    "tool_calls_limit",
    "input_tokens_limit",
    "output_tokens_limit",
    "total_tokens_limit",
    "per_request_input_tokens_limit",
)


class DelegateResult(BaseModel):
    """Bounded ordinary result returned to the parent model."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    child_instance_id: str
    subagent: str
    output: JsonValue


class DelegationToolset:
    """Own inline child dispatch, state advancement, event forwarding, and result projection."""

    def __init__(
        self,
        *,
        owner: AbstractCapability[AgentContext],
        context: AgentContext,
        configuration: DelegationConfiguration,
        state: DelegationState,
    ) -> None:
        self._owner = owner
        self._context = context
        self._configuration = configuration.model_copy(deep=True)
        self._state = state.model_copy(deep=True)
        self._state_lock = asyncio.Lock()
        self._active_lock = asyncio.Lock()
        self._active_children: set[str] = set()
        self._active_new_children: set[str] = set()

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        tool = HarnessTool(
            self.delegate,
            harness_metadata=HarnessToolMetadata(
                tool_id="delegation.inline",
                effects=frozenset({"execute"}),
                credential_audiences=(),
                idempotency="none",
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=min(256 * 1024, self._configuration.max_output_bytes),
                    max_output_bytes=self._configuration.max_output_bytes,
                    overflow="fail",
                    redact=True,
                ),
            ),
            name="delegate",
            description=(
                "Run one declared subagent inline and wait for its result. Omit child_instance_id to create a new "
                "child, or supply a previously returned ID to continue that exact child."
            ),
        )
        return FunctionToolset(tools=[tool], id="converge-delegation-tools")

    async def delegate(
        self,
        ctx: RunContext[AgentContext],
        subagent: Annotated[str, Field(description="Stable declared subagent name")],
        task: Annotated[JsonValue, Field(description="Bounded delegated task")],
        child_instance_id: Annotated[
            str | None,
            Field(description="Existing inline child ID to continue; omit to create a child"),
        ] = None,
    ) -> JsonValue:
        self._require_context(ctx)
        invocation_id = f"delegation-{secrets.token_urlsafe(9)}"
        try:
            child = self._context.subagents.require(subagent)
        except KeyError as exc:
            raise ToolFailed("Unknown inline subagent.") from exc

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
                reserved_id = child_instance_id
                selected_state = record.state.model_copy(deep=True)
            else:
                if len(self._state.children) + len(self._active_new_children) >= self._configuration.max_children:
                    raise ToolFailed("Inline child state limit reached.")
                reserved_id = self._allocate_child_id(subagent)
                baseline = HarnessState()
                selected_state = baseline.model_copy(deep=True)
                self._active_new_children.add(reserved_id)
            self._active_children.add(reserved_id)

        try:
            try:
                child_input = _build_child_input(ctx, child, task, self._configuration)
                limits = _intersect_usage_limits(
                    ctx.usage_limits,
                    child.declaration.usage_limits,
                    self._require_binding(ctx).usage_limits,
                )
                bindings = await self._require_binding(ctx).bind_inline(
                    child,
                    child_input,
                    reserved_id,
                    continuation,
                    limits,
                )
                _validate_child_lineage(self._context, bindings, reserved_id)
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
                )
            except asyncio.CancelledError:
                raise
            except ToolFailed as exc:
                await _emit_delegation_event(
                    ctx,
                    "failed",
                    reserved_id,
                    subagent,
                    "dispatch_rejected",
                    invocation_id=invocation_id,
                )
                if not continuation and baseline is not None:
                    await self._store_child_or_fail(
                        ctx,
                        reserved_id,
                        child,
                        baseline,
                        subagent=subagent,
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
                output = _project_output(result.output_or_raise(), self._configuration.max_output_bytes)
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
                child_instance_id=reserved_id,
                subagent=subagent,
                output=output,
            ).model_dump(mode="json")
        finally:
            async with self._active_lock:
                self._active_children.discard(reserved_id)
                self._active_new_children.discard(reserved_id)

    async def _run_child(
        self,
        ctx: RunContext[AgentContext],
        child: BuiltSubagent,
        child_input: RunInputValue,
        child_instance_id: str,
        invocation_id: str,
        bindings: RunBindings,
        previous_state: HarnessState | None,
        limits: UsageLimits | None,
    ) -> HarnessRunResult[Any]:
        stream = child.executable.stream(
            child_input,
            bindings=bindings,
            previous_state=previous_state,
            usage=ctx.usage,
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
    ) -> None:
        try:
            await self._store_child(child_instance_id, child, state)
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
    ) -> None:
        from converge_agent_harness.capabilities.delegation import (
            _DELEGATION_STATE_VERSION,
            DELEGATION_CAPABILITY_ID,
            DelegationState,
            InlineSubagentState,
            _validate_delegation_state,
        )

        record = InlineSubagentState(
            child_instance_id=child_instance_id,
            subagent_name=child.declaration.name,
            child_definition_id=child.definition.definition_id,
            state=state,
        )
        async with self._state_lock:
            children = dict(self._state.children)
            children[child_instance_id] = record
            candidate = DelegationState(children=children)
            _validate_delegation_state(candidate, self._context, self._configuration)
            await self._context.state.write(
                DELEGATION_CAPABILITY_ID,
                candidate,
                version=_DELEGATION_STATE_VERSION,
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
        from converge_agent_harness.capabilities.delegation import DELEGATION_CAPABILITY_ID

        if ctx.deps is not self._context:
            raise DefinitionError(
                "Delegation Toolset cannot cross logical runs.",
                code="capability_scope_invalid",
            )
        owner = ctx.capabilities.get(DELEGATION_CAPABILITY_ID)
        if owner is not self._owner:
            raise DefinitionError(
                "The finalized Delegation owner has an incompatible identity.",
                code="capability_scope_invalid",
            )

    def _require_binding(self, ctx: RunContext[AgentContext]):
        from converge_agent_harness.capabilities.delegation import (
            DELEGATION_RUN_CAPABILITY_ID,
            DelegationRunCapability,
        )

        self._require_context(ctx)
        attachment = ctx.capabilities.get(DELEGATION_RUN_CAPABILITY_ID)
        if type(attachment) is not DelegationRunCapability:
            raise DefinitionError(
                "DelegationCapability requires one fresh DelegationRunCapability.",
                code="delegation_binding_missing",
            )
        if DELEGATION_RUN_CAPABILITY_ID not in ctx.deps._capability_provenance.run_ids:
            raise DefinitionError(
                "DelegationRunCapability must originate from RunBindings.",
                code="capability_scope_invalid",
            )
        return attachment


def _build_child_input(
    ctx: RunContext[AgentContext],
    child: BuiltSubagent,
    task: JsonValue,
    configuration: DelegationConfiguration,
) -> str:
    policy = child.declaration.context
    payload: dict[str, JsonValue] = {
        "delegated_task": _JSON_ADAPTER.validate_python(task),
    }
    if policy.include_task and isinstance(ctx.prompt, str) and ctx.prompt:
        payload["parent_task"] = ctx.prompt
    if policy.history == "summary":
        summary = _restored_summary_projection(ctx.messages)
        if summary:
            payload["parent_history_summary"] = summary
    elif policy.history == "selected":
        selected = list(ctx.messages[-configuration.selected_history_messages :])
        payload["parent_history"] = ModelMessagesTypeAdapter.dump_json(selected).decode("utf-8")
    encoded = dump_json_bytes(payload, sort_keys=True)
    if len(encoded) > configuration.max_input_bytes:
        raise ToolFailed("Delegated context exceeds the configured input limit.")
    return encoded.decode("utf-8")


def _restored_summary_projection(messages: Sequence[ModelMessage]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, ModelRequest) or not message.metadata:
            continue
        if message.metadata.get("converge.restored-boundary") != "1":
            continue
        values = [
            part.content
            for part in message.parts
            if isinstance(part, UserPromptPart)
            and isinstance(part.content, str)
            and not part.content.startswith("<context-restored>")
            and not part.content.startswith("<system-reminder>")
        ]
        return "\n\n".join(values) if values else None
    return None


def _intersect_usage_limits(*values: UsageLimits | None) -> UsageLimits | None:
    present = tuple(value for value in values if value is not None)
    if not present:
        return None
    fields: dict[str, Any] = {}
    for name in _LIMIT_FIELDS:
        ceilings = [getattr(value, name) for value in present if getattr(value, name) is not None]
        fields[name] = min(ceilings) if ceilings else None
    fields["count_tokens_before_request"] = any(value.count_tokens_before_request for value in present)
    return UsageLimits(**fields)


def _validate_child_lineage(parent: AgentContext, bindings: RunBindings, child_instance_id: str) -> None:
    instance = bindings.instance
    if (
        instance.agent_instance_id == parent.instance.agent_instance_id
        or instance.parent_agent_instance_id != parent.instance.agent_instance_id
        or instance.delegation_id != child_instance_id
    ):
        raise DefinitionError(
            "Inline child bindings do not reproduce the requested parent lineage.",
            code="delegation_lineage_invalid",
        )


async def _finalize_child_bindings(
    ctx: RunContext[AgentContext],
    child: BuiltSubagent,
    bindings: RunBindings,
) -> RunBindings:
    bindings = _finalize_usage_bindings(ctx, bindings)
    return await _finalize_task_bindings(ctx, child, bindings)


def _finalize_usage_bindings(
    ctx: RunContext[AgentContext],
    bindings: RunBindings,
) -> RunBindings:
    from converge_agent_harness.usage import (
        MODEL_COST_RUN_CAPABILITY_ID,
        ModelCostRunCapability,
    )

    parent_attachment = ctx.capabilities.get(MODEL_COST_RUN_CAPABILITY_ID)
    attachments = [capability for capability in bindings.capabilities if capability.id == MODEL_COST_RUN_CAPABILITY_ID]
    if len(attachments) > 1:
        raise DefinitionError(
            "Inline child bindings contain duplicate model-cost attachments.",
            code="model_cost_binding_invalid",
        )
    child_attachment = attachments[0] if attachments else None
    if child_attachment is not None and type(child_attachment) is not ModelCostRunCapability:
        raise DefinitionError(
            "Inline child model-cost attachment has an incompatible type.",
            code="capability_type_mismatch",
        )
    if parent_attachment is None:
        if child_attachment is not None:
            raise DefinitionError(
                "Inline child model-cost selection must match its parent run.",
                code="model_cost_binding_invalid",
            )
        return bindings
    if type(parent_attachment) is not ModelCostRunCapability:
        raise DefinitionError(
            "Parent model-cost attachment has an incompatible type.",
            code="capability_type_mismatch",
        )
    if child_attachment is not None:
        if child_attachment.calculator is not parent_attachment.calculator:
            raise DefinitionError(
                "Inline child model-cost selection must match its parent run.",
                code="model_cost_binding_invalid",
            )
        return bindings
    return RunBindings(
        instance=bindings.instance,
        environment=bindings.environment,
        model_binding=bindings.model_binding,
        capabilities=(
            *bindings.capabilities,
            ModelCostRunCapability(calculator=parent_attachment.calculator),
        ),
        metadata=bindings.metadata,
    )


async def _finalize_task_bindings(
    ctx: RunContext[AgentContext],
    child: BuiltSubagent,
    bindings: RunBindings,
) -> RunBindings:
    from converge_agent_harness.capabilities.working_state import (
        TASK_STATE_RUN_CAPABILITY_ID,
        WORKING_STATE_CAPABILITY_ID,
        TaskStateRunCapability,
        WorkingStateCapability,
    )

    child_working_state = _definition_working_state(child)
    attachments = [capability for capability in bindings.capabilities if capability.id == TASK_STATE_RUN_CAPABILITY_ID]
    if len(attachments) > 1:
        raise DefinitionError(
            "Inline child bindings contain duplicate task-state attachments.",
            code="task_state_binding_invalid",
        )
    attachment = attachments[0] if attachments else None
    if attachment is not None and type(attachment) is not TaskStateRunCapability:
        raise DefinitionError(
            "Inline child task-state attachment has an incompatible type.",
            code="task_state_type_mismatch",
        )
    if child_working_state is None or not child_working_state.configuration.tasks_enabled:
        if attachment is not None:
            raise DefinitionError(
                "A task-state attachment requires a child WorkingStateCapability.",
                code="task_state_owner_missing",
            )
        return bindings

    policy = child.declaration.context
    if policy.task_state == "isolated":
        if type(attachment) is TaskStateRunCapability and attachment.source == "embedded_borrowed":
            raise DefinitionError(
                "An isolated child cannot borrow its parent task scope.",
                code="task_state_borrow_forbidden",
            )
        if child_working_state.configuration.task_mode == "provider" and (
            type(attachment) is not TaskStateRunCapability or attachment.source != "provider"
        ):
            raise DefinitionError(
                "An isolated provider-mode child requires a fresh provider task binding.",
                code="task_state_binding_missing",
            )
        if child_working_state.configuration.task_mode == "embedded" and attachment is not None:
            raise DefinitionError(
                "An isolated embedded child cannot receive a task-state attachment.",
                code="task_state_mode_mismatch",
            )
        return bindings

    parent_owner = ctx.capabilities.get(WORKING_STATE_CAPABILITY_ID)
    if not isinstance(parent_owner, WorkingStateCapability) or not parent_owner.configuration.tasks_enabled:
        raise DefinitionError(
            "Shared child tasks require an active parent WorkingStateCapability.",
            code="task_state_binding_missing",
        )
    if parent_owner.configuration.task_mode != child_working_state.configuration.task_mode:
        raise DefinitionError(
            "Parent and child Working State task modes are incompatible.",
            code="task_state_mode_mismatch",
        )
    if child_working_state.configuration.task_mode == "provider":
        if type(attachment) is not TaskStateRunCapability or attachment.source != "provider":
            raise DefinitionError(
                "Shared provider-mode tasks require a fresh provider child binding.",
                code="task_state_binding_missing",
            )
        return bindings
    if attachment is not None:
        raise DefinitionError(
            "The binder cannot replace the parent-owned embedded task view.",
            code="task_state_binding_invalid",
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
    return RunBindings(
        instance=bindings.instance,
        environment=bindings.environment,
        model_binding=bindings.model_binding,
        capabilities=(*bindings.capabilities, borrowed),
        metadata=bindings.metadata,
    )


def _definition_working_state(child: BuiltSubagent):
    from converge_agent_harness.capabilities.working_state import WorkingStateCapability

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


def _project_output(value: Any, max_bytes: int) -> JsonValue:
    try:
        projected = _JSON_ADAPTER.dump_python(value, mode="json", warnings="error")
        validated = _JSON_ADAPTER.validate_python(projected, strict=True)
        encoded = dump_json_bytes(validated, sort_keys=True)
    except Exception as exc:
        raise ToolFailed("Inline child output is not JSON-compatible.") from exc
    if len(encoded) > max_bytes:
        raise ToolFailed("Inline child output exceeds the configured result limit.")
    return validated


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
) -> None:
    try:
        await _emit_delegation_event(
            ctx,
            action,
            child_instance_id,
            subagent,
            status,
            invocation_id=invocation_id,
        )
    except (HarnessError, ValueError):
        pass


__all__ = ["DelegateResult", "DelegationToolset"]
