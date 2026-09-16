"""Render Environment configuration, Service settings, and Native API references."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from a13n_environment.direct_local.configuration import DirectLocalProviderConfiguration
from a13n_environment.docker.configuration import DockerProviderConfiguration
from a13n_environment.docker.factory import DockerBackendConfiguration
from a13n_environment.e2b.configuration import E2BBackendConfiguration, E2BCredential, E2BProviderConfiguration
from a13n_environment.local_envd.configuration import LocalEnvdProviderConfiguration
from a13n_environment.management import HostLocalProviderConfiguration
from a13n_environment.remote_envd.configuration import (
    HttpEnvdBackendConfiguration,
    HttpEnvdCredential,
    RemoteEnvdProviderConfiguration,
    WebSocketEnvdBackendConfiguration,
)
from a13n_service.configuration.sources import configuration_fields
from a13n_service.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = ROOT / "proto/a13n-service/openapi.json"


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def schema_label(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in schema or "oneOf" in schema:
        return " or ".join(schema_label(item) for item in schema.get("anyOf", schema.get("oneOf", [])))
    if "enum" in schema:
        return ", ".join(json.dumps(value) for value in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"])
    if schema.get("type") == "array":
        return f"array of {schema_label(schema.get('items', {}))}"
    return str(schema.get("type", "schema-defined value"))


def constraints(schema: dict[str, Any]) -> str:
    keys = (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "pattern",
        "format",
        "default",
    )
    return "; ".join(f"{key}={json.dumps(schema[key], ensure_ascii=False)}" for key in keys if key in schema) or "—"


def render_configuration() -> str:
    schema = Settings.model_json_schema()
    definitions = schema.get("$defs", {})
    groups: dict[str, list[str]] = defaultdict(list)
    for environment, path, _field in configuration_fields(Settings):
        selected = schema
        for part in path:
            if "$ref" in selected:
                selected = definitions[selected["$ref"].rsplit("/", 1)[-1]]
            selected = selected["properties"][part]
        label = schema_label(selected)
        if "$ref" in selected:
            target = definitions[selected["$ref"].rsplit("/", 1)[-1]]
            if "enum" in target:
                label = schema_label(target)
        groups[path[0]].append(
            "| "
            + " | ".join(
                cell(value)
                for value in (
                    f"`{'.'.join(path)}`",
                    f"`{environment}`",
                    label,
                    constraints(selected),
                )
            )
            + " |"
        )
    text = """# Service configuration reference

This field reference is generated from the same `Settings` and `configuration_fields()` definitions used by the Service loader. Run `uv run --locked python scripts/docs/references.py` after changing those definitions. Do not independently edit generated rows.

Use [Configure Service](configuration.md) for precedence, examples, role/storage requirements, and cross-field validation. Types and field constraints below do not replace those combined checks. Secret defaults are masked by the schema; this reference never reads deployment environment values. Defaults apply to the source version, not every historical release.

The complete machine-readable validation schema, including named enum/union definitions, is available as [Service settings JSON](../assets/reference/service-settings.json).

"""
    for section, rows in groups.items():
        text += f"## `{section}`\n\n| Setting | Environment variable | Type / choices | Constraints and default |\n| --- | --- | --- | --- |\n"
        text += "\n".join(rows) + "\n\n"
    return text.rstrip() + "\n"


def render_native_api() -> str:
    schema = json.loads(OPENAPI.read_text(encoding="utf-8"))
    groups: dict[str, list[tuple[str, str, dict[str, Any], list[dict[str, Any]]]]] = defaultdict(list)
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            tag = operation.get("tags", ["other"])[0]
            groups[tag].append((path, method, operation, methods.get("parameters", [])))
    text = """# Native HTTP API reference

This reference covers every operation in the checked-in Service Native OpenAPI contract, grouped by its owning API family. It is generated by `scripts/docs/references.py`; the Service contract generation gate owns parity between this contract and the Service application.

Start with [Agents, Threads, and Runs](agents-and-runs.md) for the execution workflow and [HTTP contracts](http-contracts.md) for authentication, preconditions, idempotency, pagination, and errors. A listed route is not an authorization grant. Different credential types have different scope.

Download [the complete Native OpenAPI JSON](../assets/reference/service-openapi.json) for all nested request/response models, formats, discriminators, required fields, and validation constraints. Named schemas below refer to that contract's `components.schemas`; they are not unvalidated free-form JSON.

This document excludes non-Native boundaries: operational probes, schema/documentation routes, provider event ingress, AG-UI, A2A, and the notification WebSocket. Those are described under [Streams, events, and gateways](streams-and-events.md) and [Background tasks](background-tasks.md).

"""
    for tag, operations in sorted(groups.items()):
        text += f"## {tag}\n\n"
        for path, method, operation, common in operations:
            text += f"### `{method.upper()} {path}`\n\n"
            text += operation.get("summary", "Native operation") + ".\n\n"
            if description := operation.get("description"):
                text += description + "\n\n"
            parameters = [*common, *operation.get("parameters", [])]
            if parameters:
                text += "| Parameter | Location | Required | Type / schema | Constraints and default |\n| --- | --- | --- | --- | --- |\n"
                for parameter in parameters:
                    shape = parameter.get("schema", {})
                    text += (
                        "| "
                        + " | ".join(
                            cell(value)
                            for value in (
                                f"`{parameter['name']}`",
                                parameter["in"],
                                str(parameter.get("required", False)).lower(),
                                schema_label(shape),
                                constraints(shape),
                            )
                        )
                        + " |\n"
                    )
                text += "\n"
            if body := operation.get("requestBody"):
                text += "Request body: " + ("required" if body.get("required") else "optional") + ".\n\n"
                for media, content in body.get("content", {}).items():
                    text += f"- `{media}`: `{schema_label(content.get('schema', {}))}`.\n"
                text += "\n"
            text += "Responses:\n\n"
            for status, response in operation["responses"].items():
                models = "; ".join(
                    f"{media}: {schema_label(value.get('schema', {}))}"
                    for media, value in response.get("content", {}).items()
                )
                text += f"- **{status}** — {response.get('description', '')}"
                if models:
                    text += f" (`{models}`)"
                text += ".\n"
            text += "\n"
    return text.rstrip() + "\n"


ENVIRONMENT_CONFIGURATION_MODELS = (
    DirectLocalProviderConfiguration,
    LocalEnvdProviderConfiguration,
    DockerProviderConfiguration,
    E2BProviderConfiguration,
    RemoteEnvdProviderConfiguration,
    HostLocalProviderConfiguration,
    DockerBackendConfiguration,
    E2BBackendConfiguration,
    E2BCredential,
    HttpEnvdBackendConfiguration,
    HttpEnvdCredential,
    WebSocketEnvdBackendConfiguration,
)


def render_environment_configuration() -> str:
    text = """# Provider configuration reference

Generated from the current built-in Provider Pydantic models by `scripts/docs/references.py`. Do not independently edit the rows. Use [Configure Providers](configuration.md) for authoring, configuration/runtime/state boundaries, and cross-field restrictions. These are Provider settings, not standalone daemon JSON defaults.

Required means no default. Fields backed by a factory have a model-computed default; no Host environment or credential store is read while generating this page. Named schema sections below include nested roots, mounts, and shell profiles. Runtime clients and authoritative target state do not belong in these template configuration objects.

"""
    emitted: set[str] = set()
    for model in ENVIRONMENT_CONFIGURATION_MODELS:
        schema = model.model_json_schema()
        definitions = schema.get("$defs", {})
        for name, value in ((model.__name__, schema), *definitions.items()):
            if name in emitted:
                continue
            emitted.add(name)
            text += f"## `{name}`\n\n"
            if description := value.get("description"):
                text += description + "\n\n"
            if "properties" not in value:
                text += f"Choices: `{schema_label(value)}`.\n\n"
                continue
            text += "| Field | Required | Type / choices | Constraints and default |\n| --- | --- | --- | --- |\n"
            for field, shape in value["properties"].items():
                label = schema_label(shape)
                if "$ref" in shape:
                    target = definitions[shape["$ref"].rsplit("/", 1)[-1]]
                    if "enum" in target:
                        label = schema_label(target)
                required = field in value.get("required", [])
                detail = constraints(shape)
                if not required and "default" not in shape:
                    detail += "; default from model factory"
                text += (
                    "| "
                    + " | ".join(
                        cell(item)
                        for item in (
                            f"`{field}`",
                            str(required).lower(),
                            label,
                            detail,
                        )
                    )
                    + " |\n"
                )
            text += "\n"
    return text.rstrip() + "\n"


def main() -> None:
    outputs = {
        "docs/a13n-environment/configuration-reference.md": render_environment_configuration(),
        "docs/a13n-service/configuration-reference.md": render_configuration(),
        "docs/a13n-service/api-reference.md": render_native_api(),
        "scripts/docs/service-settings.schema.json": json.dumps(Settings.model_json_schema(), indent=2) + "\n",
    }
    for name, content in outputs.items():
        (ROOT / name).write_text(content, encoding="utf-8")
        print(name)


if __name__ == "__main__":
    main()
