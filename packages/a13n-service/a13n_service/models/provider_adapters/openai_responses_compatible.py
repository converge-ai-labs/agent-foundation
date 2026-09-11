"""User-defined OpenAI Responses-compatible Provider adapter."""

from dataclasses import replace

from .openai_compatible import INTEGRATION as OPENAI_COMPATIBLE

INTEGRATION = replace(
    OPENAI_COMPATIBLE,
    type="openai_responses_compatible",
    display_name="OpenAI Responses-Compatible",
    supported_model_apis=("openai.responses",),
)
