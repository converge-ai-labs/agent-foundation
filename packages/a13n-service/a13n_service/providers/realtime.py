"""Realtime protocols that the Service can open with the Agent's primary model."""

from dataclasses import dataclass

from pydantic_ai.realtime.openai import OpenAIRealtimeModelSettings
from pydantic_ai.realtime.openai_live import OpenAILiveModelSettings
from pydantic_ai.realtime.xai import XaiRealtimeModelSettings


@dataclass(frozen=True, slots=True)
class Protocol:
    provider: str
    label: str
    endpoint: str
    settings: type
    webrtc: bool = False


PROTOCOLS = {
    "openai.realtime": Protocol(
        "openai", "OpenAI Realtime", "https://api.openai.com/v1", OpenAIRealtimeModelSettings, webrtc=True
    ),
    "openai.live": Protocol(
        "openai", "OpenAI GPT-Live", "https://api.openai.com/v1", OpenAILiveModelSettings, webrtc=True
    ),
    "xai.realtime": Protocol("xai", "xAI Grok Voice", "https://api.x.ai/v1", XaiRealtimeModelSettings),
}
