"""What the file and record memory tools share: offering tools per mount access, and their failures."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Annotated, Any, Protocol

from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.tools import Tool, ToolDefinition

from a13n_harness.context import AgentContext
from a13n_harness.providers.memory import MemoryAccess
from a13n_harness.tools.metadata import (
    HarnessTool,
    HarnessToolMetadata,
    ToolEffect,
    ToolOutputPolicy,
    recovery_retryable,
)

from ._instructions import InstructionFunctionToolset

MemoryName = Annotated[str, Field(description="The memory's mount name")]


class Mount(Protocol):
    @property
    def access(self) -> MemoryAccess: ...


@dataclass(frozen=True, slots=True)
class MemoryTool:
    access: MemoryAccess
    effects: frozenset[ToolEffect]
    max_output_bytes: int
    description: str


def memory_toolset[K: str](
    owner: object,
    kind: str,
    tools: Mapping[K, MemoryTool],
    enabled: Collection[K],
    mounts: Mapping[str, Mount],
    instruction: str,
) -> InstructionFunctionToolset | None:
    """The enabled tools some mount's access allows, each implemented by `owner`'s method of the tool's key.

    A tool's `memory` enum lists exactly the mounts whose access allows it.
    """
    offered: list[Tool[AgentContext]] = []
    for key, spec in tools.items():
        names = [name for name, mount in mounts.items() if allows(mount.access, spec.access)]
        if key in enabled and names:
            offered.append(_tool(getattr(owner, key), kind, key, spec, names))
    if not offered:
        return None
    return InstructionFunctionToolset(tools=offered, id=f"a13n-memory-{kind}-tools", instructions=[instruction])


def _tool(function: Callable[..., Any], kind: str, key: str, spec: MemoryTool, names: list[str]) -> Tool[AgentContext]:
    def prepare(ctx: RunContext[AgentContext], tool_def: ToolDefinition) -> ToolDefinition:
        del ctx
        schema = deepcopy(tool_def.parameters_json_schema)
        schema["properties"]["memory"]["enum"] = names
        return replace(tool_def, parameters_json_schema=schema)

    tool = HarnessTool(
        function,
        name=f"memory_{kind}_{key}",
        description=spec.description,
        prepare=prepare,
        harness_metadata=HarnessToolMetadata(
            tool_id=f"memory.{kind}.{key}",
            effects=spec.effects,
            credential_audiences=(),
            idempotency="read_only" if spec.access == "read" else "none",
            output_policy=ToolOutputPolicy(
                max_inline_bytes=spec.max_output_bytes,
                max_output_bytes=spec.max_output_bytes,
                overflow="truncate",
            ),
        ),
    )
    # Reads may run again after an unknown outcome; a write's outcome stays unknown.
    return recovery_retryable(tool) if spec.access == "read" else tool


def mounted[M: Mount](mounts: Mapping[str, M], memory: str, access: MemoryAccess) -> M:
    """The mount a call names, checked again at the call against the access it needs."""
    mount = mounts.get(memory)
    if mount is None:
        raise failure("unknown_memory", f"No memory is mounted as {memory!r}.")
    if not allows(mount.access, access):
        raise failure("forbidden", f"The memory {memory!r} is mounted read-only.")
    return mount


def allows(granted: MemoryAccess, needed: MemoryAccess) -> bool:
    return needed == "read" or granted == "write"


def failure(code: str, message: str, **details: JsonValue) -> ToolFailed:
    return ToolFailed(json.dumps({"error": code, "message": message, **details}, ensure_ascii=False))


__all__ = ["MemoryName", "MemoryTool", "Mount", "allows", "failure", "memory_toolset", "mounted"]
