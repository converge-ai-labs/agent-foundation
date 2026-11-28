"""Project provider speech parts into the Service's browser transcript."""

from a13n_harness import HarnessEvent
from a13n_stream_protocol.realtime import SpeechTranscript, speech_transcript
from pydantic_ai.messages import RealtimeTurnCompleteEvent


class LiveTranscripts:
    """GPT-Live can close user speech as soon as the reply starts, then transcribe its tail separately."""

    def __init__(self, *, group_user_speech: bool) -> None:
        self.group_user_speech = group_user_speech
        self.user_parts: dict[str, str] = {}

    def observe(self, item: HarnessEvent) -> SpeechTranscript | None:
        if isinstance(item.event, RealtimeTurnCompleteEvent):
            self.user_parts.clear()
            return None
        update = speech_transcript(item)
        if update is None or not self.group_user_speech or update["role"] != "user":
            return update
        key, text = update["message_id"], update["text"].strip()
        if not text and key not in self.user_parts:
            return None
        self.user_parts[key] = text
        return {
            **update,
            "message_id": next(iter(self.user_parts)),
            "text": " ".join(filter(None, self.user_parts.values())),
        }
