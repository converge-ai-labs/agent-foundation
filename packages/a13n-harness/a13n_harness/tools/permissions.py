"""Declarative tool permissions shared by embedded and hosted Agents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.native_tools import WebSearchTool

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.identity import ToolIdentity, ToolPermissionMode

TOOL_PERMISSIONS_CAPABILITY_ID = "a13n.tool-permissions"
type ToolPermissionSetting = ToolPermissionMode | Literal["auto"]


def validate_selector(selector: str) -> str:
    """Accept exact IDs, a trailing source prefix wildcard, or the global wildcard."""
    if not selector or selector != selector.strip() or len(selector) > 1024:
        raise ValueError("Permission selectors must be bounded non-blank strings")
    if "*" in selector and selector != "*" and not (selector.endswith(("/*", ".*")) and selector.count("*") == 1):
        raise ValueError("Selectors support only exact IDs, trailing '/*' or '.*', and '*'")
    return selector


def match_selector[T](rules: Mapping[str, T], tool_id: str) -> T | None:
    """Choose exact, then longest namespace prefix, then global default."""
    if tool_id in rules:
        return rules[tool_id]
    matches = [key for key in rules if key != "*" and key.endswith("*") and tool_id.startswith(key[:-1])]
    if matches:
        return rules[max(matches, key=len)]
    return rules.get("*")


class ToolPermissions(BaseModel):
    """Portable rules; optional auto resolves the matched tool's trusted default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default: ToolPermissionSetting = "auto"
    rules: dict[str, ToolPermissionSetting] = Field(default_factory=dict, max_length=1024)

    @field_validator("rules")
    @classmethod
    def _validate_rules(cls, rules: dict[str, ToolPermissionSetting]) -> dict[str, ToolPermissionSetting]:
        for selector in rules:
            validate_selector(selector)
        return rules

    def resolve(self, identity: ToolIdentity) -> ToolPermissionMode:
        setting = match_selector(self.rules, identity.tool_id) or self.default
        return identity.default_mode if setting == "auto" else setting


@dataclass(init=False)
class ToolPermissionsCapability(AbstractCapability[AgentContext]):
    """Select invocation modes without replacing Host resource authorization."""

    id = TOOL_PERMISSIONS_CAPABILITY_ID

    def __init__(self, permissions: ToolPermissions | None = None) -> None:
        self.permissions = (permissions or ToolPermissions()).model_copy(deep=True)

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if self.permissions.resolve(ToolIdentity("web.search", "allow")) != "allow" and any(
            isinstance(tool, WebSearchTool) for tool in request_context.model_request_parameters.native_tools
        ):
            raise DefinitionError(
                "web.search permissions require Host search, not provider-native search.",
                code="tool_permission_unsupported",
            )
        return request_context

    @classmethod
    def from_spec(
        cls,
        *,
        default: ToolPermissionSetting = "auto",
        rules: dict[str, ToolPermissionSetting] | None = None,
    ) -> ToolPermissionsCapability:
        return cls(ToolPermissions(default=default, rules=rules or {}))
