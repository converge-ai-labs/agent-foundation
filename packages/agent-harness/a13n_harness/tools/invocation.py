"""Outer execution boundary over Pydantic AI's assembled function Toolset."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
from collections.abc import AsyncIterable, Iterator, Mapping
from contextlib import AsyncExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai import RunContext, TextContent, ToolReturn
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.exceptions import ApprovalRequired, ToolFailed
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset

from a13n_harness._json import (
    dump_json_bytes,
    dump_json_text,
    is_sensitive_key,
    redact_bearer,
    redact_json,
    require_finite_json,
)
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.events import HarnessExtensionEvent
from a13n_harness.tools._output import (
    FINAL_TOOL_OUTPUT_HARD_CHARS,
    is_acknowledged_tool_output,
)
from a13n_harness.tools.deferred import managed_approval_tool_id
from a13n_harness.tools.metadata import (
    HARNESS_TOOL_METADATA_KEY,
    CanonicalResource,
    HarnessToolMetadata,
    ToolOutputPolicy,
    normalize_harness_tool_metadata,
)
from a13n_harness.tools.policy import (
    INVOCATION_POLICY_CAPABILITY_ID,
    CredentialLease,
    InvocationGrantRef,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    InvocationScope,
    ToolInvocationContext,
)

TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID = "a13n.tool-execution-boundary"
MAX_ARGUMENT_BYTES = 64 * 1024
_UNMANAGED_OUTPUT_POLICY = ToolOutputPolicy(
    max_inline_bytes=256 * 1024,
    max_output_bytes=4 * 1024 * 1024,
    overflow="truncate",
    redact=True,
)

_ANY_ADAPTER = TypeAdapter(Any)
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_INVOCATION_SCOPE: contextvars.ContextVar[InvocationScope | None] = contextvars.ContextVar(
    "a13n_harness_invocation_scope",
    default=None,
)
_TOOL_EXECUTION_DISABLED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "a13n_harness_tool_execution_disabled",
    default=False,
)


@dataclass(frozen=True, slots=True)
class _PreparedInvocation:
    context: ToolInvocationContext
    typed_arguments: dict[str, Any]


class ManagedToolProviderError(Exception):
    """Typed provider failure carrying only retry and outcome evidence."""

    def __init__(
        self,
        message: str = "Managed tool provider failed.",
        *,
        retryable: bool = False,
        outcome_known: bool = True,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.outcome_known = outcome_known


def current_invocation_scope() -> InvocationScope:
    """Return task-local managed authority for a first-party dispatch adapter."""
    scope = _INVOCATION_SCOPE.get()
    if scope is None:
        raise RuntimeError("No managed tool invocation is active in this task")
    return scope


@contextmanager
def disabled_tool_execution() -> Iterator[None]:
    """Disable function-tool dispatch in the current async execution context."""
    token = _TOOL_EXECUTION_DISABLED.set(True)
    try:
        yield
    finally:
        _TOOL_EXECUTION_DISABLED.reset(token)


@dataclass
class ToolExecutionBoundaryCapability(AbstractCapability[AgentContext]):
    """Mandatory code-owned outer wrapper for every non-output Toolset."""

    id: str | None = TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID

    def __post_init__(self) -> None:
        if self.id != TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID:
            raise ValueError(f"ToolExecutionBoundaryCapability.id must be {TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID!r}")

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(AbstractCapability,))

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return ToolExecutionBoundaryToolset(toolset)

    async def wrap_run_event_stream(
        self,
        ctx: RunContext[AgentContext],
        *,
        stream: AsyncIterable[Any],
    ) -> AsyncIterable[Any]:
        _validate_finalized_capability_provenance(ctx)
        async for event in stream:
            yield event


@dataclass
class ToolExecutionBoundaryToolset(WrapperToolset[AgentContext]):
    """Normalize definitions, authorize managed calls, and bound function results."""

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        _validate_final_toolset_wrapper_order(self.wrapped)
        _validate_client_run_attachment(ctx)
        tools = await self.wrapped.get_tools(ctx)
        policy = _resolve_policy(ctx)
        managed_ids: dict[str, str] = {}
        normalized: dict[str, ToolsetTool[AgentContext]] = {}

        for name, tool in tools.items():
            tool_def = tool.tool_def
            metadata_values = tool_def.metadata or {}
            reserved_present = HARNESS_TOOL_METADATA_KEY in metadata_values

            if tool_def.kind == "external":
                if reserved_present:
                    raise DefinitionError(
                        "External tools cannot carry managed function-tool metadata.",
                        code="tool_metadata_kind_invalid",
                    )
                _validate_client_marker(tool_def.name, metadata_values)
                normalized[name] = tool
                continue

            if tool_def.kind not in {"function", "unapproved"}:
                normalized[name] = tool
                continue

            if not reserved_present:
                if policy is not None and policy.strict_managed_tools:
                    raise DefinitionError(
                        "The current invocation policy requires every function tool to be managed.",
                        code="unmanaged_tool_rejected",
                        details={"tool_name": tool_def.name},
                    )
                normalized[name] = tool
                continue

            managed = normalize_harness_tool_metadata(metadata_values[HARNESS_TOOL_METADATA_KEY])
            if previous := managed_ids.get(managed.tool_id):
                raise DefinitionError(
                    "Managed tool_id values must be unique in the assembled run surface.",
                    code="managed_tool_id_duplicate",
                    details={"tool_id": managed.tool_id, "tool_name": tool_def.name, "other_tool_name": previous},
                )
            managed_ids[managed.tool_id] = tool_def.name
            copied_metadata = dict(metadata_values)
            copied_metadata[HARNESS_TOOL_METADATA_KEY] = managed
            normalized[name] = replace(tool, tool_def=replace(tool_def, metadata=copied_metadata))

        ctx.deps._record_managed_tool_surface({tool_name: tool_id for tool_id, tool_name in managed_ids.items()})
        _validate_resume_surface(ctx, normalized)
        return normalized

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentContext],
        tool: ToolsetTool[AgentContext],
    ) -> Any:
        if _TOOL_EXECUTION_DISABLED.get():
            raise ToolFailed("Tool execution is disabled during context compaction.")
        tool_def = tool.tool_def
        if tool_def.kind == "external":
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        metadata_values = tool_def.metadata or {}
        raw_metadata = metadata_values.get(HARNESS_TOOL_METADATA_KEY)
        if raw_metadata is None:
            result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
            return await _apply_result_policy(
                result,
                _UNMANAGED_OUTPUT_POLICY,
                context=ctx.deps,
                reject_non_json=False,
            )
        managed = normalize_harness_tool_metadata(raw_metadata)
        policy = _resolve_policy(ctx)
        if policy is None:
            await _emit(ctx, managed, "denied", reason="policy_unavailable")
            raise ToolFailed("Managed tool authorization is unavailable.")

        try:
            prepared = await _prepare_invocation(ctx, tool_def.name, tool_def.toolset_id, tool_args, managed)
        except ToolFailed:
            await _emit(ctx, managed, "preparation_failed")
            raise
        invocation = prepared.context
        await _emit(ctx, managed, "prepared", invocation_id=invocation.invocation_id)
        if ctx.tool_call_approved and policy.approval_verifier is not None:
            approval_metadata = ctx.tool_call_metadata
            if approval_metadata is None:
                approval_metadata = {}
            if not isinstance(approval_metadata, Mapping) or not all(isinstance(key, str) for key in approval_metadata):
                raise ToolFailed("Managed tool approval metadata is invalid.")
            try:
                verified = await policy.approval_verifier.verify(
                    invocation,
                    cast(Mapping[str, JsonValue], approval_metadata),
                    context=ctx.deps,
                )
            except Exception as exc:
                raise ToolFailed("Managed tool approval could not be verified.") from exc
            if verified is not True:
                await _emit(ctx, managed, "denied", invocation_id=invocation.invocation_id)
                raise ToolFailed("Managed tool approval is no longer valid.")
        await _require_policy_allow(
            ctx,
            policy,
            invocation,
            managed,
            approval_satisfied=ctx.tool_call_approved,
        )
        await _emit(ctx, managed, "authorized", invocation_id=invocation.invocation_id)
        leases: list[CredentialLease] = []
        lease_stack = AsyncExitStack()
        grant = None
        try:
            if policy.credential_broker is not None:
                for audience in managed.credential_audiences:
                    try:
                        lease = await policy.credential_broker.acquire(audience, invocation, context=ctx.deps)
                    except (DefinitionError, ToolFailed):
                        raise
                    except Exception as exc:
                        raise ToolFailed("A required managed tool credential is unavailable.") from exc
                    if not isinstance(lease, CredentialLease):
                        raise DefinitionError(
                            "Credential broker returned an invalid lease.",
                            code="credential_lease_invalid",
                        )
                    lease_stack.push_async_callback(lease.close)
                    if lease.audience != audience:
                        raise DefinitionError(
                            "Credential broker returned a lease for the wrong audience.",
                            code="credential_lease_invalid",
                        )
                    leases.append(lease)
            elif managed.credential_audiences:
                raise ToolFailed("A required managed tool credential is unavailable.")

            if policy.grant_broker is not None:
                try:
                    grant = await policy.grant_broker.issue(invocation, managed, context=ctx.deps)
                except (DefinitionError, ToolFailed):
                    raise
                except Exception as exc:
                    raise ToolFailed("A managed tool invocation grant is unavailable.") from exc
                if grant is not None and not isinstance(grant, InvocationGrantRef):
                    raise DefinitionError(
                        "Invocation grant broker returned an invalid value.",
                        code="invocation_grant_invalid",
                    )
                if grant is not None and grant.expires_at <= datetime.now(UTC):
                    raise ToolFailed("A managed tool invocation grant is unavailable.")

            scope = InvocationScope(
                invocation=invocation,
                credentials={lease.audience: lease.value for lease in leases},
                grant=grant,
            )
            token = _INVOCATION_SCOPE.set(scope)
            try:
                result = await self._dispatch(
                    name,
                    prepared.typed_arguments,
                    ctx,
                    tool,
                    managed,
                    invocation,
                    policy,
                )
            finally:
                _INVOCATION_SCOPE.reset(token)
            try:
                safe_result = await _apply_result_policy(
                    result,
                    managed,
                    context=ctx.deps,
                )
            except ToolFailed:
                await _emit(ctx, managed, "result_rejected", invocation_id=invocation.invocation_id)
                raise
            await _emit(ctx, managed, "completed", invocation_id=invocation.invocation_id)
            return safe_result
        finally:
            await lease_stack.aclose()

    async def _dispatch(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentContext],
        tool: ToolsetTool[AgentContext],
        metadata: HarnessToolMetadata,
        invocation: ToolInvocationContext,
        policy: InvocationPolicyCapability,
    ) -> Any:
        replay_safe = metadata.idempotency in {"read_only", "provider_key"}
        attempts = 1 + (policy.max_dispatch_retries if replay_safe else 0)
        for attempt in range(attempts):
            try:
                await _emit(
                    ctx,
                    metadata,
                    "dispatching",
                    invocation_id=invocation.invocation_id,
                    attempt=attempt,
                )
                return await self.wrapped.call_tool(name, deepcopy(tool_args), ctx, tool)
            except asyncio.CancelledError:
                await _emit_best_effort(
                    ctx,
                    metadata,
                    "unknown_outcome",
                    invocation_id=invocation.invocation_id,
                )
                raise
            except TimeoutError as exc:
                await _emit(ctx, metadata, "unknown_outcome", invocation_id=invocation.invocation_id)
                raise ToolFailed("Managed tool outcome is unknown and requires reconciliation.") from exc
            except ManagedToolProviderError as exc:
                if not exc.outcome_known:
                    await _emit(ctx, metadata, "unknown_outcome", invocation_id=invocation.invocation_id)
                    raise ToolFailed("Managed tool outcome is unknown and requires reconciliation.") from exc
                if exc.retryable and replay_safe and attempt + 1 < attempts:
                    await _emit(
                        ctx,
                        metadata,
                        "retrying",
                        invocation_id=invocation.invocation_id,
                        attempt=attempt + 1,
                    )
                    continue
                await _emit(ctx, metadata, "provider_failed", invocation_id=invocation.invocation_id)
                raise ToolFailed("Managed tool provider failed.") from exc
        raise AssertionError("unreachable")


async def _require_policy_allow(
    ctx: RunContext[AgentContext],
    policy: InvocationPolicyCapability,
    invocation: ToolInvocationContext,
    metadata: HarnessToolMetadata,
    *,
    approval_satisfied: bool,
) -> None:
    decision = await policy.evaluator(invocation, metadata, context=ctx.deps)
    if not isinstance(decision, InvocationPolicyDecision):
        raise DefinitionError("Invocation policy returned an invalid decision.", code="invocation_policy_invalid")
    if decision.decision == "deny":
        await _emit(ctx, metadata, "denied", invocation_id=invocation.invocation_id)
        raise ToolFailed("Managed tool invocation was denied.")
    if decision.decision == "approval_required" and not approval_satisfied:
        await _emit(ctx, metadata, "approval_required", invocation_id=invocation.invocation_id)
        raise ApprovalRequired(metadata=dict(decision.approval_metadata))


def _validate_finalized_capability_provenance(ctx: RunContext[AgentContext]) -> None:
    """Reject protected Capability replacement after native for_run finalization."""
    from a13n_harness.capabilities.codeact import CODEACT_CAPABILITY_ID, CodeActCapability
    from a13n_harness.capabilities.context import (
        COMPACTION_CAPABILITY_ID,
        FILE_CONTEXT_CAPABILITY_ID,
        HANDOFF_CAPABILITY_ID,
        RUNTIME_CONTEXT_CAPABILITY_ID,
        CompactionCapability,
        FileContextCapability,
        HandoffCapability,
        RuntimeContextCapability,
        _FileContextRunCapability,
    )
    from a13n_harness.capabilities.delegation import (
        DELEGATION_CAPABILITY_ID,
        DELEGATION_RUN_CAPABILITY_ID,
        DelegationCapability,
        DelegationRunCapability,
        _DelegationActiveCapability,
    )
    from a13n_harness.capabilities.documents import (
        DOCUMENTS_CAPABILITY_ID,
        DOCUMENTS_RUN_CAPABILITY_ID,
        DocumentsRunCapability,
        _DocumentsActiveCapability,
    )
    from a13n_harness.capabilities.interaction import (
        USER_INTERACTION_CAPABILITY_ID,
        UserInteractionCapability,
    )
    from a13n_harness.capabilities.lifecycle import (
        LIFECYCLE_EVENT_CAPABILITY_ID,
        _LifecycleEventActiveCapability,
    )
    from a13n_harness.capabilities.media import (
        MEDIA_CAPABILITY_ID,
        MEDIA_RUN_CAPABILITY_ID,
        MediaRunCapability,
        _MediaActiveCapability,
    )
    from a13n_harness.capabilities.process_monitor import (
        MONITORED_PROCESS_CAPABILITY_ID,
        MONITORED_PROCESS_RUN_CAPABILITY_ID,
        MonitoredProcessRunCapability,
        _MonitoredProcessActiveCapability,
    )
    from a13n_harness.capabilities.skills import (
        SKILLS_CAPABILITY_ID,
        SkillsCapability,
        _SkillsRunCapability,
    )
    from a13n_harness.capabilities.web import (
        WEB_CAPABILITY_ID,
        WEB_RUN_CAPABILITY_ID,
        WebRunCapability,
        _WebActiveCapability,
    )
    from a13n_harness.capabilities.working_state import (
        TASK_STATE_RUN_CAPABILITY_ID,
        WORKING_STATE_CAPABILITY_ID,
        TaskStateRunCapability,
        WorkingStateCapability,
        _WorkingStateRunCapability,
    )
    from a13n_harness.environment.dynamic import (
        DYNAMIC_ENVIRONMENT_CAPABILITY_ID,
        DynamicEnvironmentCapability,
        _DynamicEnvironmentRunCapability,
    )
    from a13n_harness.filters.integrity import (
        MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID,
        MessageIntegrityFilterCapability,
    )
    from a13n_harness.pricing import (
        MODEL_COST_CAPABILITY_ID,
        AbstractModelCostCapability,
    )
    from a13n_harness.tools.client import (
        CLIENT_TOOLS_CAPABILITY_ID,
        CLIENT_TOOLS_RUN_CAPABILITY_ID,
        ClientToolsCapability,
        ClientToolsRunCapability,
    )
    from a13n_harness.usage import USAGE_CAPABILITY_ID, _UsageActiveCapability

    provenance = ctx.deps._capability_provenance
    expected = {
        TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID: (
            (ToolExecutionBoundaryCapability,),
            None,
        ),
        MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID: (
            (MessageIntegrityFilterCapability,),
            None,
        ),
        LIFECYCLE_EVENT_CAPABILITY_ID: (
            (_LifecycleEventActiveCapability,),
            None,
        ),
        INVOCATION_POLICY_CAPABILITY_ID: (
            (InvocationPolicyCapability,),
            provenance.run_ids,
        ),
        USAGE_CAPABILITY_ID: (
            (_UsageActiveCapability,),
            None,
        ),
        MODEL_COST_CAPABILITY_ID: (
            (AbstractModelCostCapability,),
            None,
        ),
        CLIENT_TOOLS_CAPABILITY_ID: (
            (ClientToolsCapability,),
            provenance.definition_ids,
        ),
        CLIENT_TOOLS_RUN_CAPABILITY_ID: (
            (ClientToolsRunCapability,),
            provenance.run_ids,
        ),
        CODEACT_CAPABILITY_ID: (
            (CodeActCapability,),
            provenance.definition_ids,
        ),
        DYNAMIC_ENVIRONMENT_CAPABILITY_ID: (
            (DynamicEnvironmentCapability, _DynamicEnvironmentRunCapability),
            provenance.definition_ids,
        ),
        RUNTIME_CONTEXT_CAPABILITY_ID: (
            (RuntimeContextCapability,),
            provenance.definition_ids,
        ),
        FILE_CONTEXT_CAPABILITY_ID: (
            (FileContextCapability, _FileContextRunCapability),
            provenance.definition_ids,
        ),
        HANDOFF_CAPABILITY_ID: (
            (HandoffCapability,),
            provenance.definition_ids,
        ),
        COMPACTION_CAPABILITY_ID: (
            (CompactionCapability,),
            provenance.definition_ids,
        ),
        MONITORED_PROCESS_CAPABILITY_ID: (
            (_MonitoredProcessActiveCapability,),
            provenance.definition_ids,
        ),
        MONITORED_PROCESS_RUN_CAPABILITY_ID: (
            (MonitoredProcessRunCapability,),
            provenance.run_ids,
        ),
        USER_INTERACTION_CAPABILITY_ID: (
            (UserInteractionCapability,),
            provenance.definition_ids,
        ),
        SKILLS_CAPABILITY_ID: (
            (SkillsCapability, _SkillsRunCapability),
            provenance.definition_ids,
        ),
        MEDIA_CAPABILITY_ID: (
            (_MediaActiveCapability,),
            provenance.definition_ids,
        ),
        MEDIA_RUN_CAPABILITY_ID: (
            (MediaRunCapability,),
            provenance.run_ids,
        ),
        DOCUMENTS_CAPABILITY_ID: (
            (_DocumentsActiveCapability,),
            provenance.definition_ids,
        ),
        DOCUMENTS_RUN_CAPABILITY_ID: (
            (DocumentsRunCapability,),
            provenance.run_ids,
        ),
        WEB_CAPABILITY_ID: (
            (_WebActiveCapability,),
            provenance.definition_ids,
        ),
        WEB_RUN_CAPABILITY_ID: (
            (WebRunCapability,),
            provenance.run_ids,
        ),
        WORKING_STATE_CAPABILITY_ID: (
            (WorkingStateCapability, _WorkingStateRunCapability),
            provenance.definition_ids,
        ),
        TASK_STATE_RUN_CAPABILITY_ID: (
            (TaskStateRunCapability,),
            provenance.run_ids,
        ),
        DELEGATION_CAPABILITY_ID: (
            (DelegationCapability, _DelegationActiveCapability),
            provenance.definition_ids,
        ),
        DELEGATION_RUN_CAPABILITY_ID: (
            (DelegationRunCapability,),
            provenance.run_ids,
        ),
    }
    reserved_types = tuple(capability_type for item in expected.values() for capability_type in item[0])
    for capability_id, capability in ctx.capabilities.items():
        if isinstance(capability, reserved_types) and capability_id not in expected:
            raise DefinitionError(
                "A protected Harness Capability changed its reserved ID during run binding.",
                code="capability_scope_invalid",
                details={
                    "capability_id": capability_id,
                    "capability_type": type(capability).__name__,
                    "source": "run_finalized",
                },
            )

    for capability_id, (expected_types, allowed_ids) in expected.items():
        capability = ctx.capabilities.get(capability_id)
        if capability_id == MODEL_COST_CAPABILITY_ID:
            if not isinstance(capability, AbstractModelCostCapability) or capability_id in provenance.run_ids:
                raise DefinitionError(
                    "The finalized run is missing one build-time model-cost Capability.",
                    code="capability_scope_invalid",
                    details={"capability_id": capability_id, "source": "run_finalized"},
                )
            continue
        if capability_id in {
            TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID,
            MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID,
            LIFECYCLE_EVENT_CAPABILITY_ID,
            USAGE_CAPABILITY_ID,
        }:
            mandatory_type = {
                TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID: ToolExecutionBoundaryCapability,
                MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID: MessageIntegrityFilterCapability,
                LIFECYCLE_EVENT_CAPABILITY_ID: _LifecycleEventActiveCapability,
                USAGE_CAPABILITY_ID: _UsageActiveCapability,
            }[capability_id]
            if type(capability) is not mandatory_type:
                raise DefinitionError(
                    "The finalized run is missing an exact mandatory Harness Capability.",
                    code="capability_scope_invalid",
                    details={"capability_id": capability_id, "source": "run_finalized"},
                )
            continue
        if capability is None:
            if allowed_ids is not None and capability_id in allowed_ids:
                raise DefinitionError(
                    "A protected Harness Capability disappeared during run binding.",
                    code="capability_scope_invalid",
                    details={"capability_id": capability_id, "source": "run_finalized"},
                )
            continue
        if type(capability) not in expected_types or allowed_ids is None or capability_id not in allowed_ids:
            raise DefinitionError(
                "A protected Harness Capability changed type or source during run binding.",
                code="capability_scope_invalid",
                details={
                    "capability_id": capability_id,
                    "capability_type": type(capability).__name__,
                    "source": "run_finalized",
                },
            )

    monitored_owner = ctx.capabilities.get(MONITORED_PROCESS_CAPABILITY_ID)
    monitored_attachment = ctx.capabilities.get(MONITORED_PROCESS_RUN_CAPABILITY_ID)
    if monitored_attachment is not None and monitored_owner is None:
        raise DefinitionError(
            "A monitored-process run attachment requires its definition owner.",
            code="monitored_process_owner_missing",
        )
    if monitored_owner is not None and monitored_attachment is None:
        raise DefinitionError(
            "MonitoredProcessCapability requires one fresh run attachment.",
            code="monitored_process_binding_missing",
        )

    content_pairs = (
        (
            MEDIA_CAPABILITY_ID,
            MEDIA_RUN_CAPABILITY_ID,
            "media_owner_missing",
            "media_binding_missing",
            "Media",
        ),
        (
            DOCUMENTS_CAPABILITY_ID,
            DOCUMENTS_RUN_CAPABILITY_ID,
            "documents_owner_missing",
            "documents_binding_missing",
            "Documents",
        ),
        (
            WEB_CAPABILITY_ID,
            WEB_RUN_CAPABILITY_ID,
            "web_owner_missing",
            "web_binding_missing",
            "Web",
        ),
    )
    for owner_id, attachment_id, owner_code, binding_code, label in content_pairs:
        owner = ctx.capabilities.get(owner_id)
        attachment = ctx.capabilities.get(attachment_id)
        if attachment is not None and owner is None:
            raise DefinitionError(
                f"A {label} run attachment requires its definition owner.",
                code=owner_code,
            )
        if owner is not None and attachment is None:
            raise DefinitionError(
                f"{label}Capability requires one fresh run attachment.",
                code=binding_code,
            )

    delegation_owner = ctx.capabilities.get(DELEGATION_CAPABILITY_ID)
    delegation_attachment = ctx.capabilities.get(DELEGATION_RUN_CAPABILITY_ID)
    if delegation_attachment is not None and delegation_owner is None:
        raise DefinitionError(
            "A Delegation run attachment requires its definition owner.",
            code="delegation_owner_missing",
        )
    if delegation_owner is not None and delegation_attachment is None:
        raise DefinitionError(
            "DelegationCapability requires one fresh run attachment.",
            code="delegation_binding_missing",
        )

    working_state_owner = ctx.capabilities.get(WORKING_STATE_CAPABILITY_ID)
    task_state_attachment = ctx.capabilities.get(TASK_STATE_RUN_CAPABILITY_ID)
    if task_state_attachment is not None and working_state_owner is None:
        raise DefinitionError(
            "A task-state run attachment requires its definition owner.",
            code="task_state_owner_missing",
        )


def _resolve_policy(ctx: RunContext[AgentContext]) -> InvocationPolicyCapability | None:
    value = ctx.capabilities.get(INVOCATION_POLICY_CAPABILITY_ID)
    if value is None:
        return None
    if type(value) is not InvocationPolicyCapability:
        raise DefinitionError(
            "The invocation policy Capability changed protected type during run binding.",
            code="capability_scope_invalid",
        )
    if INVOCATION_POLICY_CAPABILITY_ID not in ctx.deps._capability_provenance.run_ids:
        raise DefinitionError(
            "InvocationPolicyCapability must originate from RunBindings.",
            code="capability_scope_invalid",
        )
    return value


async def _prepare_invocation(
    ctx: RunContext[AgentContext],
    tool_name: str,
    toolset_id: str | None,
    tool_args: dict[str, Any],
    metadata: HarnessToolMetadata,
) -> _PreparedInvocation:
    try:
        typed_arguments = deepcopy(tool_args)
        require_finite_json(typed_arguments)
        projected = _ANY_ADAPTER.dump_python(typed_arguments, mode="json", warnings="error")
        normalized = _JSON_OBJECT_ADAPTER.validate_python(projected, strict=True)
        encoded = dump_json_bytes(normalized, sort_keys=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ToolFailed("Managed tool arguments cannot be represented safely.") from exc
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise ToolFailed("Managed tool arguments exceed the supported size.")
    digest = hashlib.sha256(encoded).hexdigest()

    resources: tuple[CanonicalResource, ...] = ()
    if metadata.resource_resolver is not None:
        try:
            resolved = await metadata.resource_resolver(deepcopy(typed_arguments), context=ctx.deps)
        except Exception as exc:
            raise ToolFailed("Managed tool resources could not be resolved.") from exc
        if not isinstance(resolved, tuple) or not all(isinstance(item, CanonicalResource) for item in resolved):
            raise DefinitionError(
                "Managed resource resolver returned an invalid value.",
                code="resource_resolution_invalid",
            )
        resources = tuple(item.model_copy(deep=True) for item in resolved)

    tool_call_id = ctx.tool_call_id or f"programmatic:{uuid4()}"
    idempotency_key = None
    if metadata.idempotency in {"read_only", "provider_key"}:
        instance = ctx.deps.instance
        key_source = (
            f"{instance.identity.issuer}:{instance.identity.subject}:"
            f"{instance.agent_instance_id}:{tool_call_id}:{metadata.tool_id}:{digest}"
        ).encode()
        idempotency_key = hashlib.sha256(key_source).hexdigest()
    prepared_tool = ctx.tools.get(tool_name)
    timeout = prepared_tool.timeout if prepared_tool is not None else None
    deadline = datetime.now(UTC) + timedelta(seconds=timeout) if timeout is not None else None
    return _PreparedInvocation(
        context=ToolInvocationContext(
            invocation_id=str(uuid4()),
            tool_call_id=tool_call_id,
            run_id=ctx.deps.run_id,
            instance=ctx.deps.instance,
            tool_id=metadata.tool_id,
            toolset_id=toolset_id,
            tool_name=tool_name,
            normalized_arguments=normalized,
            arguments_digest=digest,
            resources=resources,
            idempotency_key=idempotency_key,
            deadline=deadline,
        ),
        typed_arguments=typed_arguments,
    )


async def _apply_result_policy(
    result: Any,
    source: HarnessToolMetadata | ToolOutputPolicy,
    *,
    context: AgentContext | None = None,
    reject_non_json: bool = True,
) -> Any:
    """Apply the textual result boundary without rewriting native media."""
    policy = source.output_policy if isinstance(source, HarnessToolMetadata) else source
    if is_acknowledged_tool_output(result):
        return await _apply_acknowledged_json_result_policy(result, policy)
    if isinstance(result, ToolReturn):
        return await _apply_native_tool_return_policy(
            result,
            policy,
            context=context,
            reject_non_json=reject_non_json,
        )
    if not reject_non_json and not _is_native_json_result(result):
        return result
    return await _apply_json_result_policy(result, policy, context=context)


async def _apply_acknowledged_json_result_policy(
    result: Any,
    policy: ToolOutputPolicy,
) -> JsonValue:
    """Keep semantic Toolset output intact unless it violates the larger hard ceiling."""
    try:
        value = _project_json_result(result)
        safe_value = redact_json(value) if policy.redact else deepcopy(value)
        output_chars, output_bytes, head, tail = _scan_json(
            safe_value,
            keep_bytes=min(policy.max_inline_bytes, FINAL_TOOL_OUTPUT_HARD_CHARS) // 2,
        )
    except (RecursionError, TypeError, ValueError, ValidationError) as exc:
        raise ToolFailed("Tool returned an invalid result.") from exc
    if output_chars <= FINAL_TOOL_OUTPUT_HARD_CHARS and output_bytes <= policy.max_output_bytes:
        return safe_value
    return _bounded_json_preview(
        safe_value,
        output_chars=output_chars,
        output_bytes=output_bytes,
        output_file_path=None,
        head=head,
        tail=tail,
        char_limit=FINAL_TOOL_OUTPUT_HARD_CHARS,
        byte_limit=policy.max_inline_bytes,
    )


async def _apply_json_result_policy(
    result: Any,
    policy: ToolOutputPolicy,
    *,
    context: AgentContext | None,
) -> JsonValue:
    try:
        value = _project_json_result(result)
        safe_value = redact_json(value) if policy.redact else deepcopy(value)
        output_chars, output_bytes, head, tail = _scan_json(
            safe_value,
            keep_bytes=min(policy.max_inline_bytes, FINAL_TOOL_OUTPUT_HARD_CHARS) // 2,
        )
    except (RecursionError, TypeError, ValueError, ValidationError) as exc:
        raise ToolFailed("Tool returned an invalid result.") from exc

    if output_chars <= FINAL_TOOL_OUTPUT_HARD_CHARS and output_bytes <= policy.max_inline_bytes:
        return safe_value
    if policy.overflow == "fail":
        raise ToolFailed("Tool result exceeded its output limit.")

    output_file_path: str | None = None
    if policy.overflow == "spill" and output_bytes <= policy.max_output_bytes and context is not None:
        encoded = dump_json_bytes(safe_value)
        output_file_path = await context._spill_tool_result(encoded, suffix=".json")

    return _bounded_json_preview(
        safe_value,
        output_chars=output_chars,
        output_bytes=output_bytes,
        output_file_path=output_file_path,
        head=head,
        tail=tail,
        char_limit=FINAL_TOOL_OUTPUT_HARD_CHARS,
        byte_limit=policy.max_inline_bytes,
    )


async def _apply_native_tool_return_policy(
    result: ToolReturn,
    policy: ToolOutputPolicy,
    *,
    context: AgentContext | None,
    reject_non_json: bool,
) -> ToolReturn:
    """Bound textual fields while preserving every native multimodal value."""
    try:
        return_value = await _apply_optional_json_result_policy(
            result.return_value,
            policy,
            context=context,
            reject_non_json=reject_non_json,
        )
        content: str | list[Any] | tuple[Any, ...] | None
        if isinstance(result.content, str):
            content = await _apply_text_result_policy(result.content, policy, context=context)
        elif result.content is None:
            content = None
        else:
            projected_content: list[Any] = []
            for item in result.content:
                if isinstance(item, str):
                    projected_content.append(await _apply_text_result_policy(item, policy, context=context))
                elif isinstance(item, TextContent):
                    projected_content.append(
                        TextContent(
                            content=await _apply_text_result_policy(item.content, policy, context=context),
                            metadata=(
                                None
                                if item.metadata is None
                                else await _apply_optional_json_result_policy(
                                    item.metadata,
                                    policy,
                                    context=context,
                                    reject_non_json=reject_non_json,
                                )
                            ),
                        )
                    )
                else:
                    projected_content.append(item)
            content = tuple(projected_content) if isinstance(result.content, tuple) else projected_content
        projected_metadata = (
            None
            if result.metadata is None
            else await _apply_optional_json_result_policy(
                result.metadata,
                policy,
                context=context,
                reject_non_json=reject_non_json,
            )
        )
        tools = (
            None
            if result.tools is None
            else [await _apply_text_result_policy(item, policy, context=context) for item in result.tools]
        )
        return ToolReturn(
            return_value=return_value,
            content=content,
            metadata=projected_metadata,
            tools=tools,
        )
    except ToolFailed:
        raise
    except (RecursionError, TypeError, ValueError, ValidationError) as exc:
        raise ToolFailed("Tool returned invalid native content.") from exc


async def _apply_optional_json_result_policy(
    value: Any,
    policy: ToolOutputPolicy,
    *,
    context: AgentContext | None,
    reject_non_json: bool,
) -> Any:
    if not reject_non_json and not _is_native_json_result(value):
        return value
    return await _apply_json_result_policy(value, policy, context=context)


async def _apply_text_result_policy(
    value: str,
    policy: ToolOutputPolicy,
    *,
    context: AgentContext | None,
) -> str:
    if not isinstance(value, str):
        raise TypeError("native text fields must be strings")
    safe_value = redact_bearer(value) if policy.redact else value
    encoded = safe_value.encode("utf-8")
    output_chars = len(safe_value)
    output_bytes = len(encoded)
    if output_chars <= FINAL_TOOL_OUTPUT_HARD_CHARS and output_bytes <= policy.max_inline_bytes:
        return safe_value
    if policy.overflow == "fail":
        raise ToolFailed("Tool result exceeded its output limit.")

    output_file_path: str | None = None
    if policy.overflow == "spill" and output_bytes <= policy.max_output_bytes and context is not None:
        output_file_path = await context._spill_tool_result(encoded, suffix=".txt")
    marker = (
        f"\n[tool result truncated; output_chars={output_chars}; output_bytes={output_bytes}; "
        f"output_file_path={output_file_path or 'unavailable'}]\n"
    )
    return _bounded_head_tail_text(
        safe_value,
        marker=marker,
        char_limit=FINAL_TOOL_OUTPUT_HARD_CHARS,
        byte_limit=policy.max_inline_bytes,
    )


def _bounded_json_preview(
    value: JsonValue,
    *,
    output_chars: int,
    output_bytes: int,
    output_file_path: str | None,
    head: bytes,
    tail: bytes,
    char_limit: int,
    byte_limit: int,
) -> dict[str, JsonValue]:
    leaf_limit = max(8, min(char_limit, byte_limit) // 4)
    while leaf_limit >= 8:
        result = _truncate_json_strings(value, leaf_limit)
        envelope: dict[str, JsonValue] = {
            "result": result,
            "truncated": True,
            "output_chars": output_chars,
            "output_bytes": output_bytes,
            "output_file_path": output_file_path,
        }
        if _json_fits(envelope, char_limit=char_limit, byte_limit=byte_limit):
            return envelope
        leaf_limit //= 2

    head_text = head.decode("utf-8", errors="ignore")
    tail_text = tail.decode("utf-8", errors="ignore")
    omitted_chars = max(0, output_chars - len(head_text) - len(tail_text))
    omitted_bytes = max(0, output_bytes - len(head) - len(tail))
    preview = f"{head_text}\n[... {omitted_chars} characters / {omitted_bytes} bytes omitted ...]\n{tail_text}"
    envelope = {
        "result": "",
        "truncated": True,
        "output_chars": output_chars,
        "output_bytes": output_bytes,
        "output_file_path": output_file_path,
    }
    low = 0
    high = len(preview)
    while low <= high:
        retained = (low + high) // 2
        candidate = _head_tail_chars(preview, retained)
        envelope["result"] = candidate
        if _json_fits(envelope, char_limit=char_limit, byte_limit=byte_limit):
            low = retained + 1
        else:
            high = retained - 1
    envelope["result"] = _head_tail_chars(preview, max(0, high))
    return envelope


def _truncate_json_strings(value: JsonValue, limit: int) -> JsonValue:
    if isinstance(value, str):
        if len(value) <= limit and len(value.encode("utf-8")) <= limit:
            return value
        marker = "\n[... truncated ...]\n"
        return _bounded_head_tail_text(
            value,
            marker=marker,
            char_limit=limit,
            byte_limit=limit,
        )
    if isinstance(value, list):
        return [_truncate_json_strings(item, limit) for item in value]
    if isinstance(value, dict):
        return {key: _truncate_json_strings(item, limit) for key, item in value.items()}
    return value


def _bounded_head_tail_text(
    value: str,
    *,
    marker: str,
    char_limit: int,
    byte_limit: int,
) -> str:
    if char_limit <= 0 or byte_limit <= 0:
        return ""
    if len(marker) > char_limit or len(marker.encode("utf-8")) > byte_limit:
        return _bounded_text_prefix(marker, char_limit=char_limit, byte_limit=byte_limit)
    if len(value) <= char_limit and len(value.encode("utf-8")) <= byte_limit:
        return value
    low = 0
    high = len(value)
    while low <= high:
        retained = (low + high) // 2
        head_size = retained // 2
        tail_size = retained - head_size
        tail = value[-tail_size:] if tail_size else ""
        candidate = f"{value[:head_size]}{marker}{tail}"
        if len(candidate) <= char_limit and len(candidate.encode("utf-8")) <= byte_limit:
            low = retained + 1
        else:
            high = retained - 1
    head_size = max(0, high) // 2
    tail_size = max(0, high) - head_size
    tail = value[-tail_size:] if tail_size else ""
    return f"{value[:head_size]}{marker}{tail}"


def _head_tail_chars(value: str, retained: int) -> str:
    head_size = retained // 2
    tail_size = retained - head_size
    tail = value[-tail_size:] if tail_size else ""
    return f"{value[:head_size]}{tail}"


def _bounded_text_prefix(value: str, *, char_limit: int, byte_limit: int) -> str:
    low = 0
    high = min(len(value), char_limit)
    while low <= high:
        selected = (low + high) // 2
        if len(value[:selected].encode("utf-8")) <= byte_limit:
            low = selected + 1
        else:
            high = selected - 1
    return value[: max(0, high)]


def _json_fits(value: JsonValue, *, char_limit: int, byte_limit: int) -> bool:
    text = dump_json_text(value)
    return len(text) <= char_limit and len(text.encode("utf-8")) <= byte_limit


def _scan_json(value: JsonValue, *, keep_bytes: int) -> tuple[int, int, bytes, bytes]:
    total_chars = 0
    total_bytes = 0
    head = bytearray()
    tail = bytearray()
    for chunk in _iter_json(value, redact=False):
        total_chars += len(chunk.decode("utf-8"))
        total_bytes += len(chunk)
        if len(head) < keep_bytes:
            head.extend(chunk[: keep_bytes - len(head)])
        if keep_bytes:
            tail.extend(chunk)
            if len(tail) > keep_bytes:
                del tail[: len(tail) - keep_bytes]
    return total_chars, total_bytes, bytes(head), bytes(tail)


def _project_json_result(result: Any) -> JsonValue:
    if not _is_native_json(result):
        raise TypeError("Tool results must be native JSON values")
    return cast(JsonValue, result)


def _is_native_json_result(value: Any) -> bool:
    try:
        return _is_native_json(value)
    except ValueError:
        return False


def _is_native_json(value: Any, active: set[int] | None = None) -> bool:
    if value is None or isinstance(value, str | bool | int):
        return True
    if isinstance(value, float):
        require_finite_json(value)
        return True
    if not isinstance(value, list | dict):
        return False

    active = active if active is not None else set()
    value_id = id(value)
    if value_id in active:
        raise ValueError("JSON values cannot contain cycles")
    active.add(value_id)
    try:
        if isinstance(value, list):
            return all(_is_native_json(item, active) for item in value)
        return all(isinstance(key, str) and _is_native_json(item, active) for key, item in value.items())
    finally:
        active.remove(value_id)


def _capture_json(value: JsonValue, *, limit: int, redact: bool) -> tuple[bytes, bool]:
    captured = bytearray()
    for chunk in _iter_json(value, redact=redact):
        remaining = limit - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > remaining:
            return bytes(captured), False
    return bytes(captured), True


def _iter_json(value: JsonValue, *, redact: bool) -> Iterator[bytes]:
    if value is None:
        yield b"null"
    elif value is True:
        yield b"true"
    elif value is False:
        yield b"false"
    elif isinstance(value, int | float):
        yield json.dumps(value, allow_nan=False).encode("ascii")
    elif isinstance(value, str):
        yield from _iter_json_string(redact_bearer(value) if redact else value)
    elif isinstance(value, list):
        yield b"["
        for index, item in enumerate(value):
            if index:
                yield b","
            yield from _iter_json(item, redact=redact)
        yield b"]"
    else:
        yield b"{"
        for index, (key, item) in enumerate(value.items()):
            if index:
                yield b","
            yield from _iter_json_string(key)
            yield b":"
            if redact and is_sensitive_key(key):
                yield b'"[REDACTED]"'
            else:
                yield from _iter_json(item, redact=redact)
        yield b"}"


def _iter_json_string(value: str, *, chunk_size: int = 4096) -> Iterator[bytes]:
    yield b'"'
    for offset in range(0, len(value), chunk_size):
        encoded = json.dumps(value[offset : offset + chunk_size], ensure_ascii=False)[1:-1]
        yield encoded.encode("utf-8")
    yield b'"'


async def _emit(
    ctx: RunContext[AgentContext],
    metadata: HarnessToolMetadata,
    phase: str,
    **fields: JsonValue,
) -> None:
    payload: dict[str, JsonValue] = {"phase": phase, "tool_id": metadata.tool_id, "tool_name": ctx.tool_name}
    payload.update(fields)
    await ctx.deps.events.emit(HarnessExtensionEvent(kind="invocation", payload=payload))


async def _emit_best_effort(
    ctx: RunContext[AgentContext],
    metadata: HarnessToolMetadata,
    phase: str,
    **fields: JsonValue,
) -> None:
    try:
        await asyncio.shield(_emit(ctx, metadata, phase, **fields))
    except BaseException:
        pass


def _validate_final_toolset_wrapper_order(toolset: AbstractToolset[AgentContext]) -> None:
    from a13n_harness.tools.surface import ToolSurfaceToolset
    from a13n_harness.toolsets.codeact import CodeActToolset

    current = toolset
    if isinstance(current, CodeActToolset):
        current = current.wrapped
    if not isinstance(current, ToolSurfaceToolset):
        raise DefinitionError(
            "Only CodeAct may wrap the mandatory tool surface inside the execution boundary.",
            code="tool_surface_order_invalid",
            details={"toolset_type": type(current).__name__},
        )


def _validate_client_run_attachment(ctx: RunContext[AgentContext]) -> None:
    from a13n_harness.tools.client import (
        CLIENT_TOOLS_CAPABILITY_ID,
        CLIENT_TOOLS_RUN_CAPABILITY_ID,
        ClientToolsCapability,
        ClientToolsRunCapability,
    )

    provenance = ctx.deps._capability_provenance
    owner = ctx.capabilities.get(CLIENT_TOOLS_CAPABILITY_ID)
    if owner is not None and type(owner) is not ClientToolsCapability:
        raise DefinitionError(
            "The client-tools Capability has an incompatible type.",
            code="client_tools_type_mismatch",
        )
    if owner is not None and CLIENT_TOOLS_CAPABILITY_ID not in provenance.definition_ids:
        raise DefinitionError(
            "ClientToolsCapability must originate from the Agent definition.",
            code="capability_scope_invalid",
        )

    attachment = ctx.capabilities.get(CLIENT_TOOLS_RUN_CAPABILITY_ID)
    if attachment is not None and type(attachment) is not ClientToolsRunCapability:
        raise DefinitionError(
            "The client-tools run Capability has an incompatible type.",
            code="client_tools_run_type_mismatch",
        )
    if attachment is not None and CLIENT_TOOLS_RUN_CAPABILITY_ID not in provenance.run_ids:
        raise DefinitionError(
            "ClientToolsRunCapability must originate from RunBindings.",
            code="capability_scope_invalid",
        )
    if attachment is not None and owner is None:
        raise DefinitionError(
            "A client-tools run attachment requires ClientToolsCapability in the Agent definition.",
            code="client_tools_owner_missing",
        )


def _validate_client_marker(tool_name: str, metadata: Mapping[str, Any]) -> None:
    from a13n_harness.tools.client import CLIENT_TOOL_MARKER_KEY

    marker = metadata.get(CLIENT_TOOL_MARKER_KEY)
    if marker is None:
        return
    if not isinstance(marker, Mapping) or marker.get("declared_name") != tool_name:
        raise DefinitionError(
            "A client tool was renamed after its declaration was materialized.",
            code="client_tool_name_changed",
            details={"tool_name": tool_name},
        )


def _validate_resume_surface(
    ctx: RunContext[AgentContext],
    tools: Mapping[str, ToolsetTool[AgentContext]],
) -> None:
    resume = ctx.deps.deferred_resume
    if resume is None:
        return
    required_names = {call.tool_name for call in resume.requests.calls}
    for name in required_names:
        tool = tools.get(name)
        if tool is None or tool.tool_def.kind != "external":
            raise DefinitionError(
                "The current tool surface does not match the pending external call.",
                code="deferred_surface_mismatch",
                details={"tool_name": name},
            )

    for request in resume.requests.approvals:
        expected_tool_id = managed_approval_tool_id(resume.requests, request.tool_call_id)
        if expected_tool_id is None:
            continue
        tool = tools.get(request.tool_name)
        raw_metadata = (
            (tool.tool_def.metadata or {}).get(HARNESS_TOOL_METADATA_KEY)
            if tool is not None and tool.tool_def.kind in {"function", "unapproved"}
            else None
        )
        if raw_metadata is None:
            raise DefinitionError(
                "The current tool surface does not match the pending managed approval.",
                code="deferred_surface_mismatch",
                details={"tool_name": request.tool_name},
            )
        current = normalize_harness_tool_metadata(raw_metadata)
        if current.tool_id != expected_tool_id:
            raise DefinitionError(
                "The current managed tool identity does not match the pending approval.",
                code="deferred_surface_mismatch",
                details={"tool_name": request.tool_name},
            )
