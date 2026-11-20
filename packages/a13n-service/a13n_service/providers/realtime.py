"""Realtime protocols that the Service can open with the Agent's primary model."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Protocol:
    provider: str
    label: str
    endpoint: str


PROTOCOLS = {
    "openai.realtime": Protocol("openai", "OpenAI Realtime", "https://api.openai.com/v1"),
    "openai.live": Protocol("openai", "OpenAI GPT-Live", "https://api.openai.com/v1"),
    "xai.realtime": Protocol("xai", "xAI Grok Voice", "https://api.x.ai/v1"),
}
