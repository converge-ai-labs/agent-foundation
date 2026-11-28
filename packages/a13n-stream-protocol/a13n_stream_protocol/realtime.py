"""Transcript snapshots shared by the display stream and realtime socket."""

from typing import Literal, TypedDict

from a13n_harness import HarnessEvent
from ag_ui.core import CustomEvent
from pydantic_ai.messages import PartDeltaEvent, PartEndEvent, PartStartEvent, SpeechPart, SpeechPartDelta

REALTIME_TRANSCRIPT_EVENT = "a13n.realtime.transcript"


class SpeechTranscript(TypedDict):
    message_id: str
    role: Literal["user", "assistant"]
    text: str
    final: bool


def speech_transcript(item: HarnessEvent) -> SpeechTranscript | None:
    """Project one native speech update, including corrections to the current text."""
    event = item.event
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, SpeechPartDelta):
        role, text = event.delta.speaker, event.delta.transcript
    elif isinstance(event, PartStartEvent | PartEndEvent) and isinstance(event.part, SpeechPart):
        role, text = event.part.speaker, event.part.transcript
        if isinstance(event, PartEndEvent):
            text = text or ""
        elif not text:
            return None
    else:
        return None
    if role is None or text is None:
        return None
    return {
        "message_id": f"{item.run_id}:speech:{event.index}",
        "role": role,
        "text": text,
        "final": isinstance(event, PartEndEvent),
    }


def transcript_event(item: HarnessEvent, transcript: SpeechTranscript) -> CustomEvent:
    return CustomEvent(
        timestamp=int(item.occurred_at.timestamp() * 1000),
        name=REALTIME_TRANSCRIPT_EVENT,
        value={
            "thread_id": item.thread_id,
            "run_id": item.run_id,
            "sequence": item.sequence,
            "occurred_at": item.occurred_at.isoformat(),
            "event": transcript,
        },
    )
