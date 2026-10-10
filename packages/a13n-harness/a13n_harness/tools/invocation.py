"""Outer execution boundary over Pydantic AI's assembled function Toolset."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
from collections.abc import AsyncIterable, Generator, Mapping
from contextlib import AsyncExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from a13n_environment.models import EnvironmentError
from a13n_logging import exception_details, get_logger
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai import CallToolsNode, RunContext, ToolReturn
from pydantic_ai.capabilities import AbstractCapability, AgentNode, CapabilityOrdering, ValidatedToolArgs
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ToolFailed
from pydantic_ai.messages import DeferredToolResultsEvent, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDefinition, ToolDenied
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset

from a13n_harness._json import (
    dump_json_bytes,
    project_json,
)
from a13n_harness._review_context import ReviewEvidence, append_review_evidence, record_approval_denials
from a13n_harness._tool_observation import record_tool_operation_failure
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError, EnvironmentActivationError
from a13n_harness.events import HarnessExtensionEvent
from a13n_harness.tools._output import (
    _apply_result_policy,
    _render_tool_return,
)
from a13n_harness.tools._source import tool_call_source_scope
from a13n_harness.tools.approval import (
    APPROVAL_PRESENTATION_KEY,
    approval_presentation,
    approval_required,
    compact_target,
    tool_approval_scope,
)
from a13n_harness.tools.identity import identify_tool, tool_identity
from a13n_harness.tools.metadata import (
    HARNESS_TOOL_METADATA_KEY,
    RECOVERY_RETRY_SAFE_METADATA_KEY,
    CanonicalResource,
    HarnessToolMetadata,
    ToolOutputPolicy,
    normalize_harness_tool_metadata,
)
from a13n_harness.tools.permission_gate import check_permission, gate_tool, permission_mode
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
logger = get_logger(__name__)
MAX_ARGUMENT_BYTES = 64 * 1024
_UNMANAGED_OUTPUT_POLICY = ToolOutputPolicy(
    max_inline_bytes=256 * 1024,
    max_output_bytes=4 * 1024 * 1024,
    overflow="truncate",
    redact=True,
)

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


async def _allow_managed_invocation(
    invocation: ToolInvocationContext,
    metadata: HarnessToolMetadata,
    *,
    context: AgentContext,
) -> InvocationPolicyDecision:
    del invocation, metadata, context
    return InvocationPolicyDecision.allow()


_DEFAULT_INVOCATION_POLICY = InvocationPolicyCapability(
    evaluator=_allow_managed_invocation,
    max_dispatch_retries=0,
)


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
def disabled_tool_execution() -> Generator[None]:
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

    async def before_node_run(
        self, ctx: RunContext[AgentContext], *, node: AgentNode[AgentContext]
    ) -> AgentNode[AgentContext]:
        if ctx.run_id != ctx.deps._model_recovery.attempt_id:
            return node
        accepted = ctx.deps._deferred_input
        resume = accepted.pending if accepted is not None else None
        if isinstance(node, CallToolsNode) and resume is not None and not resume.recovery:
            await record_approval_denials(resume.requests, resume.results, context=ctx.deps)
        recovery = ctx.deps._tool_recovery
        if recovery is not None and recovery.pending and isinstance(node, CallToolsNode):
            recovery.native_results = node.tool_call_results
        return node

    async def on_event(self, ctx: RunContext[AgentContext], *, event: Any) -> None:
        if isinstance(event, DeferredToolResultsEvent):
            await record_approval_denials(None, event.results, context=ctx.deps)
            # Native inline external resolution bypasses execution hooks. This
            # awaited event precedes conversion of accepted results into history.
            for call_id, result in event.results.calls.items():
                if isinstance(result, ToolReturn):
                    event.results.calls[call_id] = _render_tool_return(result)

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if ctx.run_id != ctx.deps._model_recovery.attempt_id:
            return request_context
        # Native results are now in canonical history. Retire accepted facts
        # before any context Capability can replace that history.
        if ctx.deps._deferred_input is not None:
            ctx.deps._deferred_input.reconcile(ctx.messages)
        ctx.deps._tool_permission_checks.clear()
        ctx.deps._tool_pending_approvals.clear()
        # Recovery applies only before the model makes its next decision.
        if (recovery := ctx.deps._tool_recovery) is not None:
            recovery.pending.clear()
            recovery.native_results = None
        return request_context

    async def before_tool_validate(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: str | dict[str, Any],
    ) -> str | dict[str, Any]:
        if permission_mode(ctx, tool_def) == "deny":
            raise ToolFailed("Tool invocation is denied by its permission configuration.")
        return args

    async def after_tool_validate(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
    ) -> ValidatedToolArgs:
        recovery = ctx.deps._tool_recovery
        if recovery is not None and call.tool_call_id in recovery.pending:
            if tool_def.kind == "unapproved":
                raise ApprovalRequired()
            if tool_def.kind == "external":
                raise CallDeferred()
        return args

    async def after_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        result: Any,
    ) -> Any:
        # The final native settlement owns model rendering. Nested CodeAct calls
        # retain their structured values until the outer runner returns.
        return _render_tool_return(result) if isinstance(result, ToolReturn) else result

    async def handle_deferred_tool_calls(
        self,
        ctx: RunContext[AgentContext],
        *,
        requests: DeferredToolRequests,
    ) -> DeferredToolResults | None:
        # Native declarative approvals are collected by kind without validator
        # metadata. Preserve our captured advisory provenance for
        # both inline handlers and the portable request returned on suspension.
        for call in requests.approvals:
            captured = ctx.deps._tool_pending_approvals.get(call.tool_call_id)
            if captured is not None:
                requests.metadata[call.tool_call_id] = {
                    **requests.metadata.get(call.tool_call_id, {}),
                    **captured,
                }
        for call in requests.calls:
            definition = ctx.tools.get(call.tool_name)
            if definition is not None and definition.kind in {"function", "unapproved"}:
                requests.metadata.setdefault(call.tool_call_id, {})["a13n.harness.deferred-function-id"] = (
                    tool_identity(definition).tool_id
                )
        if ctx.deps.deferred_tools_supported:
            return None
        message = "Deferred tool interaction is unavailable for this Run."
        return DeferredToolResults(
            calls={request.tool_call_id: ToolDenied(message) for request in requests.calls},
            approvals={request.tool_call_id: ToolDenied(message) for request in requests.approvals},
        )

    async def wrap_run_event_stream(
        self,
        ctx: RunContext[AgentContext],
        *,
        stream: AsyncIterable[Any],
    ) -> AsyncIterable[Any]:
        from a13n_harness._capability_contract import _validate_finalized_capability_provenance

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
            tool = identify_tool(tool)
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

        identities: dict[str, str] = {}
        for name, tool in normalized.items():
            identity = tool_identity(tool.tool_def)
            if identity.tool_id in identities:
                raise DefinitionError("Duplicate tool permission identity.", code="tool_identity_duplicate")
            identities[identity.tool_id] = name
            mode = permission_mode(ctx, tool.tool_def)
            if tool.tool_def.kind == "external" and mode in {"ask", "review"}:
                raise DefinitionError(
                    "External tools do not support local approval/review modes.", code="tool_permission_unsupported"
                )
            normalized[name] = gate_tool(tool)
        _validate_resume_surface(ctx, normalized)
        if (recovery := ctx.deps._tool_recovery) is not None:
            recovery.resolve(
                {
                    name: (tool.tool_def.metadata or {}).get(RECOVERY_RETRY_SAFE_METADATA_KEY) is True
                    for name, tool in normalized.items()
                }
            )
        return normalized

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentContext],
        tool: ToolsetTool[AgentContext],
    ) -> Any:
        tool = identify_tool(tool)
        check = await check_permission(ctx, tool.tool_def, tool_args)
        ctx = replace(ctx, tool_call_approved=check.approval.approved)
        with tool_approval_scope(ctx.deps, check.approval), tool_call_source_scope(ctx):
            try:
                return await self._call_tool(name, tool_args, ctx, tool)
            except ApprovalRequired as exc:
                raise approval_required(
                    ctx,
                    check.approval,
                    metadata={
                        **(exc.metadata or {}),
                        APPROVAL_PRESENTATION_KEY: approval_presentation(
                            tool_args,
                            tool_id=check.approval.tool_id,
                            reason="policy" if _POLICY_APPROVAL_METADATA_KEY in (exc.metadata or {}) else None,
                        ),
                    },
                ) from exc

    async def _call_tool(
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
            result = await self._call_observed(name, tool_args, ctx, tool)
            return await _apply_result_policy(
                result,
                _UNMANAGED_OUTPUT_POLICY,
                context=ctx.deps,
                reject_non_json=False,
            )
        managed = normalize_harness_tool_metadata(raw_metadata)
        policy = _resolve_policy(ctx) or _DEFAULT_INVOCATION_POLICY

        try:
            prepared = await _prepare_invocation(ctx, tool_def.name, tool_def.toolset_id, tool_args, managed)
        except EnvironmentError as exc:
            record_tool_operation_failure(exc.code, reason=exc.details.get("reason"), stage="preparation")
            await _emit(ctx, managed, "preparation_failed")
            # Known Environment failures are actionable even before dispatch.
            # Project only public details, never raw provider exception text.
            error = exc.safe_projection()
            return await _apply_result_policy(
                {"ok": False, "error": error},
                managed.output_policy,
                context=ctx.deps,
                reject_non_json=True,
            )
        except ToolFailed:
            await _emit(ctx, managed, "preparation_failed")
            raise
        invocation = prepared.context
        await _emit(ctx, managed, "prepared", invocation_id=invocation.invocation_id)
        policy_decision = await _evaluate_policy(ctx, policy, invocation, managed)
        if policy_decision.decision == "deny":
            await _emit(ctx, managed, "denied", invocation_id=invocation.invocation_id)
            raise ToolFailed("Managed tool invocation was denied.")
        requires_approval = policy_decision.decision == "approval_required" and not ctx.tool_call_approved
        if requires_approval:
            await _emit(ctx, managed, "approval_required", invocation_id=invocation.invocation_id)
            metadata: dict[str, JsonValue] = {
                **dict(policy_decision.approval_metadata),
                _POLICY_APPROVAL_METADATA_KEY: {
                    "decision": policy_decision.decision,
                    "metadata": dict(policy_decision.approval_metadata),
                },
            }
            raise ApprovalRequired(metadata=metadata)
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
                    managed.output_policy,
                    context=ctx.deps,
                )
            except ToolFailed:
                await _emit(ctx, managed, "result_rejected", invocation_id=invocation.invocation_id)
                raise
            await _emit(ctx, managed, "completed", invocation_id=invocation.invocation_id)
            return safe_result
        finally:
            await lease_stack.aclose()

    async def _call_observed(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentContext],
        tool: ToolsetTool[AgentContext],
    ) -> Any:
        """Record dispatch facts, including nested targets, never full tool output."""
        check = ctx.deps._tool_permission_checks.get(ctx.tool_call_id or "")
        evidence = ReviewEvidence(
            run_id=ctx.deps.run_id,
            tool_call_id=ctx.tool_call_id or "",
            tool_id=tool_identity(tool.tool_def).tool_id,
            binding=check.binding if check is not None else "",
            kind="action",
            target=compact_target(tool_args),
            approved=check.approval.approved if check is not None else False,
            outcome="unknown",
        )
        # A started dispatch may have side effects even if it raises or is cancelled.
        # Persist unknown first, then update the same bounded receipt on return.
        await append_review_evidence(ctx.deps, evidence)
        result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
        content = result.return_value if isinstance(result, ToolReturn) else result
        outcome = (
            "tool_reported_failure" if isinstance(content, dict) and content.get("ok") is False else "tool_returned"
        )
        await append_review_evidence(ctx.deps, evidence.model_copy(update={"outcome": outcome}))
        return result

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
                return await self._call_observed(name, deepcopy(tool_args), ctx, tool)
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


_POLICY_APPROVAL_METADATA_KEY = "a13n.harness.invocation-policy"


async def _evaluate_policy(
    ctx: RunContext[AgentContext],
    policy: InvocationPolicyCapability,
    invocation: ToolInvocationContext,
    metadata: HarnessToolMetadata,
) -> InvocationPolicyDecision:
    decision = await policy.evaluator(invocation, metadata, context=ctx.deps)
    if not isinstance(decision, InvocationPolicyDecision):
        raise DefinitionError("Invocation policy returned an invalid decision.", code="invocation_policy_invalid")
    return decision


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
        projected = project_json(typed_arguments)
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
        except (EnvironmentError, EnvironmentActivationError):
            raise
        except Exception as exc:
            logger.warning(
                "managed_tool_resource_resolution_failed",
                extra={
                    "run_id": ctx.deps.run_id,
                    "tool_call_id": ctx.tool_call_id,
                    "tool_name": tool_name,
                    "exception_chain": exception_details(exc),
                },
            )
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
    from a13n_harness.toolsets.tool_proxy import ToolProxySurfaceToolset

    current = toolset
    if isinstance(current, CodeActToolset):
        current = current.wrapped
    if isinstance(current, ToolProxySurfaceToolset):
        current = current.wrapped
    if not isinstance(current, ToolSurfaceToolset):
        raise DefinitionError(
            "Only CodeAct and ToolProxy may wrap the mandatory tool surface inside the execution boundary.",
            code="tool_surface_order_invalid",
            details={"toolset_type": type(current).__name__},
        )


def _validate_client_run_attachment(ctx: RunContext[AgentContext]) -> None:
    from a13n_harness.tools.client import (
        CLIENT_TOOLS_CAPABILITY_ID,
        ClientToolsCapability,
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

    if ctx.deps.client_toolsets is not None and owner is None:
        raise DefinitionError(
            "RunBindings.client_toolsets requires ClientToolsCapability in the Agent definition.",
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
    # Native deferred dispatch precedes the first model step. Later steps have
    # consumed this batch and may prepare a different dynamic tool surface.
    recovery = ctx.deps._tool_recovery
    requests = resume.requests if resume is not None else recovery.retained_requests if recovery is not None else None
    if requests is None or ctx.run_step != 0 or ctx.run_id != ctx.deps._model_recovery.attempt_id:
        return
    results = resume.results if resume is not None else recovery.results if recovery is not None else None
    for call in requests.calls:
        name = call.tool_name
        tool = tools.get(name)
        function_id = requests.metadata.get(call.tool_call_id, {}).get("a13n.harness.deferred-function-id")
        negative_closure = (
            tool is None
            and function_id is None
            and results is not None
            and isinstance(results.calls.get(call.tool_call_id), ToolFailed)
        )
        matches = negative_closure or (
            tool is not None
            and (
                (tool.tool_def.kind == "external" and function_id is None)
                or (
                    function_id is not None
                    and tool.tool_def.kind in {"function", "unapproved"}
                    and tool_identity(tool.tool_def).tool_id == function_id
                )
            )
        )
        if not matches:
            raise DefinitionError(
                "The current tool surface does not match the pending external call.",
                code="deferred_surface_mismatch",
                details={"tool_name": name},
            )

    for request in requests.approvals:
        tool = tools.get(request.tool_name)
        if tool is None or tool.tool_def.kind not in {"function", "unapproved"}:
            raise DefinitionError(
                "The current tool surface does not match the pending approval.",
                code="deferred_surface_mismatch",
                details={"tool_name": request.tool_name},
            )
