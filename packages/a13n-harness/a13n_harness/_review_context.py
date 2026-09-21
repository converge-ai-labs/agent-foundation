"""Compact review evidence and one bounded XML input format (never authority)."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied

from a13n_harness._json import dump_json_text

if TYPE_CHECKING:
    from a13n_harness.context import AgentContext

REVIEW_HISTORY_ID = "a13n.tool-execution-boundary.review-history"
MAX_REVIEW_INPUT_BYTES = 64 * 1024
_REVIEW_TARGET_BYTES = 16 * 1024


class ReviewEvidence(BaseModel):
    """A flat observation at one point in time, without arguments or results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    tool_call_id: str
    tool_id: str
    binding: str
    target: str = Field(default="", max_length=240)
    kind: Literal["review", "action", "approval"]
    decision: Literal["allow", "deny", "approval_required"] | None = None
    reason: str = Field(default="", max_length=400)
    risk: Literal["low", "medium", "high", "extra_high"] | None = None
    approved_sources: tuple[Literal["permission", "reviewer", "tool"], ...] = ()
    denied_sources: tuple[Literal["permission", "reviewer", "tool"], ...] = ()
    outcome: Literal["not_executed", "tool_returned", "tool_reported_failure", "unknown"] = "not_executed"


class ReviewHistory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    records: tuple[ReviewEvidence, ...] = Field(default=(), max_length=48)


async def read_review_history(context: AgentContext) -> ReviewHistory:
    return await context.state.read(REVIEW_HISTORY_ID, ReviewHistory, version="1") or ReviewHistory()


async def append_review_evidence(context: AgentContext, record: ReviewEvidence) -> None:
    # Serialize the entire read/modify/write, not model or tool execution.
    async with context._review_history_lock:
        history = await read_review_history(context)
        records = tuple(item for item in history.records if item.evidence_id != record.evidence_id)
        await context.state.write(REVIEW_HISTORY_ID, ReviewHistory(records=(*records[-47:], record)), version="1")


async def record_approval_denials(
    requests: DeferredToolRequests | None,
    results: DeferredToolResults,
    *,
    context: AgentContext,
) -> None:
    """Observe the Host approval channel, not tool-return text or result metadata."""
    from a13n_harness.tools.approval import NATIVE_TOOL_APPROVAL_KEY, TOOL_APPROVAL_KEY

    if not context.deferred_tools_supported:
        return  # Automatic unsupported-Host denials are not human feedback.
    pending = requests.metadata if requests is not None else context._tool_pending_approvals
    for call_id, result in results.approvals.items():
        if result is not False and not isinstance(result, ToolDenied):
            continue
        metadata = pending.get(call_id, {})
        facts = metadata.get(TOOL_APPROVAL_KEY, metadata.get(NATIVE_TOOL_APPROVAL_KEY))
        if not isinstance(facts, dict):
            continue
        tool_id, binding = facts.get("tool_id"), facts.get("binding")
        sources = facts.get("requested_sources")
        if not isinstance(tool_id, str) or not isinstance(binding, str) or not isinstance(sources, list):
            continue
        if not sources or any(source not in ("permission", "reviewer", "tool") for source in sources):
            continue
        await append_review_evidence(
            context,
            ReviewEvidence.model_validate(
                {
                    "evidence_id": f"denied:{context.run_id}:{call_id}:{binding}",
                    "run_id": context.run_id,
                    "tool_id": tool_id,
                    "tool_call_id": call_id,
                    "binding": binding,
                    "kind": "approval",
                    "decision": "deny",
                    "denied_sources": sources,
                    "reason": (
                        result.message[:400]
                        if isinstance(result, ToolDenied) and result.message
                        else "Approval channel denied the request."
                    ),
                }
            ),
        )


def select_review_history(
    history: ReviewHistory, *, tool_id: str, tool_call_id: str, binding: str
) -> tuple[tuple[ReviewEvidence, ...], tuple[ReviewEvidence, ...]]:
    recent = list(reversed(history.records))
    reviews = [record for record in recent if record.kind == "review"]
    reviews.sort(
        key=lambda record: (
            0
            if record.tool_call_id == tool_call_id and record.binding == binding
            else 1
            if record.binding == binding
            else 2
            if record.tool_id == tool_id
            else 3
        )
    )
    actions = [record for record in recent if record.kind != "review"][:8]
    return tuple(reviews[:5]), tuple(reversed(actions))


def _element(name: str, text: str) -> str:
    return f"<{name}>{escape(text, quote=False)}</{name}>"


def _value(value: JsonValue) -> str:
    if isinstance(value, str):
        return _element("string", value)
    if isinstance(value, dict):
        fields = "".join(
            f'<field name="{escape(key, quote=True)}">{_value(item)}</field>' for key, item in value.items()
        )
        return f"<object>{fields}</object>"
    if isinstance(value, list):
        items = "".join(f"<item>{_value(item)}</item>" for item in value)
        return f"<array>{items}</array>"
    if value is None:
        return "<null/>"
    return _element("boolean" if isinstance(value, bool) else "number", dump_json_text(value))


def _arguments(tool_id: str, arguments: dict[str, JsonValue]) -> str:
    if tool_id != "environment.shell_exec":
        return _value(arguments)
    # Shell is an input-format specialization, not another reviewer or policy.
    execution = dict(arguments)
    command = f"<command>{_value(execution.pop('command'))}</command>" if "command" in execution else ""
    return command + f"<execution>{_value(execution)}</execution>"


def _evidence(record: ReviewEvidence) -> str:
    facts = {
        "tool": record.tool_id,
        "call": record.tool_call_id,
        "outcome": record.outcome,
    }
    if record.risk is not None:
        facts["risk"] = record.risk
    if record.decision is not None:
        facts["policy-decision"] = record.decision
    if record.approved_sources:
        facts["verified-approval-sources"] = ",".join(record.approved_sources)
    if record.denied_sources:
        facts["denied-approval-sources"] = ",".join(record.denied_sources)
    attrs = " ".join(f'{name}="{escape(value, quote=True)}"' for name, value in facts.items())
    text = " | ".join(value for value in (record.target, record.reason) if value)
    return f"<entry {attrs}>{escape(text, quote=False)}</entry>"


def render_review_input(
    *,
    tool_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, JsonValue],
    parameters_schema: dict[str, JsonValue],
    task: str | None = None,
    description: str | None = None,
    context: dict[str, JsonValue] | None = None,
    approved_sources: tuple[str, ...] = (),
    previous_reviews: tuple[ReviewEvidence, ...] = (),
    recent_actions: tuple[ReviewEvidence, ...] = (),
    omitted: tuple[str, ...] = (),
) -> str:
    """Prioritize the complete current call; budget *after* escaping UTF-8 text.

    Normal context targets 16 KiB. A larger current call may use the 64 KiB hard
    limit. Never silently truncate current arguments or misrepresent JSON Schema.
    """
    from a13n_harness.capabilities.tool_review import ToolReviewError

    current = (
        "<current-call>"
        + _element("tool-id", tool_id)
        + _element("tool-call-id", tool_call_id)
        + _element("tool-name", tool_name)
        + f"<arguments>{_arguments(tool_id, arguments)}</arguments>"
        + _element("parameters-schema", dump_json_text(parameters_schema))
        + _element("verified-approval-sources", ", ".join(approved_sources) or "none")
        + "</current-call>"
    )
    sections: list[str] = []
    omissions = list(omitted)
    base = "<tool-review>\n" + current
    # Reserve room for section delimiters and explicit omissions.
    budget = max(_REVIEW_TARGET_BYTES, len(base.encode()) + 1024)
    budget = min(budget, MAX_REVIEW_INPUT_BYTES)

    def add(name: str, body: str) -> None:
        section = f"\n<{name}>{body}</{name}>"
        if len((base + "".join(sections) + section).encode()) <= budget - 512:
            sections.append(section)
        else:
            omissions.append(name)

    if task:
        add("task", escape(task, quote=False))
    if context:
        add("environment", _value(context))
    # Ranked reviews are more relevant than old actions. Add entries individually
    # so a long tail cannot displace the closest match or the current call.
    for review in previous_reviews[:5]:
        add("previous-review", _evidence(review))
    for action in reversed(recent_actions[-8:]):
        add("recent-action", _evidence(action))
    if description:
        add("description", escape(description, quote=False))
    if len(previous_reviews) > 5:
        omissions.append("previous-reviews")
    if len(recent_actions) > 8:
        omissions.append("recent-actions")
    prompt = base + "".join(sections)
    if omissions:
        prompt += "\n" + _element("omitted", ", ".join(dict.fromkeys(omissions)))
    prompt += "\n</tool-review>"
    if len(prompt.encode()) > MAX_REVIEW_INPUT_BYTES:
        raise ToolReviewError("tool_review_input_too_large")
    return prompt
