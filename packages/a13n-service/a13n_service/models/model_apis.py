"""Service request-body authority for supported native calling APIs."""

from dataclasses import dataclass
from types import MappingProxyType

# Connection fields are Provider-owned, even on custom compatible endpoints.
_CONNECTION_FIELDS = ("api_key", "authorization", "credentials", "base_url", "endpoint", "headers", "extra_headers")
_CHAT_FIELDS = (
    "model",
    "messages",
    "tools",
    "tool_choice",
    "functions",
    "function_call",
    "stream",
    "stream_options",
    "response_format",
    "reasoning_effort",
    "reasoning",
    "thinking",
    "enable_thinking",
)
_RESPONSES_FIELDS = (
    "model",
    "input",
    "instructions",
    "tools",
    "tool_choice",
    "stream",
    "stream_options",
    "previous_response_id",
    "conversation",
    "reasoning",
)
_ANTHROPIC_FIELDS = (
    "model",
    "messages",
    "system",
    "tools",
    "tool_choice",
    "stream",
    "container",
    "output_format",
    "thinking",
    "output_config",
)


def _paths(*fields: str) -> tuple[tuple[str, ...], ...]:
    return tuple((name,) for name in (*_CONNECTION_FIELDS, *fields))


@dataclass(frozen=True, slots=True)
class ModelApiPolicy:
    protected_body_paths: tuple[tuple[str, ...], ...]
    supports_extra_body: bool = True


MODEL_API_POLICIES = MappingProxyType(
    {
        "openai.responses": ModelApiPolicy((*_paths(*_RESPONSES_FIELDS), ("text", "format")), supports_extra_body=True),
        "openai.chat_completions": ModelApiPolicy(_paths(*_CHAT_FIELDS), supports_extra_body=True),
        "anthropic.messages": ModelApiPolicy(_paths(*_ANTHROPIC_FIELDS), supports_extra_body=True),
        "google.generate_content": ModelApiPolicy((), supports_extra_body=False),
        "bedrock.converse": ModelApiPolicy(
            _paths(
                *_ANTHROPIC_FIELDS,
                "modelId",
                "toolConfig",
                "outputConfig",
                "inferenceConfig",
                "reasoning_effort",
                "reasoning_config",
            ),
            supports_extra_body=False,
        ),
        "bedrock_mantle.responses": ModelApiPolicy(
            (*_paths(*_RESPONSES_FIELDS), ("text", "format")), supports_extra_body=True
        ),
        "bedrock_mantle.chat_completions": ModelApiPolicy(_paths(*_CHAT_FIELDS), supports_extra_body=True),
        "openrouter.chat_completions": ModelApiPolicy(
            _paths(*_CHAT_FIELDS, "models", "preset", "transforms"), supports_extra_body=True
        ),
        "ollama.chat_completions": ModelApiPolicy(_paths(*_CHAT_FIELDS, "format"), supports_extra_body=True),
    }
)
