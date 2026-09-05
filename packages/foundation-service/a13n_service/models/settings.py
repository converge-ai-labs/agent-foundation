"""One serializable native settings contract for authoring and execution."""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from functools import lru_cache
from typing import Any, get_type_hints

from jsonschema import Draft202012Validator
from pydantic import ConfigDict, JsonValue, create_model
from pydantic_ai.models.bedrock import BedrockModelSettings

from .model_apis import BUILT_IN_MODEL_APIS
from .service_common import ModelError

JsonObject = dict[str, JsonValue]
MAX_SETTINGS_BYTES = 64 * 1024
MAX_SETTINGS_DEPTH = 16

# These native controls alter request identity or Harness-owned conversation/tools.
_PRIVATE_SETTINGS = frozenset(
    {
        "openrouter_models",
        "openrouter_preset",
        "openrouter_transforms",
        "openai_native_tools",
        "openai_previous_response_id",
        "openai_conversation_id",
        "bedrock_inference_profile",
        "bedrock_prompt_variables",
        "google_cached_content",
        "anthropic_container",
        "anthropic_code_execution_tool_version",
    }
)
_RESERVED = frozenset(
    {
        "model",
        "models",
        "modelid",
        "deployment",
        "deploymentid",
        "preset",
        "presets",
        "apikey",
        "authorization",
        "credentials",
        "credential",
        "baseurl",
        "endpoint",
        "url",
        "messages",
        "input",
        "instructions",
        "system",
        "tools",
        "toolresults",
        "toolresult",
        "functions",
        "stream",
        "streamoptions",
        "responseformat",
        "outputschema",
        "jsonschema",
        "contents",
        "systeminstruction",
        "toolconfig",
        "cachedcontent",
        "conversation",
        "previousresponseid",
        "inferenceprofile",
        "transforms",
        "format",
        "schema",
        "responsemimetype",
        "responseschema",
        "responsejsonschema",
        "outputconfig",
        "outputformat",
        "systemprompt",
        "httpoptions",
        "clientoptions",
        "headers",
        "extraheaders",
        "auth",
        "authentication",
        "accesskey",
        "secretkey",
        "sessiontoken",
        "awsaccesskeyid",
        "awssecretaccesskey",
    }
)
_SAFE_HEADERS = frozenset({"http-referer", "x-title"})
_ESCAPE_FIELDS = frozenset({"extra_body", "bedrock_additional_model_requests_fields"})


def _field_descriptions(native: Any) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for parent in getattr(native, "__orig_bases__", ()):
        if isinstance(parent, type) and hasattr(parent, "__annotations__"):
            descriptions.update(_field_descriptions(parent))
    try:
        source = textwrap.dedent(inspect.getsource(native))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError, UnicodeError):
        return descriptions
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for field, following in zip(node.body, node.body[1:], strict=False):
            if (
                isinstance(field, ast.AnnAssign)
                and isinstance(field.target, ast.Name)
                and isinstance(following, ast.Expr)
                and isinstance(following.value, ast.Constant)
                and isinstance(following.value.value, str)
            ):
                descriptions[field.target.id] = inspect.cleandoc(following.value.value)
    return descriptions


@lru_cache(maxsize=16)
def settings_schema(model_api: str) -> dict[str, Any]:
    binding = BUILT_IN_MODEL_APIS.get(model_api)
    if binding is None:
        raise ModelError("invalid_model_api", "The Model API is invalid.", status_code=400)
    native = binding.settings_type
    if native is BedrockModelSettings:
        from mypy_boto3_bedrock_runtime import type_defs

        annotations = get_type_hints(native, localns=vars(type_defs))
    else:
        annotations = get_type_hints(native)
    if not binding.supports_extra_body:
        annotations.pop("extra_body", None)
    annotations["timeout"] = float
    for field in _ESCAPE_FIELDS & annotations.keys():
        annotations[field] = dict[str, JsonValue]
    fields: dict[str, Any] = {
        name: (annotation, None) for name, annotation in annotations.items() if name not in _PRIVATE_SETTINGS
    }
    schema = create_model("NativeSettings", __config__=ConfigDict(extra="forbid"), **fields).model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    descriptions = _field_descriptions(native)
    for name, field_schema in schema["properties"].items():
        field_schema.pop("default", None)
        if name in descriptions:
            field_schema["description"] = descriptions[name]
    schema["properties"]["extra_headers"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {name: {"type": "string", "maxLength": 2048} for name in sorted(_SAFE_HEADERS)},
        "description": "Optional attribution headers; authentication and endpoint headers belong to the Provider.",
    }
    for name in ("max_tokens", "timeout"):
        schema["properties"][name]["exclusiveMinimum"] = 0
    _close_native_objects(schema)
    return schema


def _close_native_objects(node: Any) -> None:
    if isinstance(node, dict):
        if "properties" in node and "additionalProperties" not in node:
            node["additionalProperties"] = False
        for value in node.values():
            _close_native_objects(value)
    elif isinstance(node, list):
        for value in node:
            _close_native_objects(value)


def _invalid(path: list[str | int], reason: str) -> ModelError:
    return ModelError(
        "invalid_model_settings",
        "The Model settings are invalid.",
        status_code=400,
        details={"path": ["settings", *path], "reason": reason},
    )


def validate_settings_bounds(settings: JsonObject) -> JsonObject:
    try:
        encoded = json.dumps(settings, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise _invalid([], "invalid_json") from error
    if len(encoded) > MAX_SETTINGS_BYTES:
        raise _invalid([], "too_large")

    def visit(value: JsonValue, depth: int) -> None:
        if isinstance(value, (dict, list)):
            if depth > MAX_SETTINGS_DEPTH:
                raise _invalid([], "too_deep")
            for child in value.values() if isinstance(value, dict) else value:
                visit(child, depth + 1)

    visit(settings, 1)
    return settings


def _request_key(key: str) -> str:
    return key.replace("_", "").replace("-", "").casefold()


def _validate_escape(value: JsonValue, path: list[str | int], addressed: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _request_key(key) in _RESERVED:
                raise _invalid([*path, key], "reserved_request_field")
            if _request_key(key) in addressed:
                raise _invalid([*path, key], "conflicting_native_setting")
            _validate_escape(child, [*path, key], addressed)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_escape(child, [*path, index], addressed)


def outbound_parameter(name: str) -> str:
    """Native settings names that address the same upstream field."""
    aliases = {
        "max_tokens": "max_tokens",
        "stop_sequences": "stop",
        "google_thinking_config": "thinkingConfig",
        "google_logprobs": "responseLogprobs",
        "google_top_logprobs": "logprobs",
    }
    if name in aliases:
        return aliases[name]
    for prefix in ("openai_", "anthropic_", "openrouter_", "google_", "bedrock_"):
        if name.startswith(prefix):
            return name.removeprefix(prefix)
    return name


def validate_settings(model_api: str, settings: JsonObject) -> JsonObject:
    validate_settings_bounds(settings)
    error = next(Draft202012Validator(settings_schema(model_api)).iter_errors(settings), None)
    if error is not None:
        raise _invalid(list(error.absolute_path), "invalid_type_or_value")
    addressed = {_request_key(outbound_parameter(key)) for key in settings if key not in _ESCAPE_FIELDS}
    if "max_tokens" in settings:
        addressed.update({"maxcompletiontokens", "maxoutputtokens", "maxtokens"})
    if "thinking" in settings:
        addressed.update({"reasoning", "reasoningeffort", "thinking", "thinkingconfig"})
    if any(key.startswith("openai_reasoning_") for key in settings):
        addressed.add("reasoning")
    if "openai_text_verbosity" in settings:
        addressed.add("verbosity")
    for field in _ESCAPE_FIELDS & settings.keys():
        extra = settings[field]
        _validate_escape(extra, [field], addressed)
    return settings


def effective_settings(model_api: str, defaults: JsonObject, overrides: JsonObject) -> JsonObject:
    validate_settings(model_api, overrides)
    return validate_settings(model_api, {**defaults, **overrides})
