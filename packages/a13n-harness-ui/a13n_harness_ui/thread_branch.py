"""Resolve a displayed turn to a boundary in the selected native history."""

from dataclasses import dataclass

from a13n_harness import HarnessState
from a13n_harness.content import ContentItem, project_input_content, request_input_content
from pydantic_ai.messages import ModelMessage, ModelRequest

from a13n_harness_ui.conversation import ConversationExcerpt, checkpoint_excerpt
from a13n_harness_ui.thread_projection import _MAX_TEXT, _transcript_turns


@dataclass(frozen=True)
class Boundary:
    before: int | None
    after: int | None


@dataclass(frozen=True, slots=True)
class EditableInput:
    source_id: str | None
    attachment_ids: frozenset[str]


def editable_input(content: list[ContentItem]) -> EditableInput | None:
    """Recognize authored text and retained composer parts, with one input identity."""
    sources: set[str | None] = set()
    attachments: set[str] = set()
    for item in content:
        projected = project_input_content(item)
        if projected is None:
            continue
        text, metadata = projected
        if not metadata.display:
            continue
        extra = metadata.model_extra or {}
        if extra.keys() - {"harness_ui", "a13n.input-provenance"}:
            return None
        provenance = extra.get("a13n.input-provenance")
        if provenance is not None and (not isinstance(provenance, dict) or provenance.get("kind") != "task"):
            return None
        ui = extra.get("harness_ui", {})
        if not isinstance(ui, dict):
            return None
        sources.add(metadata.source_id)
        if "attachment" in ui:
            attachment = ui["attachment"]
            if (
                ui.keys() - {"attachment", "composer", "mount", "path", "captured_source_text"}
                or ui.get("mount") != "thread-files"
                or not isinstance(attachment, dict)
                or not isinstance(attachment.get("attachment_id"), str)
            ):
                return None
            attachments.add(attachment["attachment_id"])
        elif ui.keys() - {"composer", "skills"} or not isinstance(text, str) or metadata.media or len(text) > _MAX_TEXT:
            return None
    if len(sources) != 1:
        return None
    return EditableInput(sources.pop(), frozenset(attachments))


def boundaries(history: tuple[ModelMessage, ...], continuing_turn: bool) -> dict[str, Boundary]:
    points: dict[str, Boundary] = {}
    ambiguous: set[str] = set()
    turns = _transcript_turns(history)
    checked = 0
    restored_at = None
    for turn in turns[:-1] if continuing_turn else turns:
        message = history[turn.input_position]
        assert isinstance(message, ModelRequest)
        # Compaction may replay older user inputs after a newer summary. Such
        # an input alone cannot identify its original prefix.
        for prior in history[checked : turn.input_position]:
            metadata = prior.metadata or {}
            if prior.timestamp is not None and (
                metadata.get("keep") == "compact" or metadata.get("a13n.context") in {"compaction", "handoff"}
            ):
                restored_at = prior.timestamp
        checked = turn.input_position
        if restored_at and (message.timestamp is None or message.timestamp < restored_at):
            continue
        before = turn.input_position if editable_input(request_input_content(message)) is not None else None
        after = turn.end_position if turn.output_position is not None else None
        # Live display groups use the native attempt ID. Imported rows retain
        # their authored input ID, or their original position and timestamp.
        identity = turn.turn_id
        if identity.startswith("entry:") and turn.timestamp is not None:
            identity = f"{identity}@{turn.model_dump(mode='json')['timestamp']}"
        for key in {identity, message.run_id}:
            if key is None:
                continue
            if key in points:
                ambiguous.add(key)
            else:
                points[key] = Boundary(before, after)
    return {key: point for key, point in points.items() if key not in ambiguous}


def branch_prefix(
    history: tuple[ModelMessage, ...], cut: int, previous: HarnessState | None = None
) -> tuple[HarnessState, ConversationExcerpt, set[str]]:
    prefix = history[:cut]
    baseline = (
        HarnessState.new(message_history=prefix)
        if previous is None
        else HarnessState.new(
            thread_id=previous.thread_id,
            message_history=prefix,
            environment_states=previous.environment_states,
        )
    )
    attachments = set()
    for message in prefix:
        if isinstance(message, ModelRequest):
            for item in request_input_content(message):
                ui = (item.metadata.model_extra or {}).get("harness_ui")
                attachment = ui.get("attachment") if isinstance(ui, dict) else None
                if isinstance(attachment, dict) and isinstance(attachment.get("attachment_id"), str):
                    attachments.add(attachment["attachment_id"])
    return baseline, checkpoint_excerpt(ConversationExcerpt(), prefix), attachments
