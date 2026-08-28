from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .model import DataFrameProfile, OptionReader, SchemaIndex, real_oneofs, short_name
from .python_renderer import method_records


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_models(models_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("a13n_generated_eip_models", models_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load generated EIP models")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_schema(index: SchemaIndex, options: OptionReader, models_path: Path) -> dict[str, object]:
    module = _load_models(models_path)
    definitions: dict[str, object] = {}
    wire_names = sorted(
        {short_name(name) for name in index.messages if not short_name(name).startswith("EIPMethodOptions")}
        | {short_name(name) for name in index.enums}
    )
    for name in wire_names:
        value = getattr(module, name, None)
        if value is None:
            continue
        schema = TypeAdapter(value).json_schema(ref_template="#/$defs/{model}")
        nested = schema.pop("$defs", {})
        for nested_name, nested_schema in nested.items():
            existing = definitions.get(nested_name)
            if existing is not None and existing != nested_schema:
                raise ValueError(f"conflicting JSON Schema definition for {nested_name}")
            definitions[nested_name] = nested_schema
        if schema != {"$ref": f"#/$defs/{name}"}:
            existing = definitions.get(name)
            if existing is None:
                definitions[name] = schema

    for message in index.messages.values():
        message_option = options.message(message)
        if message_option is not None and message_option.discriminated_union:
            continue
        oneofs = real_oneofs(message)
        if not oneofs:
            continue
        definition = definitions.get(message.name)
        if not isinstance(definition, dict):
            raise ValueError(f"EIP oneof message {message.name} has no object schema")
        constraints = definition.setdefault("allOf", [])
        if not isinstance(constraints, list):
            raise ValueError(f"EIP schema {message.name} has invalid allOf constraints")
        for fields in oneofs.values():
            constraints.append(
                {
                    "oneOf": [
                        {
                            "required": [field.name],
                            "properties": {field.name: {"not": {"type": "null"}}},
                        }
                        for field in fields
                    ]
                }
            )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/converge-ai-labs/agent-foundation/eip/v1/schema.json",
        "title": "Environment Interaction Protocol 0.1",
        "$defs": definitions,
    }


def build_openrpc(records: list[dict[str, Any]], schema: dict[str, object]) -> dict[str, object]:
    definitions = schema["$defs"]
    if not isinstance(definitions, dict):
        raise ValueError("EIP JSON Schema definitions must be an object")
    methods: list[dict[str, object]] = []
    for record in records:
        params_type = record["params_type"]
        definition = definitions[params_type]
        if not isinstance(definition, dict) or not isinstance(definition.get("properties"), dict):
            raise ValueError(f"EIP params schema {params_type} must be an object")
        properties = definition["properties"]
        required = set(definition.get("required", ()))
        params = [
            {
                "name": name,
                "required": name in required,
                "schema": {"$ref": f"schema.json#/$defs/{params_type}/properties/{name}"},
            }
            for name in properties
        ]
        method: dict[str, object] = {
            "name": record["jsonrpc_method"],
            "params": params,
            "paramStructure": "by-name",
            "x-eip-kind": record["kind"],
            "x-eip-replay-class": record["replay_class"],
            "x-eip-introduced": record["introduced"],
            "x-eip-error-family": record["error_family"],
            "x-eip-transfer-action": record["transfer_action"],
            "x-eip-transfer-direction": record["transfer_direction"],
            "x-eip-params-schema": {"$ref": f"schema.json#/$defs/{params_type}"},
        }
        if record["result_type"] is not None:
            method["result"] = {
                "name": "result",
                "schema": {"$ref": f"schema.json#/$defs/{record['result_type']}"},
            }
        methods.append(method)
    return {
        "openrpc": "1.3.2",
        "info": {"title": "Environment Interaction Protocol", "version": "0.1"},
        "methods": methods,
    }


def build_data_frame_artifact(profile: DataFrameProfile) -> dict[str, object]:
    fields: list[dict[str, object]] = []
    offset = 0
    for name, width in (
        ("magic", profile.magic_bytes),
        ("profile_version", profile.version_bytes),
        ("frame_kind", profile.kind_bytes),
        ("terminal_status", profile.status_bytes),
        ("handle_byte_length", profile.handle_length_bytes),
        ("reserved", profile.reserved_bytes),
        ("stream_offset", profile.stream_offset_bytes),
        ("payload_byte_length", profile.payload_length_bytes),
    ):
        fields.append({"name": name, "offset": offset, "width": width})
        offset += width
    return {
        "generated": True,
        "protocol": {"package": "a13n.agent_envd.eip.v1", "eip_major": profile.eip_major},
        "magic_ascii": profile.magic.decode("ascii"),
        "profile_version": profile.profile_version,
        "header_bytes": profile.header_bytes,
        "byte_order": "network",
        "fields": fields,
        "kinds": profile.kinds,
        "reset_statuses": profile.reset_statuses,
        "maximum_handle_bytes": profile.maximum_handle_bytes,
        "maximum_payload_bytes": profile.maximum_payload_bytes,
    }


def write_artifacts(
    output_dir: Path,
    index: SchemaIndex,
    options: OptionReader,
    models_path: Path,
    data_frame_profile: DataFrameProfile,
) -> list[Path]:
    records = method_records(index, options)
    inventory = {
        "generated": True,
        "protocol": {"package": "a13n.agent_envd.eip.v1", "version": "0.1"},
        "method_count": len(records),
        "methods": records,
    }
    schema = build_schema(index, options, models_path)
    outputs = {
        "methods.json": inventory,
        "schema.json": schema,
        "openrpc.json": build_openrpc(records, schema),
        "data-frame-profile.json": build_data_frame_artifact(data_frame_profile),
    }
    paths: list[Path] = []
    for name, value in outputs.items():
        path = output_dir / name
        _write_json(path, value)
        paths.append(path)
    return paths
