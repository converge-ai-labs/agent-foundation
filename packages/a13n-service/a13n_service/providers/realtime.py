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
    native_image_types: tuple[str, ...] = ()
    native_images_per_input: int = 0
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
    def supported_image_types(self) -> tuple[str, ...]:
        profile = self.model_profile(self.profile_name)
        # The GPT-Live adapter cannot send text and images together to its visual backend.
        if not profile.get("supports_image_input") or profile.get("image_input_requires_response"):
            return ()
        return self.native_image_types

    @property
    def max_images_per_input(self) -> int:
        return self.native_images_per_input if self.supported_image_types else 0

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
        native_image_types=("image/png", "image/jpeg"),
        native_images_per_input=4,
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
        native_image_types=("image/png", "image/jpeg"),
        native_images_per_input=4,
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
        native_image_types=("image/jpeg",),
        native_images_per_input=1,
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
