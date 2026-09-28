"""Code-first client-side tools composed as native Pydantic AI ExternalToolsets."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_harness._json import dump_json_bytes
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.deferred import deferred_presentation
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY
from a13n_harness.tools.metadata import HARNESS_TOOL_METADATA_KEY

CLIENT_TOOLS_CAPABILITY_ID = "a13n.client-tools"
CLIENT_TOOL_MARKER_KEY = "a13n.harness.client-tool"
MAX_CLIENT_TOOLSETS = 32
MAX_CLIENT_TOOLS = 128
MAX_CLIENT_SCHEMA_BYTES = 64 * 1024
MAX_CLIENT_METADATA_BYTES = 16 * 1024
MAX_CLIENT_TEXT_LENGTH = 16 * 1024
_FORBIDDEN_METADATA_KEYS = {
    "authorization",
    "credential",
    "credentials",
    "grant",
    "invocation_grant",
    "policy",
    "server_correlation",
}


class ClientToolDefinition(BaseModel):
    """Portable model guidance for one externally executed client tool."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]*$")
    description: str = Field(min_length=1, max_length=MAX_CLIENT_TEXT_LENGTH)
    parameters_json_schema: dict[str, JsonValue]
    instruction: str | None = Field(default=None, min_length=1, max_length=MAX_CLIENT_TEXT_LENGTH)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    permission: Literal["inherit", "allow", "deny"] = "inherit"

    @field_validator("parameters_json_schema")
    @classmethod
    def _validate_schema(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if value.get("type") != "object":
            raise ValueError("client tool parameters_json_schema must have type='object'")
        _require_bounded_json(value, MAX_CLIENT_SCHEMA_BYTES, "client tool schema")
        return deepcopy(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if _metadata_contains_key(value, {HARNESS_TOOL_METADATA_KEY, CLIENT_TOOL_MARKER_KEY, TOOL_IDENTITY_KEY}):
            raise ValueError("client metadata contains a reserved Harness key")
        if _metadata_contains_key(value, _FORBIDDEN_METADATA_KEYS, case_insensitive=True):
            raise ValueError("client metadata contains an authority-bearing key")
        deferred_presentation(value)
        _require_bounded_json(value, MAX_CLIENT_METADATA_BYTES, "client tool metadata")
        return deepcopy(value)


class ClientToolsetDefinition(BaseModel):
    """One stable grouping of external client declarations."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    toolset_id: str = Field(min_length=1, max_length=256)
    tools: tuple[ClientToolDefinition, ...]

    @model_validator(mode="after")
    def _validate_tools(self) -> ClientToolsetDefinition:
        if not self.tools:
            raise ValueError("client toolset must contain at least one tool")
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("client tool names must be unique")
        return self


class ClientToolsSpec(BaseModel):
    """Definition defaults and whole-list replacement policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default_toolsets: tuple[ClientToolsetDefinition, ...] = ()
    allow_run_override: bool = False

    @model_validator(mode="after")
    def _validate_surface(self) -> ClientToolsSpec:
        _validate_toolsets(self.default_toolsets)
        return self


@dataclass(kw_only=True)
class ClientToolsCapability(AbstractCapability[AgentContext]):
    """Definition-selected owner that resolves and composes the effective client surface."""

    id: str | None = CLIENT_TOOLS_CAPABILITY_ID
    spec: ClientToolsSpec = field(default_factory=ClientToolsSpec)

    def __post_init__(self) -> None:
        if self.id != CLIENT_TOOLS_CAPABILITY_ID:
            raise ValueError(f"ClientToolsCapability.id must be {CLIENT_TOOLS_CAPABILITY_ID!r}")
        if not isinstance(self.spec, ClientToolsSpec):
            self.spec = ClientToolsSpec.model_validate(self.spec, strict=True)
        else:
            self.spec = self.spec.model_copy(deep=True)

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id="a13n-client-tools")

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext] | None:
        from a13n_harness.toolsets.client import ClientToolsToolset

        return ClientToolsToolset(self._effective_toolsets(ctx)).get_toolset()

    def _effective_toolsets(self, ctx: RunContext[AgentContext]) -> tuple[ClientToolsetDefinition, ...]:
        provenance = ctx.deps._capability_provenance
        if CLIENT_TOOLS_CAPABILITY_ID not in provenance.definition_ids:
            raise DefinitionError(
                "ClientToolsCapability must originate from the Agent definition.",
                code="capability_scope_invalid",
            )
        toolsets = ctx.deps.client_toolsets
        if toolsets is not None:
            if not self.spec.allow_run_override:
                raise DefinitionError(
                    "This Agent definition does not allow a client-tools run override.",
                    code="client_tools_override_forbidden",
                )
            return tuple(deepcopy(toolsets))
        return tuple(deepcopy(self.spec.default_toolsets))


def _validate_toolsets(toolsets: tuple[ClientToolsetDefinition, ...]) -> None:
    if len(toolsets) > MAX_CLIENT_TOOLSETS:
        raise ValueError("too many client toolsets")
    ids = [toolset.toolset_id for toolset in toolsets]
    if len(set(ids)) != len(ids):
        raise ValueError("client toolset IDs must be unique")
    names = [tool.name for toolset in toolsets for tool in toolset.tools]
    if len(names) > MAX_CLIENT_TOOLS:
        raise ValueError("too many client tools")
    if len(set(names)) != len(names):
        raise ValueError("client tool names must be unique across the effective surface")


def _metadata_contains_key(
    value: JsonValue,
    keys: set[str],
    *,
    case_insensitive: bool = False,
) -> bool:
    if isinstance(value, dict):
        expected = {key.lower() for key in keys} if case_insensitive else keys
        for key, item in value.items():
            candidate = key.lower() if case_insensitive else key
            if candidate in expected or _metadata_contains_key(
                item,
                keys,
                case_insensitive=case_insensitive,
            ):
                return True
    elif isinstance(value, list):
        return any(_metadata_contains_key(item, keys, case_insensitive=case_insensitive) for item in value)
    return False


def _require_bounded_json(value: Any, limit: int, description: str) -> None:
    try:
        encoded = dump_json_bytes(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{description} must be JSON-safe") from exc
    if len(encoded) > limit:
        raise ValueError(f"{description} is too large")
