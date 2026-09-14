"""One serializable native settings contract for authoring and execution."""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from copy import deepcopy
from functools import lru_cache
from typing import Any, cast, get_type_hints

from jsonschema import Draft202012Validator
from pydantic import ConfigDict, JsonValue, create_model
from pydantic_ai.models.bedrock import BedrockModelSettings

from a13n_service.application_errors import ErrorCategory

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
        raise ModelError("invalid_model_api", "The Model API is invalid.", category=ErrorCategory.invalid_request)
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
        category=ErrorCategory.invalid_request,
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


def _validate_body_paths(model_api: str, field: str, body: JsonValue) -> None:
    # Validate supplied paths only; SDK merge precedence remains caller-owned.
    # OpenAI and Anthropic shallow-merge extra_body: text={"verbosity": "low"}
    # or output_config={"effort": "low"} replaces the whole container, dropping
    # Harness's format/schema even though no protected leaf is supplied. Empty
    # objects do the same. These cases are intentionally allowed; use native
    # verbosity/effort settings to retain Harness's structured-output format.
    for path in BUILT_IN_MODEL_APIS[model_api].protected_body_paths:
        value = body
        visited: list[str | int] = [field]
        for part in path:
            # A scalar ancestor replaces the protected object just as surely as
            # supplying its leaf. Unrelated nested extension data remains opaque.
            if not isinstance(value, dict):
                raise _invalid(visited, "reserved_request_field")
            if part not in value:
                break
            value = value[part]
            visited.append(part)
        else:
            raise _invalid(visited, "reserved_request_field")


def validate_settings(model_api: str, settings: JsonObject) -> JsonObject:
    validate_settings_bounds(settings)
    error = next(Draft202012Validator(settings_schema(model_api)).iter_errors(settings), None)
    if error is not None:
        raise _invalid(list(error.absolute_path), "invalid_type_or_value")
    for field in _ESCAPE_FIELDS & settings.keys():
        _validate_body_paths(model_api, field, settings[field])
    _reasoning_choice(model_api, settings)
    return settings


def effective_settings(model_api: str, defaults: JsonObject, *override_layers: JsonObject) -> JsonObject:
    effective = validate_settings(model_api, defaults)
    for overrides in override_layers:
        validate_settings(model_api, overrides)
        inherited = effective
        if (choice := _reasoning_choice(model_api, overrides)) is not None:
            inherited = _without_conflicting_reasoning_settings(model_api, effective, choice)
        merged = {**inherited, **deepcopy(overrides)}
        if choice is not None:
            alternative = BUILT_IN_MODEL_APIS[model_api].reasoning_alternatives[choice]
            for path in alternative.paths:
                if not _has_path(overrides, path) and (value := _path_value(inherited, path)) is not _MISSING:
                    _set_path(merged, path, value)
        effective = validate_settings(model_api, merged)
    return effective


def _reasoning_choice(model_api: str, settings: JsonObject) -> int | None:
    selected: list[tuple[int, tuple[str, ...]]] = []
    for index, alternative in enumerate(BUILT_IN_MODEL_APIS[model_api].reasoning_alternatives):
        path = next((path for path in alternative.paths if _has_path(settings, path)), None)
        if path is not None:
            selected.append((index, path))
    if len(selected) > 1:
        raise _invalid(list(selected[1][1]), "conflicting_reasoning_settings")
    return selected[0][0] if selected else None


def _has_path(settings: JsonObject, path: tuple[str, ...]) -> bool:
    value: JsonValue = settings
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return True


def _without_conflicting_reasoning_settings(model_api: str, settings: JsonObject, selected: int) -> JsonObject:
    result = deepcopy(settings)
    for index, alternative in enumerate(BUILT_IN_MODEL_APIS[model_api].reasoning_alternatives):
        if index == selected:
            continue
        for source, target in alternative.preserved_fields:
            if (value := _path_value(result, source)) is not _MISSING:
                _set_path(result, target, value)
        for path in alternative.paths:
            _delete_path(result, path)
    return result


_MISSING = object()


def _path_value(settings: JsonObject, path: tuple[str, ...]) -> JsonValue | object:
    value: JsonValue = settings
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _set_path(settings: JsonObject, path: tuple[str, ...], value: JsonValue | object) -> None:
    current = settings
    for part in path[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            return
        current = child
    current[path[-1]] = cast(JsonValue, value)


def _delete_path(settings: JsonObject, path: tuple[str, ...]) -> None:
    parents: list[tuple[JsonObject, str]] = []
    current = settings
    for part in path[:-1]:
        value = current.get(part)
        if not isinstance(value, dict):
            return
        parents.append((current, part))
        current = value
    current.pop(path[-1], None)
    for parent, part in reversed(parents):
        child = parent.get(part)
        if child != {}:
            break
        del parent[part]
