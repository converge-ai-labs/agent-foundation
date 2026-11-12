"""Text measurements of the model-neutral input to one generation."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeGuard, get_args

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import (
    AudioUrl,
    BaseToolCallPart,
    BaseToolReturnPart,
    BinaryContent,
    CachePoint,
    CapabilityInstructionSource,
    CapabilityLoadDeltaPart,
    CompactionPart,
    InstructionBaselinePart,
    InstructionDeltaPart,
    InstructionId,
    ModelMessage,
    ModelRequest,
    RetryFeedbackPart,
    RetryPromptPart,
    SpeechPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolAvailabilityDeltaPart,
    ToolsetInstructionSource,
    UploadedFile,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters, _instruction_parts_of

from a13n_harness._json import dump_json_text
from a13n_harness.content import prompt_content

type ContextGroupName = Literal[
    "system", "tools", "skills", "memory", "environment_context", "runtime_state", "conversation", "tool_calls", "other"
]


class ContextItemUsage(BaseModel):
    characters: int = Field(ge=0)
    calls: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)
    argument_characters: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)
    result_characters: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)


@dataclass(frozen=True)
class InstructionCatalog:
    group: ContextGroupName
    text: str
    items: dict[str, ContextItemUsage]


class ContextGroupUsage(BaseModel):
    characters: int = Field(ge=0)
    items: dict[str, ContextItemUsage] = Field(default_factory=dict)
    omitted_items: int = Field(default=0, ge=0, exclude_if=lambda n: n == 0)


class ContextBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: Literal["text_characters_v1"] = "text_characters_v1"
    unmeasured_content: tuple[Literal["audio", "other"], ...] = ()
    counted_input_characters: int = Field(ge=0)
    groups: dict[ContextGroupName, ContextGroupUsage]


_CONTEXT_GROUPS = frozenset(get_args(ContextGroupName.__value__))


def _is_context_group(value: object) -> TypeGuard[ContextGroupName]:
    return isinstance(value, str) and value in _CONTEXT_GROUPS


# Compatibility with persisted source IDs, including histories written before blocks carried a group.
# New producers declare their group on the block or catalog.
def _untagged_source_group(source: str | None, default: ContextGroupName) -> ContextGroupName:
    if source == "a13n.skills":
        return "skills"
    if source in ("a13n.memory.file", "a13n.memory.record"):
        return "memory"
    if source in ("a13n.workspace-outline", "a13n.file-context", "a13n.environment-mounts"):
        return "environment_context"
    if source == "a13n.handoff":
        return "conversation"
    if source == "a13n.tool-discovery":
        return "tools"
    if source in (
        "a13n.agent-context",
        "a13n.runtime-context",
        "a13n.async_subagent",
        "a13n.background_process",
        "a13n.envd_session",
        "tool_execution.summary",
        "codeact.stored_keys",
    ) or (source or "").startswith("a13n.working-state"):
        return "runtime_state"
    return default


def _instruction_group(key: InstructionId | None) -> ContextGroupName:
    source = key.source if key else None
    if isinstance(source, ToolsetInstructionSource):
        return "tools"
    return _untagged_source_group(source.id if isinstance(source, CapabilityInstructionSource) else None, "system")


def measure_context(
    messages: Sequence[ModelMessage],
    params: ModelRequestParameters,
    catalogs: Mapping[str, InstructionCatalog] | None = None,
) -> ContextBreakdown:
    from a13n_harness.tools.tool_search import proxy_control, valid_group_name, valid_tool_name
    from a13n_harness.toolsets.memory_files import FILE_READ_TOOL_NAMES
    from a13n_harness.toolsets.memory_records import RECORD_READ_TOOL_NAMES

    totals: Counter[ContextGroupName] = Counter()
    details: dict[ContextGroupName, Counter[str]] = defaultdict(Counter)
    calls: Counter[str] = Counter()
    arguments: Counter[str] = Counter()
    results: Counter[str] = Counter()
    unmeasured: set[Literal["audio", "other"]] = {"other"} if params.native_tools else set()
    call_names: dict[tuple[str, str], str] = {}

    def media(value: object) -> None:
        is_audio = isinstance(value, AudioUrl) or (
            isinstance(value, (BinaryContent, UploadedFile)) and (value.media_type or "").startswith("audio/")
        )
        unmeasured.add("audio" if is_audio else "other")

    def instruction(key: InstructionId | None, content: str) -> None:
        catalog = catalogs.get(str(key.source)) if catalogs and key else None
        group = catalog.group if catalog else _instruction_group(key)
        totals[group] += len(content)
        if catalog and content == catalog.text:
            for name, entry in catalog.items.items():
                details[group][name] += entry.characters

    # Measure authored instructions, before provider-specific framing is added.
    for block in _instruction_parts_of(messages, params) or ():
        if params.instruction_visibility_of(block) == "prefix":
            instruction(block.id, block.content)
    for name, tool in params.tool_defs.items():
        visibility = params.visibility_of(name)
        if visibility != "visible" and name not in params.revealed_tool_names:
            continue
        definition = {"name": name, "description": tool.description or "", "parameters": tool.parameters_json_schema}
        size = len(dump_json_text(definition))
        details["tools"][name] += size
        totals["tools"] += size
    for message in messages:
        for index, part in enumerate(message.parts):
            if isinstance(part, UserPromptPart) and isinstance(message, ModelRequest):
                for item in prompt_content(message, index):
                    source = item.metadata.source_id
                    extra = item.metadata.model_extra or {}
                    declared = extra.get("context_group")
                    group = (
                        declared
                        if _is_context_group(declared)
                        else _untagged_source_group(
                            source, "other" if source and not item.metadata.display else "conversation"
                        )
                    )
                    if isinstance(item.value, (str, TextContent)):
                        size = len(item.value if isinstance(item.value, str) else item.value.content)
                        totals[group] += size
                        mount = extra.get("memory_mount")
                        if group == "memory" and isinstance(mount, str):
                            details[group][mount] += size
                    elif not isinstance(item.value, CachePoint):
                        media(item.value)
            elif isinstance(part, BaseToolCallPart):
                name = part.tool_name
                tool_def = params.tool_defs.get(name)
                if tool_def is not None and proxy_control(tool_def) == "call":
                    proxy = part.args_as_dict()
                    target, namespace = proxy.get("tool"), proxy.get("group")
                    if valid_group_name(namespace) and valid_tool_name(target):
                        name = f"{namespace}.{target}"
                        call_names[part.tool_call_id, part.tool_name] = name
                calls[name] += 1
                arguments[name] += len(part.args_as_json_str()) if part.args is not None else 0
            elif isinstance(part, BaseToolReturnPart):
                name = call_names.get((part.tool_call_id, part.tool_name), part.tool_name)
                size = len(part.model_response_str())
                mount = part.content.get("memory") if isinstance(part.content, dict) else None
                tool_name = name.rsplit(".", 1)[-1]
                if (
                    (tool_name in FILE_READ_TOOL_NAMES or tool_name in RECORD_READ_TOOL_NAMES)
                    and part.outcome == "success"
                    and isinstance(mount, str)
                    and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", mount)
                ):
                    totals["memory"] += size
                    details["memory"][mount] += size
                else:
                    results[name] += size
                for file in part.files:
                    media(file)
            elif isinstance(part, (TextPart, ThinkingPart, CompactionPart)):
                totals["conversation"] += len(part.content or "")
                if isinstance(part, CompactionPart) and part.content is None:
                    unmeasured.add("other")
            elif isinstance(part, SystemPromptPart):
                totals["system"] += len(part.content)
            elif isinstance(part, InstructionDeltaPart):
                instruction(part.id, part.content or "")
            elif isinstance(part, RetryPromptPart) and part.tool_name:
                results[part.tool_name] += len(part.model_response())
            elif isinstance(part, (RetryFeedbackPart, RetryPromptPart)):
                totals["other"] += len(part.model_response())
            elif isinstance(part, SpeechPart):
                totals["conversation"] += len(part.transcript or "")
                if part.audio is not None:
                    media(part.audio)
            elif not isinstance(part, (InstructionBaselinePart, CapabilityLoadDeltaPart, ToolAvailabilityDeltaPart)):
                # Media, opaque provider data or an unsupported history part has no reliable text weight.
                unmeasured.add("other")

    for name in calls.keys() | results.keys():
        size = arguments[name] + results[name]
        totals["tool_calls"] += size
        details["tool_calls"][name] = size

    groups: dict[ContextGroupName, ContextGroupUsage] = {
        group: ContextGroupUsage(characters=size, omitted_items=len(details[group]))
        for group, size in totals.items()
        if size or details[group]
    }
    # Take the largest item from each category before considering the next rank.
    by_rank: list[tuple[int, ContextGroupName, str, int]] = sorted(
        (rank, group, name, chars)
        for group in groups
        for rank, (name, chars) in enumerate(sorted(details[group].items(), key=lambda item: (-item[1], item[0])))
    )
    remaining = 24 * 1024
    for _, group, name, chars in by_rank:
        metric = ContextItemUsage(
            characters=chars,
            calls=calls[name] if group == "tool_calls" else None,
            argument_characters=arguments[name] if group == "tool_calls" else None,
            result_characters=results[name] if group == "tool_calls" else None,
        )
        cost = len(dump_json_text({name: metric.model_dump()}).encode())
        if cost <= remaining:
            groups[group].items[name] = metric
            groups[group].omitted_items -= 1
            remaining -= cost

    return ContextBreakdown(
        counted_input_characters=sum(totals.values()), groups=groups, unmeasured_content=tuple(sorted(unmeasured))
    )
