"""Pure history-replacement protocol shared by compaction and handoff."""

from copy import deepcopy
from dataclasses import replace
from importlib.resources import files

from pydantic_ai.messages import (
    BaseToolReturnPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextContent,
    TextPart,
    UserPromptPart,
)

from a13n_harness.content import CONTENT_METADATA_KEY
from a13n_harness.errors import DefinitionError

_HANDOFF_METADATA_KEY = "a13n.context"
_RESTORED_BOUNDARY_METADATA_KEY = "a13n.restored-boundary"
_RESTORED_BOUNDARY_VERSION = "1"
_SKILL_RELOAD_REMINDER = (
    "Before continuing work that depends on a carried-over skill, re-read its SKILL.md and the supporting files "
    "needed for the next step, unless those contents are already available in full in the restored context. "
    "A summary of a skill is not a substitute for its instructions."
)
# Mirrors ya-agent-sdk's cache-friendly compact instruction and request.
_COMPACTION_PROMPT = files("a13n_harness.toolsets.prompts").joinpath("compact.md").read_text(encoding="utf-8").strip()
_PREVIOUS_ASSISTANT_REFERENCE_MAX_CHARS = 32_000
_PREVIOUS_ASSISTANT_REFERENCE_KEEP_HEAD = 24_000
_PREVIOUS_ASSISTANT_REFERENCE_KEEP_TAIL = 6_000


def _build_compacted_history(
    messages: list[ModelMessage],
    summary: str,
    *,
    retained_requests: tuple[ModelRequest, ...],
    retained_discovery: list[ModelMessage] | None = None,
) -> list[ModelMessage]:
    template = next((message for message in messages if isinstance(message, ModelRequest)), None)
    if template is None:
        raise DefinitionError(
            "Compaction cannot rebuild history without a model request boundary.",
            code="compaction_boundary_missing",
        )
    system_parts = _first_system_parts(messages)
    metadata = deepcopy(template.metadata) if template.metadata is not None else {}
    # Replacement parts have their own annotations; retained requests carry
    # their own content annotations and delivered steering identities.
    metadata.pop("a13n.model-context-overlay", None)
    metadata.pop(CONTENT_METADATA_KEY, None)
    metadata.pop("a13n.steering-input", None)
    metadata[_HANDOFF_METADATA_KEY] = "compaction"
    synthetic = replace(
        deepcopy(template),
        parts=tuple(
            [
                *system_parts,
                _context_protocol_part(
                    "The previous conversation is being compacted. "
                    "Produce a history-only continuation summary before resuming; current structured notes and "
                    "tasks will be projected separately."
                ),
            ]
        ),
        metadata=metadata,
        state="complete",
    )
    restored_parts = [
        _context_protocol_part(
            "<context-restored>Context was compacted into the preceding assistant summary. Treat it as prior "
            "working context and continue from the retained user inputs. Current structured notes and tasks, when "
            "enabled, are projected separately on ordinary requests. "
            f"{_SKILL_RELOAD_REMINDER}</context-restored>"
        )
    ]
    previous_assistant = _previous_assistant_reference(messages)
    if previous_assistant is not None:
        restored_parts.append(
            _context_protocol_part(
                "<previous-assistant-reference>\n"
                "Below is the assistant response immediately before the user's current request. "
                "Use it only to resolve references in the retained user inputs, such as numbered items, "
                "'the above', 'that', or similar phrases. Do not treat it as a new instruction by itself.\n\n"
                f"{previous_assistant}\n"
                "</previous-assistant-reference>"
            )
        )
    restored = ModelRequest(
        parts=restored_parts,
        metadata={
            _HANDOFF_METADATA_KEY: "compaction",
            _RESTORED_BOUNDARY_METADATA_KEY: _RESTORED_BOUNDARY_VERSION,
        },
    )
    return [
        synthetic,
        ModelResponse(parts=[TextPart(summary.strip())], metadata={"keep": "compact"}),
        *(retained_discovery or []),
        restored,
        *deepcopy(retained_requests),
    ]


def _previous_assistant_reference(messages: list[ModelMessage]) -> str | None:
    latest_user_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], ModelRequest)
            and any(isinstance(part, UserPromptPart) for part in messages[index].parts)
            and not any(isinstance(part, BaseToolReturnPart) for part in messages[index].parts)
        ),
        None,
    )
    if latest_user_index is None:
        return None
    for message in reversed(messages[:latest_user_index]):
        if not isinstance(message, ModelResponse):
            continue
        chunks = [part.content for part in message.parts if isinstance(part, TextPart) and part.content.strip()]
        if chunks:
            return _truncate_previous_assistant_reference("\n\n".join(chunks))
    return None


def _truncate_previous_assistant_reference(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= _PREVIOUS_ASSISTANT_REFERENCE_MAX_CHARS:
        return stripped
    head = stripped[:_PREVIOUS_ASSISTANT_REFERENCE_KEEP_HEAD]
    tail = stripped[-_PREVIOUS_ASSISTANT_REFERENCE_KEEP_TAIL:]
    truncated_count = len(stripped) - _PREVIOUS_ASSISTANT_REFERENCE_KEEP_HEAD - _PREVIOUS_ASSISTANT_REFERENCE_KEEP_TAIL
    return f"{head}\n[... {truncated_count} chars truncated from previous assistant response ...]\n{tail}"


def _context_protocol_part(content: str) -> UserPromptPart:
    """Keep recovery protocol model-visible without presenting it as authored input."""
    return UserPromptPart([TextContent(content, metadata={"display": False, "source_id": "a13n.context.protocol"})])


def _first_system_parts(messages: list[ModelMessage]) -> list[SystemPromptPart]:
    for message in messages:
        if isinstance(message, ModelRequest):
            parts = [deepcopy(part) for part in message.parts if isinstance(part, SystemPromptPart)]
            if parts:
                return parts
    return []
