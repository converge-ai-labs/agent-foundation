"""Model-facing materialization for externally executed client tools."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset, ExternalToolset

from a13n_harness.context import AgentContext

if TYPE_CHECKING:
    from a13n_harness.tools.client import ClientToolsetDefinition


class _ClientExternalToolset(ExternalToolset[AgentContext]):
    def __init__(self, tool_defs: list[ToolDefinition], *, id: str, instructions: str | None) -> None:
        super().__init__(tool_defs, id=id)
        self._instructions = instructions

    async def get_instructions(self, ctx: RunContext[AgentContext]) -> str | None:
        del ctx
        return self._instructions


class ClientToolsToolset:
    """Own native model schemas for one effective client-tool surface."""

    def __init__(self, toolsets: tuple[ClientToolsetDefinition, ...]) -> None:
        self._toolsets = tuple(deepcopy(toolsets))

    def get_toolset(self) -> AbstractToolset[AgentContext] | None:
        from a13n_harness.tools.client import CLIENT_TOOL_MARKER_KEY

        native: list[ExternalToolset[AgentContext]] = []
        for toolset in self._toolsets:
            definitions: list[ToolDefinition] = []
            for tool in toolset.tools:
                metadata = deepcopy(tool.metadata)
                metadata[CLIENT_TOOL_MARKER_KEY] = {
                    "declared_name": tool.name,
                    "toolset_id": toolset.toolset_id,
                }
                definitions.append(
                    ToolDefinition(
                        name=tool.name,
                        description=tool.description,
                        parameters_json_schema=deepcopy(tool.parameters_json_schema),
                        metadata=metadata,
                    )
                )
            instructions = "\n\n".join(
                f"Client tool `{tool.name}`: {tool.instruction}"
                for tool in toolset.tools
                if tool.instruction is not None
            )
            native.append(
                _ClientExternalToolset(
                    definitions,
                    id=toolset.toolset_id,
                    instructions=instructions or None,
                )
            )
        if not native:
            return None
        if len(native) == 1:
            return native[0]
        return CombinedToolset(native)


__all__ = ["ClientToolsToolset"]
