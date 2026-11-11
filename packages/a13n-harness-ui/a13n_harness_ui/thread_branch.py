"""Resolve a displayed turn to a boundary in the selected native history."""

from dataclasses import dataclass

from a13n_harness import HarnessState
from a13n_harness.content import project_input_content, request_input_content
from pydantic_ai.messages import ModelMessage, ModelRequest

from a13n_harness_ui.conversation import ConversationExcerpt, checkpoint_excerpt
from a13n_harness_ui.thread_projection import _MAX_TEXT, _transcript_turns


@dataclass(frozen=True)
class Boundary:
    before: int | None
    after: int | None


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
        visible = [item for item in request_input_content(message) if item.metadata.display]
        projected = project_input_content(visible[0]) if len(visible) == 1 else None
        plain = False
        if projected:
            text, metadata = projected
            ui = (metadata.model_extra or {}).get("harness_ui") or {}
            composer = ui.get("composer", {}) if isinstance(ui, dict) else None
            plain = (
                isinstance(text, str)
                and len(text) <= _MAX_TEXT
                and not metadata.media
                and isinstance(ui, dict)
                and not (ui.keys() - {"composer"})
                and isinstance(composer, dict)
                and not composer.get("skills")
            )
        before = turn.input_position if plain else None
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


def branch_prefix(history: tuple[ModelMessage, ...], cut: int) -> tuple[HarnessState, ConversationExcerpt, set[str]]:
    prefix = history[:cut]
    baseline = HarnessState.new(message_history=prefix)
    attachments = set()
    for message in prefix:
        if isinstance(message, ModelRequest):
            for item in request_input_content(message):
                ui = (item.metadata.model_extra or {}).get("harness_ui")
                attachment = ui.get("attachment") if isinstance(ui, dict) else None
                if isinstance(attachment, dict) and isinstance(attachment.get("attachment_id"), str):
                    attachments.add(attachment["attachment_id"])
    return baseline, checkpoint_excerpt(ConversationExcerpt(), prefix), attachments
