"""Realtime protocols that the Service can open with the Agent's primary model."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Self

from pydantic_ai.profiles.google import google_realtime_model_profile
from pydantic_ai.profiles.grok import grok_realtime_model_profile
from pydantic_ai.profiles.openai import openai_realtime_model_profile
from pydantic_ai.realtime import RealtimeModelProfile, RealtimeModelSettings
from pydantic_ai.realtime.azure import AzureRealtimeModel, AzureRealtimeModelSettings
from pydantic_ai.realtime.google import GoogleRealtimeModel, GoogleRealtimeModelSettings
from pydantic_ai.realtime.openai import OpenAIRealtimeModel, OpenAIRealtimeModelSettings
from pydantic_ai.realtime.openai_live import OpenAILiveModel, OpenAILiveModelSettings
from pydantic_ai.realtime.xai import XaiRealtimeModel, XaiRealtimeModelSettings


@dataclass(frozen=True, slots=True)
class Protocol:
    provider: str
    label: str
    endpoint: str | None
    settings: type
    adapter: type[OpenAIRealtimeModel] | type[OpenAILiveModel] | type[XaiRealtimeModel] | type[GoogleRealtimeModel]
    model_profile: Callable[[str], RealtimeModelProfile]
    profile_name: str
    websocket_path: str
    webrtc: bool = False
    group_user_speech: bool = False
    audio_defaults: RealtimeModelSettings = field(default_factory=RealtimeModelSettings)
    text_defaults: RealtimeModelSettings = field(default_factory=RealtimeModelSettings)

    def for_model(self, model_name: str) -> Self:
        return replace(self, profile_name=model_name)

    @property
    def audio_input_hz(self) -> int:
        return self.model_profile(self.profile_name)["audio_input_sample_rate"]

    @property
    def audio_output_hz(self) -> int:
        return self.model_profile(self.profile_name)["audio_output_sample_rate"]

    @property
    def supports_image_input(self) -> bool:
        return self.model_profile(self.profile_name).get("supports_image_input", False)

    @property
    def supports_interruption(self) -> bool:
        return self.model_profile(self.profile_name).get("supports_interruption", False)


PROTOCOLS = {
    "openai.realtime": Protocol(
        "openai",
        "OpenAI Realtime",
        "https://api.openai.com/v1",
        OpenAIRealtimeModelSettings,
        OpenAIRealtimeModel,
        openai_realtime_model_profile,
        "gpt-realtime",
        "/realtime",
        webrtc=True,
        audio_defaults={"turn_detection": {"silence_duration_ms": 850}},
    ),
    "openai.live": Protocol(
        "openai",
        "OpenAI GPT-Live",
        "https://api.openai.com/v1",
        OpenAILiveModelSettings,
        OpenAILiveModel,
        openai_realtime_model_profile,
        "gpt-live",
        "/live/sessions",
        webrtc=True,
        group_user_speech=True,
        text_defaults=OpenAILiveModelSettings(openai_live_idle_audio=True),
    ),
    "azure.realtime": Protocol(
        "azure_openai",
        "Azure OpenAI Realtime",
        None,
        AzureRealtimeModelSettings,
        AzureRealtimeModel,
        openai_realtime_model_profile,
        "gpt-realtime",
        "/realtime",
        webrtc=True,
    ),
    "google.realtime": Protocol(
        "google_gemini",
        "Google Gemini Live",
        "https://generativelanguage.googleapis.com",
        GoogleRealtimeModelSettings,
        GoogleRealtimeModel,
        google_realtime_model_profile,
        "gemini-3.1-flash-live-preview",
        "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent",
    ),
    "xai.realtime": Protocol(
        "xai",
        "xAI Grok Voice",
        "https://api.x.ai/v1",
        XaiRealtimeModelSettings,
        XaiRealtimeModel,
        grok_realtime_model_profile,
        "grok-voice",
        "/realtime",
    ),
}
