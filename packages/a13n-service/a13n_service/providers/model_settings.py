"""The native settings each calling API accepts, as one JSON Schema the Console edits and the Service checks.

The schema is derived from the Pydantic AI settings type the Harness API names, so it cannot drift from what
execution passes on. Members JSON cannot carry, such as an `httpx.Timeout` object, are left out, and so are the
members that would let settings escape what the model and its provider resource decide.
"""

import json
from collections.abc import Mapping, Sequence
from functools import cache
from typing import Annotated, Any, get_type_hints

from a13n_harness.providers.model.apis import MODEL_APIS
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.providers.model.headers import ExtraHeaders, validate_header_names
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from pydantic import AfterValidator, ConfigDict, JsonValue, TypeAdapter, ValidationError, create_model
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import PydanticOmit, core_schema

from a13n_service.infra.errors import invalid

# Bedrock's settings name boto3 request shapes that exist only for type checking; they are JSON objects.
_TYPE_CHECKING_ONLY: dict[str, object] = dict.fromkeys(
    (
        "GuardrailConfigurationTypeDef",
        "PerformanceConfigurationTypeDef",
        "PromptVariableValuesTypeDef",
        "ServiceTierTypeDef",
    ),
    dict[str, JsonValue],
)

_EXCLUDED = frozenset(
    {
        # Raw body and headers use the qualified schemas below, not the SDK's unconstrained object types.
        "extra_body",
        "extra_headers",
        "timeout",
        "bedrock_additional_model_requests_fields",
        # Upstream selection: the model's `model_name` alone names what its provider's credential pays for.
        "bedrock_inference_profile",
        "openrouter_models",
        "openrouter_preset",
        # Account state: server-side conversations, containers and caches of the organization's provider account.
        "anthropic_container",
        "google_cached_content",
        "openai_conversation_id",
        "openai_previous_response_id",
        # Server-side tools: they search the account's vector stores or the web and run computer use, billed per
        # call outside token pricing and `max_usage`.
        "openai_native_tools",
        "openai_moderation",
    }
)


def bounded_settings(settings: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Bound JSON request settings, including opaque inference parameters."""
    if len(json.dumps(settings, ensure_ascii=False, allow_nan=False).encode()) > 64 * 1024:
        raise ValueError("Model settings exceed 64 KiB")

    def depth(value: JsonValue) -> int:
        children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        return 1 + max(map(depth, children), default=0)

    if depth(settings) > 16:
        raise ValueError("Model settings nest deeper than 16 levels")
    return settings


JsonSettings = Annotated[dict[str, JsonValue], AfterValidator(bounded_settings)]

# These fields replace host-owned input, selection, output, tools, transport or account state.
# Unknown inference fields remain open deliberately; this is not an upstream parameter catalogue.
_BODY_RESERVED = {
    "model",
    "messages",
    "input",
    "instructions",
    "prompt",
    "tools",
    "tool_choice",
    "functions",
    "function_call",
    "response_format",
    "text",
    "stream",
    "stream_options",
    "conversation",
    "previous_response_id",
    "background",
    "store",
    "include",
    "container",
    "moderation",
    "web_search_options",
    "models",
    "route",
    "provider",
    "preset",
    "n",
    "modalities",
    "audio",
}
_RAW_BODY_APIS = frozenset({"openai.responses", "openai.chat_completions"})
_REQUEST_HEADERS_RESERVED = frozenset(
    {
        "authorization",
        "api-key",
        "x-api-key",
        "x-goog-api-key",
        "accept",
        "content-type",
    }
)
_HEADERS = TypeAdapter(ExtraHeaders)


def check_request_headers(
    definition: ModelProviderDefinition,
    configuration: Mapping[str, JsonValue],
    headers: Mapping[str, str],
    *,
    credential_configured: bool,
    static_names: Sequence[str],
    field: str,
) -> None:
    """A request cannot replace the Provider's authentication, secrets, protocol or affinity."""
    try:
        definition.validate_configuration(
            configuration, credential_configured=credential_configured, header_names=tuple(headers)
        )
        validate_header_names(headers, reserved=(*_REQUEST_HEADERS_RESERVED, *static_names))
    except ValueError:
        raise invalid(field, "contains a header managed by the Provider or HTTP transport") from None


class _PortableSchema(GenerateJsonSchema):
    def handle_invalid_for_json_schema(self, schema: object, error_info: str) -> JsonSchemaValue:
        raise PydanticOmit

    def default_schema(self, schema: core_schema.WithDefaultSchema) -> JsonSchemaValue:
        # Every setting is optional and an absent one keeps the provider's behaviour, so none states a default.
        return self.generate_inner(schema["schema"])


@cache
def settings_schema(model_api: str) -> dict[str, JsonValue]:
    """Derive the schema. This imports the API's SDK, so the registry does it when it is assembled."""
    native = MODEL_APIS[model_api].settings_type
    fields: dict[str, Any] = {
        name: (hint, None)
        for name, hint in get_type_hints(native, localns=_TYPE_CHECKING_ONLY).items()
        if name not in _EXCLUDED
    }
    fields["extra_headers"] = (ExtraHeaders, None)
    if model_api in _RAW_BODY_APIS:
        fields["extra_body"] = (JsonSettings, None)
    settings = create_model(
        native.__name__, __config__=ConfigDict(extra="forbid", arbitrary_types_allowed=True), **fields
    )
    schema = settings.model_json_schema(schema_generator=_PortableSchema)
    if model_api in _RAW_BODY_APIS:
        schema["properties"]["extra_body"]["propertyNames"] = {
            "not": {
                "enum": sorted(
                    _BODY_RESERVED | ({"prompt_cache_options"} if model_api == "openai.responses" else set())
                )
            }
        }
    return schema


def check_settings(
    schema: Mapping[str, JsonValue], settings: Mapping[str, JsonValue], *, field: str
) -> dict[str, JsonValue]:
    """Check API shape and normalize headers without including submitted values in errors."""
    error = best_match(Draft202012Validator(schema).iter_errors(settings))
    if error is not None:
        path = ".".join((field, *map(str, error.absolute_path)))
        message = (
            "invalid request override"
            if any(key in settings for key in ("extra_body", "extra_headers"))
            else error.message
        )
        raise invalid(path, message)
    result = dict(settings)
    try:
        bounded_settings(result)
    except ValueError as error:
        raise invalid(field, str(error)) from None
    if "extra_headers" in result:
        try:
            headers = _HEADERS.validate_python(result["extra_headers"])
            validate_header_names(headers, reserved=_REQUEST_HEADERS_RESERVED)
        except (ValidationError, ValueError):
            raise invalid(f"{field}.extra_headers", "invalid or reserved request headers") from None
        result["extra_headers"] = dict(headers)
    return result
