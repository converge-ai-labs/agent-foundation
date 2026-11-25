"""Realtime protocols that the Service can open with the Agent's primary model."""

from dataclasses import dataclass, field

from pydantic_ai.realtime import RealtimeModelSettings
from pydantic_ai.realtime.openai import OpenAIRealtimeModel, OpenAIRealtimeModelSettings
from pydantic_ai.realtime.openai_live import OpenAILiveModel, OpenAILiveModelSettings
from pydantic_ai.realtime.xai import XaiRealtimeModel, XaiRealtimeModelSettings


@dataclass(frozen=True, slots=True)
class Protocol:
    provider: str
    label: str
    endpoint: str
    settings: type
    adapter: type[OpenAIRealtimeModel] | type[OpenAILiveModel] | type[XaiRealtimeModel]
    webrtc: bool = False
    group_user_speech: bool = False
    audio_defaults: RealtimeModelSettings = field(default_factory=RealtimeModelSettings)
    text_defaults: RealtimeModelSettings = field(default_factory=RealtimeModelSettings)


PROTOCOLS = {
    "openai.realtime": Protocol(
        "openai",
        "OpenAI Realtime",
        "https://api.openai.com/v1",
        OpenAIRealtimeModelSettings,
        OpenAIRealtimeModel,
        webrtc=True,
        audio_defaults={"turn_detection": {"silence_duration_ms": 850}},
    ),
    "openai.live": Protocol(
        "openai",
        "OpenAI GPT-Live",
        "https://api.openai.com/v1",
        OpenAILiveModelSettings,
        OpenAILiveModel,
        webrtc=True,
        group_user_speech=True,
        text_defaults=OpenAILiveModelSettings(openai_live_idle_audio=True),
    ),
    "xai.realtime": Protocol(
        "xai", "xAI Grok Voice", "https://api.x.ai/v1", XaiRealtimeModelSettings, XaiRealtimeModel
    ),
}
