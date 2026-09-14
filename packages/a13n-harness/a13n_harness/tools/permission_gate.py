"""Invocation-scoped permission/review checks before tool-owned validation and dispatch."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass, replace
from typing import Any, cast

from pydantic import JsonValue, TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, ToolFailed
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness._json import dump_json_bytes, redact_json
from a13n_harness.capabilities.shell_review import SHELL_REVIEW_CAPABILITY_ID, ShellReviewCapability
from a13n_harness.capabilities.tool_review import (
    TOOL_REVIEW_CAPABILITY_ID,
    ToolReviewCapability,
    ToolReviewError,
    ToolReviewRequest,
    ToolReviewResultPayload,
)
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.events import HarnessExtensionEvent
from a13n_harness.observation import observe_operation, observe_output
from a13n_harness.tools.approval import (
    ToolApprovalContext,
    approval_required,
    resolve_tool_approval,
    tool_approval_scope,
)
from a13n_harness.tools.identity import ToolPermissionMode, tool_identity
from a13n_harness.tools.permissions import TOOL_PERMISSIONS_CAPABILITY_ID, ToolPermissionsCapability
from a13n_harness.tools.policy import InvocationDecisionKind

_JSON = TypeAdapter(dict[str, JsonValue])
_ANY = TypeAdapter(Any)


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
        return _JSON.validate_python(_ANY.dump_python(args, mode="json", warnings="error"), strict=True)
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
    approval = resolve_tool_approval(ctx, tool_id=identity.tool_id, binding=binding)
    cached = ctx.deps._tool_permission_checks.get(ctx.tool_call_id or "")
    if cached is not None and cached.binding == binding and cached.approval == approval and cached.mode == mode:
        return cached
    if mode == "ask" and "permission" not in approval.approved_sources:
        raise approval_required(
            ctx,
            approval,
            binding=binding,
            sources=frozenset({"permission"}),
            metadata={
                "reason": "Tool permission configuration requires approval.",
            },
        )
    if mode == "review":
        capability = ctx.capabilities.get(TOOL_REVIEW_CAPABILITY_ID) or ctx.capabilities.get(SHELL_REVIEW_CAPABILITY_ID)
        if capability is not None:
            if not isinstance(capability, ToolReviewCapability | ShellReviewCapability):
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
    capability: ToolReviewCapability | ShellReviewCapability,
) -> None:
    try:
        request = _review_request(ctx, tool_def, arguments)
        if len(request.model_dump_json().encode()) > 64 * 1024:
            raise ToolReviewError("tool_review_input_too_large")
        with observe_operation("tool_review", capability_id=capability.id, operation_id=approval.tool_call_id) as span:
            span.set_attribute("a13n.tool.id", approval.tool_id)
            span.set_attribute("a13n.tool.call.id", approval.tool_call_id)
            result = (
                await capability.review_tool(request, context=ctx.deps)
                if isinstance(capability, ShellReviewCapability)
                else await capability.review(request, context=ctx.deps)
            )
            if result is not None:
                observe_output(span, result.assessment.model_dump(mode="json"), status="completed")
    except ToolReviewError as exc:
        for usage in exc.usage:
            await ctx.deps.record_provider_usage(
                usage,
                source="shell.review" if isinstance(capability, ShellReviewCapability) else "tool.review",
                tool_id=approval.tool_id,
                tool_call_id=approval.tool_call_id,
            )
        decision = (
            capability.on_error.value
            if isinstance(capability, ShellReviewCapability)
            else capability.config.on_error
            if capability.config is not None
            else "approval_required"
        )
        if decision == "skip":
            decision = "allow"
        if exc.code == "tool_review_timeout":
            decision = "deny"
        reason = "Tool review timed out." if exc.code == "tool_review_timeout" else "Tool review could not complete."
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
                source="shell.review" if isinstance(capability, ShellReviewCapability) else "tool.review",
                tool_id=approval.tool_id,
                tool_call_id=approval.tool_call_id,
            )
        await ctx.deps.events.emit(
            HarnessExtensionEvent(
                kind="tool",
                payload=ToolReviewResultPayload(
                    tool_id=approval.tool_id,
                    tool_call_id=approval.tool_call_id,
                    status="completed",
                    result=result,
                ).model_dump(mode="json", exclude={"error_code", "decision"}),
            )
        )
        decision, reason = result.assessment.decision, result.assessment.reason
    if decision == "deny":
        raise ToolFailed(f"Tool review denied the invocation: {reason}")
    if decision == "approval_required" and "reviewer" not in approval.approved_sources:
        raise approval_required(
            ctx, approval, binding=binding, sources=frozenset({"reviewer"}), metadata={"reason": reason}
        )


def _review_request(
    ctx: RunContext[AgentContext], tool_def: ToolDefinition, arguments: dict[str, JsonValue]
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
    task = None
    for message in reversed(ctx.messages):
        if isinstance(message, ModelRequest):
            text = [
                part.content
                for part in message.parts
                if isinstance(part, UserPromptPart) and isinstance(part.content, str)
            ]
            if text:
                task = "\n".join(text)
                if len(task) > 8192:
                    task = task[:8192]
                    omitted.append("task")
                break
    description = tool_def.description
    if description is not None and len(description) > 8192:
        description = description[:8192]
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
    return ToolReviewRequest(
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
        with tool_approval_scope(ctx.deps, check.approval):
            if original_validator is not None:
                try:
                    result = original_validator(ctx, **args)
                    if inspect.isawaitable(result):
                        await result
                except ApprovalRequired as exc:
                    raise approval_required(
                        ctx, check.approval, binding=check.binding, sources=frozenset({"tool"}), metadata=exc.metadata
                    ) from exc

    return replace(tool, args_validator_func=validate)
