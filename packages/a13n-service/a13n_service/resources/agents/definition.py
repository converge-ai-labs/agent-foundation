"""The parts of a Harness definition compiled from configuration alone: output type and tool permissions.

Revision validation compiles them once to refuse what the builder could not compile, so a stored revision
always builds.
"""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from a13n_harness.output_schema import structured_output_type
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog, HarnessPluginFactoryContext
from a13n_harness.tools import (
    ToolExposure,
    ToolPermissions,
    ToolPermissionSetting,
    ToolsetExposure,
    source_tool_id,
    source_tool_prefix,
)
from referencing import Registry, Resource
from referencing.exceptions import CannotDetermineSpecification, Unresolvable
from referencing.jsonschema import DRAFT202012

from a13n_service.infra.errors import invalid
from a13n_service.resources.agents import toolsets
from a13n_service.resources.agents.schemas import AgentConfig, JsonObject, OutputSpec

QUESTION_TOOL = "ask_user_question"
_MAX_REFERENCE_EXPANSIONS = 1024


def output_type(spec: OutputSpec | None) -> Any:
    """`str`, one structured output type, or a tuple of them for variants; `$ref`s are inlined.

    Raises `invalid_argument` at `output_spec` for a schema the Harness cannot enforce.
    """
    if spec is None or (spec.schema_ is None and spec.variants is None):
        return str
    try:
        if spec.schema_ is not None:
            schema = _inline(spec.schema_, spec.resources)
            return structured_output_type(schema, name=spec.name, description=spec.description)
        return tuple(
            structured_output_type(
                _inline(variant.schema_, variant.resources), name=variant.name, description=variant.description
            )
            for variant in spec.variants or ()
        )
    except Exception as error:
        # Schema checks raise their own exception types, whose messages can quote the whole schema.
        reason = str(error) if isinstance(error, ValueError) else "the schema is not a valid JSON Schema"
        raise invalid("output_spec", reason) from None


def _inline(schema: JsonObject, resources: Mapping[str, JsonObject]) -> dict[str, Any]:
    """The schema with every `$ref` replaced by its target, because model providers accept no references."""
    try:
        root = DRAFT202012.create_resource(deepcopy(schema))
        registry = Registry().with_resources(
            (name, DRAFT202012.create_resource(deepcopy(resource))) for name, resource in resources.items()
        )
        return _expand(root.contents, registry.resolver_with_root(root), frozenset(), [0])
    except CannotDetermineSpecification:
        raise ValueError("a resource declares an unknown $schema") from None
    except Unresolvable as error:
        raise ValueError(f"unresolvable reference {error.ref!r}") from None


def _expand(value: Any, resolver: Any, active: frozenset[int], expansions: list[int]) -> Any:
    if isinstance(value, list):
        return [_expand(item, resolver, active, expansions) for item in value]
    if not isinstance(value, Mapping):
        return deepcopy(value)
    reference = value.get("$ref")
    if isinstance(reference, str):
        expansions[0] += 1
        if expansions[0] > _MAX_REFERENCE_EXPANSIONS:
            raise ValueError("the schema expands too many references")
        resolved = resolver.lookup(reference)
        if id(resolved.contents) in active:
            raise ValueError("the schema is recursive")
        target = _expand(resolved.contents, resolved.resolver, active | {id(resolved.contents)}, expansions)
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if not siblings:
            return target
        # A `$ref` with sibling keywords applies both; the target's type stays visible at the top.
        combined: dict[str, Any] = {"allOf": [target, _expand(siblings, resolver, active, expansions)]}
        if isinstance(target, Mapping) and isinstance(target.get("type"), str):
            combined["type"] = target["type"]
        return combined
    nested = resolver.in_subresource(Resource.from_contents(dict(value), default_specification=DRAFT202012))
    return {key: _expand(item, nested, active, expansions) for key, item in value.items()}


def tool_permissions(config: AgentConfig, connection_types: Mapping[str, str]) -> ToolPermissions:
    """Every permission the configuration sets, as rules keyed by the tool identities the Harness assigns.

    `connection_types` maps each selected connection to its type: MCP servers identify their tools under the
    `mcp` kind, connector toolsets under the plain `tool` kind.
    """
    rules = toolsets.permission_rules(config.toolsets)
    for selection in config.connection_tools:
        source, kind = selection.connection_id, "mcp" if connection_types[selection.connection_id] == "mcp" else "tool"
        if selection.tools is None:
            rules[source_tool_prefix(source, kind=kind) + "*"] = selection.permission
            rules.update(
                (source_tool_id(source, name, kind=kind), mode) for name, mode in selection.permissions.items()
            )
        else:
            rules.update(
                (source_tool_id(source, name, kind=kind), selection.permissions.get(name, selection.permission))
                for name in selection.tools
            )
    rules.update(
        (source_tool_id(source.toolset_id, tool.name), tool.permission)
        for source in config.client_toolsets
        for tool in source.tools
    )
    return ToolPermissions(rules=rules)


def tool_exposure(config: AgentConfig, plugins: HarnessPluginFactoryCatalog) -> ToolExposure:
    """Compile source selections to native Toolset identities without changing their tools."""
    sources = toolsets.exposure_rules(config.toolsets)

    def add(toolset_id: str, policy: ToolsetExposure, path: str) -> None:
        if not toolset_id.strip() or toolset_id in sources:
            raise invalid(path, f"native Toolset ID {toolset_id!r} must be nonblank and unique across sources")
        if policy.description is None and (
            policy.exposure == "progressive" or "progressive" in policy.tool_exposure.values()
        ):
            raise invalid(f"{path}.description", "required for progressive exposure")
        sources[toolset_id] = policy

    for index, selection in enumerate(config.connection_tools):
        add(selection.connection_id, _exposure(selection), f"connection_tools.{index}")
    for index, source in enumerate(config.client_toolsets):
        add(source.toolset_id, _exposure(source), f"client_toolsets.{index}")
    for index, selected in enumerate(config.plugins):
        context = HarnessPluginFactoryContext(
            plugin_key=selected.plugin_key,
            plugin_id=selected.instance_name,
            configuration=selected.config,
            extensions={},
        )
        for toolset_id, policy in plugins.tool_exposure(context, _exposure(selected)).items():
            add(toolset_id, policy, f"plugins.{index}")
    return ToolExposure(
        toolsets=sources,
        progressive_search=config.progressive_search,
        strategy=config.progressive_search_strategy,
    )


def _exposure(selection: ToolsetExposure) -> ToolsetExposure:
    return ToolsetExposure(
        exposure=selection.exposure,
        tool_exposure=selection.tool_exposure,
        description=selection.description,
    )


def declared_permissions(config: AgentConfig) -> set[ToolPermissionSetting]:
    """The permission values the configuration uses anywhere, regardless of tool identities."""
    return {
        *toolsets.permission_rules(config.toolsets).values(),
        *(selection.permission for selection in config.connection_tools),
        *(mode for selection in config.connection_tools for mode in selection.permissions.values()),
    }
