"""Invocation-scoped permission/review checks before tool-owned validation and dispatch."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass, replace
from typing import Any, cast

from pydantic import JsonValue, TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, ToolFailed
from pydantic_ai.messages import ModelRequest, TextContent
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness._json import dump_json_bytes, project_json, redact_json
from a13n_harness._review_context import (
    ReviewEvidence,
    append_review_evidence,
    read_review_history,
    select_review_history,
)
from a13n_harness.capabilities.tool_review import (
    ToolReviewError,
    ToolReviewRequest,
    ToolReviewResultPayload,
)
from a13n_harness.content import request_input_content
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.events import HarnessExtensionEvent
from a13n_harness.observation import observe_operation, observe_output
from a13n_harness.tools._source import tool_call_source, tool_call_source_scope
from a13n_harness.tools.approval import (
    APPROVAL_PRESENTATION_KEY,
    ToolApprovalContext,
    approval_presentation,
    approval_required,
    compact_target,
    resolve_tool_approval,
    tool_approval_scope,
)
from a13n_harness.tools.identity import ToolPermissionMode, tool_identity
from a13n_harness.tools.permissions import TOOL_PERMISSIONS_CAPABILITY_ID, ToolPermissionsCapability
from a13n_harness.tools.policy import InvocationDecisionKind

_JSON = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True, slots=True)
class PermissionCheck:
    binding: str
    approval: ToolApprovalContext
    mode: ToolPermissionMode


def permission_mode(ctx: RunContext[AgentContext], tool_def: ToolDefinition) -> ToolPermissionMode:
    identity = tool_identity(tool_def)
    capability = ctx.capabilities.get(TOOL_PERMISSIONS_CAPABILITY_ID)
    if capability is None:
        return identity.default_mode
    if not isinstance(capability, ToolPermissionsCapability):
        raise DefinitionError("Incompatible tool permissions.", code="capability_type_mismatch")
    return capability.permissions.resolve(identity)


def _arguments(args: dict[str, Any]) -> dict[str, JsonValue]:
    try:
        return _JSON.validate_python(project_json(args), strict=True)
    except (TypeError, ValueError) as exc:
        raise ToolFailed("Tool arguments cannot be represented for permission checks.") from exc


def _binding(tool_def: ToolDefinition, args: dict[str, JsonValue]) -> str:
    return hashlib.sha256(
        dump_json_bytes(
            {
                "tool_id": tool_identity(tool_def).tool_id,
                "schema": tool_def.parameters_json_schema,
                "arguments": args,
                "kind": tool_def.kind,
            },
            sort_keys=True,
        )
    ).hexdigest()


async def check_permission(
    ctx: RunContext[AgentContext], tool_def: ToolDefinition, args: dict[str, Any]
) -> PermissionCheck:
    mode = permission_mode(ctx, tool_def)
    if mode == "deny":
        raise ToolFailed("Tool invocation is denied by its permission configuration.")
    arguments = _arguments(args)
    binding = _binding(tool_def, arguments)
    identity = tool_identity(tool_def)
    approval = resolve_tool_approval(ctx, tool_id=identity.tool_id)
    cached = ctx.deps._tool_permission_checks.get(ctx.tool_call_id or "")
    if cached is not None and cached.binding == binding and cached.approval == approval and cached.mode == mode:
        return cached
    if approval.approved:
        await append_review_evidence(
            ctx.deps,
            ReviewEvidence(
                run_id=ctx.deps.run_id,
                tool_call_id=approval.tool_call_id,
                tool_id=identity.tool_id,
                binding=binding,
                kind="approval",
                approved=approval.approved,
                target=compact_target(arguments),
            ),
        )
    if mode == "ask" and not approval.approved:
        raise approval_required(
            ctx,
            approval,
            metadata={
                APPROVAL_PRESENTATION_KEY: approval_presentation(
                    arguments,
                    tool_id=approval.tool_id,
                    reason="permission",
                ),
                "reason": "Tool permission configuration requires approval.",
            },
        )
    if mode == "review":
        capability = ctx.capabilities.get(TOOL_PERMISSIONS_CAPABILITY_ID)
        if capability is not None:
            if not isinstance(capability, ToolPermissionsCapability):
                raise DefinitionError("Incompatible tool reviewer.", code="capability_type_mismatch")
            if capability.has_reviewer(identity.tool_id):
                await _review(ctx, tool_def, arguments, approval, binding, capability)
    result = PermissionCheck(binding, approval, mode)
    ctx.deps._tool_permission_checks[ctx.tool_call_id or ""] = result
    return result


async def _review(
    ctx: RunContext[AgentContext],
    tool_def: ToolDefinition,
    arguments: dict[str, JsonValue],
    approval: ToolApprovalContext,
    binding: str,
    capability: ToolPermissionsCapability,
) -> None:
    review_metadata: dict[str, JsonValue] = {}
    result = None
    try:
        request = await _review_request(ctx, tool_def, arguments, approval, binding)
        request.to_prompt()  # Enforce the same escaped-byte budget for custom reviewers.
        with observe_operation("tool_review", capability_id=capability.id, operation_id=approval.tool_call_id) as span:
            span.set_attribute("a13n.tool.id", approval.tool_id)
            span.set_attribute("a13n.tool.call.id", approval.tool_call_id)
            result = await capability.review(request, context=ctx.deps)
            if result is not None:
                observe_output(span, result.assessment.model_dump(mode="json"), status="completed")
    except ToolReviewError as exc:
        for usage in exc.usage:
            await ctx.deps.record_provider_usage(
                usage,
                source="tool.review",
                tool_id=approval.tool_id,
                tool_call_id=approval.tool_call_id,
            )
        decision = capability.config.on_error if capability.config is not None else "approval_required"
        if exc.code == "tool_review_timeout":
            decision = "deny"
        reason = "Tool review timed out." if exc.code == "tool_review_timeout" else "Tool review could not complete."
        await append_review_evidence(
            ctx.deps,
            ReviewEvidence(
                run_id=ctx.deps.run_id,
                tool_call_id=approval.tool_call_id,
                tool_id=approval.tool_id,
                binding=binding,
                kind="review",
                decision=cast(InvocationDecisionKind, decision),
                reason=reason,
                approved=approval.approved,
                target=compact_target(arguments),
            ),
        )
        await ctx.deps.events.emit(
            HarnessExtensionEvent(
                kind="tool",
                payload=ToolReviewResultPayload(
                    tool_id=approval.tool_id,
                    tool_call_id=approval.tool_call_id,
                    status="error",
                    error_code=exc.code,
                    decision=cast(InvocationDecisionKind, decision),
                    result=None,
                ).model_dump(mode="json"),
            )
        )
        if exc.code == "tool_review_timeout":
            raise ToolFailed("Tool review timed out; the tool was not executed.") from exc
    else:
        if result is None:
            return
        for usage in result.usage:
            await ctx.deps.record_provider_usage(
                usage,
                source="tool.review",
                tool_id=approval.tool_id,
                tool_call_id=approval.tool_call_id,
            )
        decision, reason = capability.decision_for(approval.tool_id, result.assessment), result.assessment.reason
        review_metadata["a13n.harness.tool-review"] = result.assessment.model_dump(mode="json")
        await ctx.deps.events.emit(
            HarnessExtensionEvent(
                kind="tool",
                payload=ToolReviewResultPayload(
                    tool_id=approval.tool_id,
                    tool_call_id=approval.tool_call_id,
                    status="completed",
                    result=result,
                    decision=decision,
                ).model_dump(mode="json", exclude={"error_code"}),
            )
        )
        await append_review_evidence(
            ctx.deps,
            ReviewEvidence(
                run_id=ctx.deps.run_id,
                tool_call_id=approval.tool_call_id,
                tool_id=approval.tool_id,
                binding=binding,
                kind="review",
                decision=decision,
                reason=reason[:400] if reason is not None else None,
                risk=result.assessment.risk.value,
                approved=approval.approved,
                target=compact_target(arguments),
            ),
        )
    if decision == "deny":
        message = "Tool review denied the invocation."
        if reason is not None:
            message = f"Tool review denied the invocation: {reason}"
        raise ToolFailed(message)
    if decision == "approval_required" and not approval.approved:
        raise approval_required(
            ctx,
            approval,
            metadata={
                APPROVAL_PRESENTATION_KEY: approval_presentation(
                    arguments,
                    tool_id=approval.tool_id,
                    reason="review" if result is not None else "review_error",
                    risk=result.assessment.risk.value if result is not None else None,
                ),
                "reason": reason,
                **review_metadata,
            },
        )


async def _review_request(
    ctx: RunContext[AgentContext],
    tool_def: ToolDefinition,
    arguments: dict[str, JsonValue],
    approval: ToolApprovalContext,
    binding: str,
) -> ToolReviewRequest:
    omitted: list[str] = []
    projected = dict(arguments)
    if isinstance(projected.get("environment"), dict):
        projected["environment"] = cast(
            JsonValue, {"keys": sorted(cast(dict[str, JsonValue], projected["environment"]))}
        )
        omitted.append("arguments.environment.values")
    redacted = cast(dict[str, JsonValue], redact_json(projected))
    if redacted != projected:
        omitted.append("arguments.sensitive_fields")
    tasks: list[str] = []
    for message in reversed(ctx.messages):
        if isinstance(message, ModelRequest):
            text = [
                item.value if isinstance(item.value, str) else item.value.content
                for item in request_input_content(message)
                if item.metadata.display and isinstance(item.value, str | TextContent)
            ]
            if text:
                task_text = "\n".join(text)
                if len(task_text) > 2048:
                    task_text = task_text[:2048]
                    omitted.append("task")
                tasks.append(task_text)
                if len(tasks) == 2:
                    break
    task = "\n\n".join(reversed(tasks)) or None
    description = tool_def.description
    if description is not None and len(description) > 1024:
        description = description[:1024]
        omitted.append("description")
    snapshot = ctx.deps.environment.snapshot
    if len(snapshot.mounts) > 16:
        omitted.append("context.mounts")
    mounts: list[JsonValue] = []
    for mount in snapshot.mounts[:16]:
        mounts.append(
            {
                "name": mount.name,
                "provider_type": mount.provider_type,
                "root": mount.mount_path,
                "default_working_directory": mount.default_working_directory,
                "operations": [str(action) for action in sorted(mount.permission_ceiling.operations)],
            }
        )
    environment_context: dict[str, JsonValue] = {"default_mount": snapshot.default_mount, "mounts": mounts}
    history = await read_review_history(ctx.deps)
    previous_reviews, recent_actions = select_review_history(
        history,
        tool_id=approval.tool_id,
        tool_call_id=approval.tool_call_id,
        binding=binding,
    )
    if len(history.records) > len(previous_reviews) + len(recent_actions):
        omitted.append("history.older_entries")
    return ToolReviewRequest(
        source=tool_call_source(ctx),
        previous_reviews=previous_reviews,
        recent_actions=recent_actions,
        approved=approval.approved,
        context=environment_context,
        tool_id=tool_identity(tool_def).tool_id,
        tool_call_id=ctx.tool_call_id or "",
        tool_name=tool_def.name,
        description=description,
        parameters_schema=tool_def.parameters_json_schema,
        arguments=redacted,
        task=task,
        omitted=tuple(omitted),
        profile="shell" if tool_identity(tool_def).tool_id == "environment.shell_exec" else "general",
    )


def gate_tool(tool: ToolsetTool[AgentContext]) -> ToolsetTool[AgentContext]:
    """Prepend the permission gate to the native post-schema argument validator."""
    original_validator = tool.args_validator_func

    async def validate(ctx: RunContext[AgentContext], **args: Any) -> None:
        check = await check_permission(ctx, tool.tool_def, args)
        ctx = replace(ctx, tool_call_approved=check.approval.approved)
        with tool_approval_scope(ctx.deps, check.approval), tool_call_source_scope(ctx):
            if original_validator is not None:
                try:
                    result = original_validator(ctx, **args)
                    if inspect.isawaitable(result):
                        await result
                except ApprovalRequired as exc:
                    raise approval_required(
                        ctx,
                        check.approval,
                        metadata={
                            **(exc.metadata or {}),
                            APPROVAL_PRESENTATION_KEY: approval_presentation(args, tool_id=check.approval.tool_id),
                        },
                    ) from exc
            if tool.tool_def.kind == "unapproved" and not ctx.tool_call_approved:
                ctx.deps._tool_pending_approvals[check.approval.tool_call_id] = {
                    APPROVAL_PRESENTATION_KEY: approval_presentation(args, tool_id=check.approval.tool_id),
                }

    return replace(tool, args_validator_func=validate)
