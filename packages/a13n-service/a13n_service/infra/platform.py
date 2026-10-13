"""Explicit admission and operation effects shared by HTTP, MCP and internal agents."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

type PlatformGroup = Literal[
    "agents", "models", "connections", "skills", "environment_templates", "providers", "execution"
]

PLATFORM_GROUPS: tuple[PlatformGroup, ...] = (
    "agents",
    "models",
    "connections",
    "skills",
    "environment_templates",
    "providers",
    "execution",
)
PLATFORM_METADATA = "x-a13n-platform"
RUN_CREDENTIAL = "platform_run_credential"
RUN_GROUPS = "platform_run_groups"


class PlatformOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    group: PlatformGroup
    effect: Literal["read", "write"]


def platform_operation(group: PlatformGroup, effect: Literal["read", "write"]) -> dict:
    """Admission is explicit at each owning route, including the effects of read-only POSTs."""
    return {"x-a13n-mcp": True, PLATFORM_METADATA: PlatformOperation(group=group, effect=effect).model_dump()}
