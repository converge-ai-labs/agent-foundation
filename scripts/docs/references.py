"""Render the Environment configuration and Service settings references."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from a13n_harness.providers.environment.boxd.provider import (
    BoxdConnectionConfiguration,
    BoxdEnvironmentConfiguration,
)
from a13n_harness.providers.environment.configuration import HostLocalProviderConfiguration
from a13n_harness.providers.environment.daytona.provider import (
    DaytonaConnectionConfiguration,
    DaytonaEnvironmentConfiguration,
)
from a13n_harness.providers.environment.direct_local.configuration import DirectLocalEnvironmentConfiguration
from a13n_harness.providers.environment.docker.configuration import DockerEnvironmentConfiguration
from a13n_harness.providers.environment.docker.provider import DockerConnectionConfiguration
from a13n_harness.providers.environment.e2b.configuration import (
    E2BConnectionConfiguration,
    E2BCredential,
    E2BEnvironmentConfiguration,
)
from a13n_harness.providers.environment.local_envd.configuration import (
    LocalEnvdEnvironmentConfiguration,
    LocalEnvdLaunchConfiguration,
)
from a13n_harness.providers.environment.modal.provider import (
    ModalConnectionConfiguration,
    ModalCredential,
    ModalEnvironmentConfiguration,
)
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    RemoteEnvdEnvironmentConfiguration,
    WebSocketEnvdConnectionConfiguration,
)
from a13n_harness.providers.environment.runloop.provider import (
    RunloopConnectionConfiguration,
    RunloopEnvironmentConfiguration,
)
from a13n_harness.providers.environment.sprites.provider import (
    SpritesConnectionConfiguration,
    SpritesEnvironmentConfiguration,
)
from a13n_harness.providers.environment.vercel.provider import (
    VercelConnectionConfiguration,
    VercelEnvironmentConfiguration,
)
from a13n_service.providers.environment.recipes.daytona import DaytonaRecipe
from a13n_service.providers.environment.recipes.docker import DockerRecipe
from a13n_service.providers.environment.recipes.e2b import E2BRecipe
from a13n_service.providers.environment.recipes.modal import ModalRecipe
from a13n_service.providers.environment.recipes.sprites import SpritesRecipe
from a13n_service.providers.environment.recipes.vercel import VercelRecipe
from a13n_service.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def schema_label(schema: dict[str, Any], definitions: dict[str, Any] | None = None) -> str:
    """Label a schema; with `definitions`, a reference shows its choices or `object` instead of its name."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if definitions is None or name not in definitions:
            return name
        target = definitions[name]
        return "object" if "properties" in target else schema_label(target, definitions)
    if "anyOf" in schema or "oneOf" in schema:
        items = schema.get("anyOf", schema.get("oneOf", []))
        return " or ".join(schema_label(item, definitions) for item in items)
    if "enum" in schema:
        return ", ".join(json.dumps(value) for value in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"])
    if schema.get("type") == "array":
        return f"array of {schema_label(schema.get('items', {}), definitions)}"
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
    # A nullable field keeps its bounds on the non-null member of its union.
    bounded = {**next((item for item in schema.get("anyOf", ()) if item.get("type") != "null"), {}), **schema}
    values = []
    for key in keys:
        if key in bounded:
            value = f"{key}={json.dumps(bounded[key], ensure_ascii=False)}"
            # Regex brackets followed by parentheses otherwise become Markdown links.
            values.append(f"`{value}`" if key == "pattern" else value)
    return "; ".join(values) or "—"


def render_configuration() -> str:
    schema = Settings.model_json_schema()
    definitions = schema.get("$defs", {})
    groups: dict[str, list[str]] = defaultdict(list)

    def nested(field: dict[str, Any]) -> dict[str, Any] | None:
        """The definition of a field that is itself a section, such as `auth.mail`."""
        definition = definitions.get(field.get("$ref", "").rsplit("/", 1)[-1], {})
        return definition if "properties" in definition else None

    def add(section: str, path: str, variable: str, properties: dict[str, Any]) -> None:
        for name, field in properties.items():
            inner = nested(field)
            if inner is not None:
                # An environment variable carries a whole nested section as JSON.
                add(section, f"{path}.{name}", f"{variable}__{name.upper()}", inner["properties"])
                continue
            is_nested = path.count(".") > 0
            env = f"`{variable}` (JSON field `{name}`)" if is_nested else f"`{variable}__{name.upper()}`"
            row = (f"`{path}.{name}`", env, schema_label(field, definitions), constraints(field))
            groups[section].append("| " + " | ".join(cell(value) for value in row) + " |")

    for section, definition in schema["properties"].items():
        if "$ref" not in definition:
            continue  # `extensions`: sections a distribution declares, documented by that distribution
        section_schema = definitions[definition["$ref"].rsplit("/", 1)[-1]]
        add(section, section, f"A13N_{section.upper()}", section_schema["properties"])
    text = """---
title: Service settings reference
sidebarTitle: Settings reference
description: Every field of the Service settings, with its environment variable, type, bounds, and default.
---

Find a setting by its section, then use the row to choose its TOML key or environment variable and check the accepted type, bounds, and default. For a complete deployment configuration, start with [Configure Service](configuration.md); that guide covers precedence, role and storage requirements, and checks across fields.

For example, `server.port` is `port` in the TOML `[server]` section or `A13N_SERVER__PORT` in the environment. Rows marked **JSON field** share one environment variable: set `A13N_AUTH__MAIL` to a JSON object containing the mail settings. An environment value replaces that field's file value, including the whole object for nested settings.

These tables describe the built-in settings at this source version. Secret defaults are masked. Download [Service settings JSON](/reference/service-settings.json) for the full schema, including named enum and union definitions. Distribution-specific extension settings are documented by their distribution.

The tables are generated from `Settings`. Contributors updating those definitions regenerate the English reference with `uv run --locked python scripts/docs/references.py` and synchronize the translated rows.

"""
    for section, rows in groups.items():
        text += f"## `{section}`\n\n| Setting | Environment variable | Type / choices | Constraints and default |\n| --- | --- | --- | --- |\n"
        text += "\n".join(rows) + "\n\n"
    return text.rstrip() + "\n"


ENVIRONMENT_CONFIGURATION_MODELS = (
    DirectLocalEnvironmentConfiguration,
    LocalEnvdEnvironmentConfiguration,
    LocalEnvdLaunchConfiguration,
    DockerEnvironmentConfiguration,
    DockerRecipe,
    E2BEnvironmentConfiguration,
    E2BRecipe,
    DaytonaRecipe,
    ModalRecipe,
    VercelRecipe,
    SpritesRecipe,
    DaytonaEnvironmentConfiguration,
    ModalEnvironmentConfiguration,
    VercelEnvironmentConfiguration,
    SpritesEnvironmentConfiguration,
    RunloopEnvironmentConfiguration,
    BoxdEnvironmentConfiguration,
    RemoteEnvdEnvironmentConfiguration,
    HostLocalProviderConfiguration,
    DockerConnectionConfiguration,
    E2BConnectionConfiguration,
    E2BCredential,
    DaytonaConnectionConfiguration,
    ModalConnectionConfiguration,
    ModalCredential,
    VercelConnectionConfiguration,
    SpritesConnectionConfiguration,
    RunloopConnectionConfiguration,
    BoxdConnectionConfiguration,
    TokenCredential,
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    WebSocketEnvdConnectionConfiguration,
)


def render_environment_configuration() -> str:
    text = """---
title: Provider configuration reference
description: Generated Harness execution inputs, Service management recipes, account settings, and credentials.
---

Choose the model for the boundary you are configuring. Harness execution inputs describe an already selected target. Service management recipes add target creation and desktop preparation. Account settings and credentials are separate from both. Use [Configure Providers](configuration.md) to assemble these inputs.

A field marked **Required** must be supplied. **Default from model factory** means the model computes the value when constructed. The tables show field schemas, not cross-field validation or the values selected by a Host; follow the setup guide for automatic desktop defaults and valid combinations. Standalone daemon JSON uses [Envd configuration](../a13n-envd/configuration.md), not these models.

These tables are generated from the selected built-in models in `scripts/docs/references.py`. Contributors regenerate them with `uv run --locked python scripts/docs/references.py` and synchronize the Chinese page. Keep each execution model distinct from its Service recipe.

## Cloud Providers

Choose the execution column for a fixed-target Harness connector, or the management column for a Service template. A Service recipe's automatic image or snapshot is resolved before execution. See [cloud setup and limits](cloud-providers.md) for preparation and valid desktop combinations.

| Provider | Harness execution | Service management | Account | Credential |
| --- | --- | --- | --- | --- |
| Docker | [DockerEnvironmentConfiguration](#dockerenvironmentconfiguration) | [DockerRecipe](#dockerrecipe) | [DockerConnectionConfiguration](#dockerconnectionconfiguration) | Engine authentication |
| E2B | [E2BEnvironmentConfiguration](#e2benvironmentconfiguration) | [E2BRecipe](#e2brecipe) | [E2BConnectionConfiguration](#e2bconnectionconfiguration) | [E2BCredential](#e2bcredential) |
| Daytona | [DaytonaEnvironmentConfiguration](#daytonaenvironmentconfiguration) | [DaytonaRecipe](#daytonarecipe) | [DaytonaConnectionConfiguration](#daytonaconnectionconfiguration) | [TokenCredential](#tokencredential) |
| Modal | [ModalEnvironmentConfiguration](#modalenvironmentconfiguration) | [ModalRecipe](#modalrecipe) | [ModalConnectionConfiguration](#modalconnectionconfiguration) | [ModalCredential](#modalcredential) |
| Vercel Sandbox | [VercelEnvironmentConfiguration](#vercelenvironmentconfiguration) | [VercelRecipe](#vercelrecipe) | [VercelConnectionConfiguration](#vercelconnectionconfiguration) | [TokenCredential](#tokencredential) |
| Fly.io Sprites | [SpritesEnvironmentConfiguration](#spritesenvironmentconfiguration) | [SpritesRecipe](#spritesrecipe) | [SpritesConnectionConfiguration](#spritesconnectionconfiguration) | [TokenCredential](#tokencredential) |
| Runloop | [RunloopEnvironmentConfiguration](#runloopenvironmentconfiguration) | [RunloopEnvironmentConfiguration](#runloopenvironmentconfiguration) | [RunloopConnectionConfiguration](#runloopconnectionconfiguration) | [TokenCredential](#tokencredential) |
| boxd | [BoxdEnvironmentConfiguration](#boxdenvironmentconfiguration) | [BoxdEnvironmentConfiguration](#boxdenvironmentconfiguration) | [BoxdConnectionConfiguration](#boxdconnectionconfiguration) | [TokenCredential](#tokencredential) |

Runloop and boxd use the same configuration model for execution and management; they have no separate Service desktop recipe.

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
        "docs/environments/configuration-reference.md": render_environment_configuration(),
        "docs/a13n-service/configuration-reference.md": render_configuration(),
        "scripts/docs/service-settings.schema.json": json.dumps(Settings.model_json_schema(), indent=2) + "\n",
    }
    for name, content in outputs.items():
        (ROOT / name).write_text(content, encoding="utf-8")
        print(name)


if __name__ == "__main__":
    main()
