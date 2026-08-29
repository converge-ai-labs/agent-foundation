"""Native Harness Toolset reconstructed from frozen Agent Connector declarations."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from a13n_harness import AgentContext
from a13n_harness.tools import (
    HARNESS_TOOL_METADATA_KEY,
    HarnessToolMetadata,
    ToolOutputPolicy,
)
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema

from .domain import AgentConnectorDeclaration, ConnectorTurnSelection, FrozenConnectorTool, PrincipalRef
from .errors import ConnectorError
from .provider import ConnectorProviderContext
from .runtime import ConnectorToolRuntime

_ARGS_VALIDATOR = SchemaValidator(core_schema.any_schema())


class ConnectorManagedToolset(AbstractToolset[AgentContext]):
    """Expose exact frozen Connector tools through the mandatory Harness boundary."""

    def __init__(
        self,
        runtime: ConnectorToolRuntime,
        *,
        organization_id: str,
        workspace_id: str,
        principal: PrincipalRef,
        declarations: Sequence[AgentConnectorDeclaration],
        selections: Sequence[ConnectorTurnSelection],
        context_factory: Callable[[str], ConnectorProviderContext],
    ) -> None:
        self._runtime = runtime
        self._organization_id = organization_id
        self._workspace_id = workspace_id
        self._principal = principal
        self._context_factory = context_factory
        selection_by_index = {selection.declaration_index: selection for selection in selections}
        if len(selection_by_index) != len(selections) or set(selection_by_index) != set(range(len(declarations))):
            raise ConnectorError("Connector Turn selections are incomplete.", code="connection_incompatible")
        entries: dict[str, _ToolEntry] = {}
        for index, declaration in enumerate(declarations):
            selection = selection_by_index[index]
            if declaration.connector_revision_id != selection.connector_revision_id:
                raise ConnectorError("Connector Turn selection is incompatible.", code="connection_incompatible")
            for frozen in declaration.tools:
                if frozen.model_tool_name in entries:
                    raise ConnectorError("Connector model tool names collide.", code="tool_contract_incompatible")
                entries[frozen.model_tool_name] = _ToolEntry(
                    declaration=declaration,
                    selection=selection,
                    frozen=frozen,
                    validator=_schema_validator(frozen),
                )
        self._entries = entries

    @property
    def id(self) -> str:
        return "a13n-connector-tools"

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        del ctx
        return {
            name: ToolsetTool(
                toolset=self,
                tool_def=_tool_definition(entry.frozen),
                max_retries=0,
                args_validator=_ARGS_VALIDATOR,
            )
            for name, entry in self._entries.items()
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentContext],
        tool: ToolsetTool[AgentContext],
    ) -> Any:
        del ctx, tool
        entry = self._entries.get(name)
        if entry is None:
            raise ConnectorError("Connector tool was not found.", code="tool_not_found")
        try:
            entry.validator.validate(tool_args)
        except ValidationError:
            raise ConnectorError("Connector tool arguments are invalid.", code="invalid_request") from None
        result = await self._runtime.call_tool(
            organization_id=self._organization_id,
            workspace_id=self._workspace_id,
            declaration=entry.declaration,
            selection=entry.selection,
            provider_tool_name=entry.frozen.provider_tool_name,
            arguments=tool_args,
            principal=self._principal,
            context=self._context_factory(entry.frozen.tool_id),
        )
        return result.value


class _ToolEntry:
    __slots__ = ("declaration", "frozen", "selection", "validator")

    def __init__(
        self,
        *,
        declaration: AgentConnectorDeclaration,
        selection: ConnectorTurnSelection,
        frozen: FrozenConnectorTool,
        validator: Draft202012Validator,
    ) -> None:
        self.declaration = declaration
        self.selection = selection
        self.frozen = frozen
        self.validator = validator


def _schema_validator(tool: FrozenConnectorTool) -> Draft202012Validator:
    try:
        Draft202012Validator.check_schema(tool.parameters_json_schema)
        return Draft202012Validator(tool.parameters_json_schema)
    except SchemaError:
        raise ConnectorError("Connector tool schema is invalid.", code="tool_contract_incompatible") from None


def _tool_definition(tool: FrozenConnectorTool) -> ToolDefinition:
    metadata = HarnessToolMetadata(
        tool_id=tool.tool_id,
        effects=frozenset(tool.effects),
        credential_audiences=tool.credential_audiences,
        idempotency=tool.idempotency,
        output_policy=ToolOutputPolicy.model_validate(tool.output_policy),
    )
    return ToolDefinition(
        name=tool.model_tool_name,
        description=tool.description,
        parameters_json_schema=tool.parameters_json_schema,
        metadata={HARNESS_TOOL_METADATA_KEY: metadata},
    )
