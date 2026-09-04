"""Bounded compiler and evaluator for deterministic Ingress input mappings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue, TypeAdapter, ValidationError

from a13n_service.connectivity.bounds import (
    MAPPING_MAX_ARRAY_ITEMS,
    MAPPING_MAX_DEPTH,
    MAPPING_MAX_NODES,
    MAPPING_MAX_OBJECT_FIELDS,
    MAPPING_MAX_OUTPUT_BYTES,
    MAPPING_MAX_STATIC_BYTES,
    MAPPING_MAX_STRING_BYTES,
)
from a13n_service.interactions.input import AgentInput


class MappingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EventInputView:
    type: str
    occurred_at: datetime | None
    text: str | None
    actor: dict[str, JsonValue] | None
    context: dict[str, JsonValue]
    data: dict[str, JsonValue]

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "type": self.type,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at is not None else None,
            "text": self.text,
            "actor": self.actor,
            "context": self.context,
            "data": self.data,
        }


@dataclass(frozen=True, slots=True)
class EventInputBatch:
    events: tuple[EventInputView, ...]


@dataclass(frozen=True, slots=True)
class _Select:
    path: tuple[str | int, ...]
    has_default: bool
    default: JsonValue


@dataclass(frozen=True, slots=True)
class _Object:
    fields: tuple[tuple[str, _Node], ...]


@dataclass(frozen=True, slots=True)
class _Array:
    items: tuple[_Node, ...]


@dataclass(frozen=True, slots=True)
class _Static:
    value: JsonValue


type _Node = _Select | _Object | _Array | _Static


@dataclass(frozen=True, slots=True)
class CompiledMapping:
    value: dict[str, JsonValue]
    digest: str
    _root: _Node

    def evaluate(self, batch: EventInputBatch) -> AgentInput:
        source: JsonValue = {"events": [event.as_json() for event in batch.events]}
        value = _evaluate(self._root, source)
        _validate_json_bounds(value, max_bytes=MAPPING_MAX_OUTPUT_BYTES)
        try:
            return AgentInput.model_validate(value)
        except ValidationError as error:
            raise MappingError("mapping output is not a canonical AgentInput") from error


def compile_mapping(value: object) -> CompiledMapping:
    budget = [MAPPING_MAX_NODES]
    normalized, root = _compile_node(value, depth=1, budget=budget)
    encoded = _canonical(normalized)
    return CompiledMapping(value=normalized, digest=hashlib.sha256(encoded).hexdigest(), _root=root)


def _compile_node(value: object, *, depth: int, budget: list[int]) -> tuple[dict[str, JsonValue], _Node]:
    if depth > MAPPING_MAX_DEPTH:
        raise MappingError("mapping exceeds maximum depth")
    budget[0] -= 1
    if budget[0] < 0:
        raise MappingError("mapping exceeds maximum node count")
    if not isinstance(value, dict) or not isinstance(value.get("op"), str):
        raise MappingError("mapping node must be an object with an op")
    op = value["op"]
    if op == "select":
        return _compile_select(value)
    if op == "object":
        return _compile_object(value, depth=depth, budget=budget)
    if op == "array":
        return _compile_array(value, depth=depth, budget=budget)
    if op == "static":
        return _compile_static(value)
    raise MappingError("mapping operation is unsupported")


def _compile_select(value: dict[object, object]) -> tuple[dict[str, JsonValue], _Select]:
    allowed = {"op", "path", "default"}
    if not set(value) <= allowed or set(value) < {"op", "path"}:
        raise MappingError("select node fields are invalid")
    path_value = value["path"]
    if not isinstance(path_value, list) or not 1 <= len(path_value) <= MAPPING_MAX_DEPTH:
        raise MappingError("select path is invalid")
    path: list[str | int] = []
    for part in path_value:
        if isinstance(part, bool) or not isinstance(part, (str, int)):
            raise MappingError("select path segment is invalid")
        if isinstance(part, str) and (not part or len(part.encode()) > 128):
            raise MappingError("select path segment is invalid")
        if isinstance(part, int) and part < 0:
            raise MappingError("select path index is invalid")
        path.append(part)
    if path[0] != "events":
        raise MappingError("select path must begin at the safe events root")
    has_default = "default" in value
    default = _json_value(value.get("default")) if has_default else None
    if has_default:
        _validate_json_bounds(default, max_bytes=MAPPING_MAX_STATIC_BYTES)
    normalized_path: list[JsonValue] = list(path)
    normalized: dict[str, JsonValue] = {"op": "select", "path": normalized_path}
    if has_default:
        normalized["default"] = default
    return normalized, _Select(tuple(path), has_default, default)


def _compile_object(
    value: dict[object, object], *, depth: int, budget: list[int]
) -> tuple[dict[str, JsonValue], _Object]:
    if set(value) != {"op", "fields"} or not isinstance(value["fields"], dict):
        raise MappingError("object node fields are invalid")
    fields_value = value["fields"]
    if len(fields_value) > MAPPING_MAX_OBJECT_FIELDS:
        raise MappingError("object node has too many fields")
    if any(not isinstance(key, str) for key in fields_value):
        raise MappingError("object field name is invalid")
    normalized_fields: dict[str, JsonValue] = {}
    fields: list[tuple[str, _Node]] = []
    for key in sorted(fields_value):
        if not key or len(key.encode()) > 128:
            raise MappingError("object field name is invalid")
        normalized, node = _compile_node(fields_value[key], depth=depth + 1, budget=budget)
        normalized_fields[key] = normalized
        fields.append((key, node))
    return {"op": "object", "fields": normalized_fields}, _Object(tuple(fields))


def _compile_array(
    value: dict[object, object], *, depth: int, budget: list[int]
) -> tuple[dict[str, JsonValue], _Array]:
    if set(value) != {"op", "items"} or not isinstance(value["items"], list):
        raise MappingError("array node fields are invalid")
    if len(value["items"]) > MAPPING_MAX_ARRAY_ITEMS:
        raise MappingError("array node has too many items")
    compiled = [_compile_node(item, depth=depth + 1, budget=budget) for item in value["items"]]
    return {"op": "array", "items": [item[0] for item in compiled]}, _Array(tuple(item[1] for item in compiled))


def _compile_static(value: dict[object, object]) -> tuple[dict[str, JsonValue], _Static]:
    if set(value) != {"op", "value"}:
        raise MappingError("static node fields are invalid")
    static = _json_value(value["value"])
    _validate_json_bounds(static, max_bytes=MAPPING_MAX_STATIC_BYTES)
    return {"op": "static", "value": static}, _Static(static)


def _evaluate(node: _Node, root: JsonValue) -> JsonValue:
    if isinstance(node, _Static):
        return node.value
    if isinstance(node, _Object):
        return {key: _evaluate(child, root) for key, child in node.fields}
    if isinstance(node, _Array):
        return [_evaluate(child, root) for child in node.items]
    current = root
    for part in node.path:
        if isinstance(part, str) and isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(part, int) and isinstance(current, list) and part < len(current):
            current = current[part]
        elif node.has_default:
            return node.default
        else:
            raise MappingError("mapping selection is missing")
    return current


def _json_value(value: object) -> JsonValue:
    try:
        return TypeAdapter(JsonValue).validate_python(value)
    except ValidationError as error:
        raise MappingError("mapping contains an invalid JSON value") from error


def _validate_json_bounds(value: JsonValue, *, max_bytes: int) -> None:
    try:
        encoded = _canonical(value)
    except (TypeError, ValueError) as error:
        raise MappingError("mapping contains an invalid JSON value") from error
    if len(encoded) > max_bytes:
        raise MappingError("mapping JSON exceeds its byte limit")
    _walk_json(value, depth=1)


def _walk_json(value: JsonValue, *, depth: int) -> None:
    if depth > MAPPING_MAX_DEPTH:
        raise MappingError("mapping JSON exceeds maximum depth")
    if isinstance(value, str) and len(value.encode()) > MAPPING_MAX_STRING_BYTES:
        raise MappingError("mapping string exceeds its byte limit")
    if isinstance(value, dict):
        for child in value.values():
            _walk_json(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _walk_json(child, depth=depth + 1)


def _canonical(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
