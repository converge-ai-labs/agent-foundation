"""Run-time Agent selection and effective-config construction."""

from __future__ import annotations

from typing import Literal

from a13n_harness.tools import ToolPermissions
from pydantic import Field, model_validator

from a13n_service.memory.domain import MemorySelection
from a13n_service.search.domain import SearchSelection

from .domain import (
    AgentConfig,
    AgentModel,
    AgentReviewer,
    AgentRunOverride,
    AssetPublicationConfig,
    BoundedKey,
    ClientToolDefinition,
    ConnectionToolSelection,
    InputAdapterConfig,
    OutputSpec,
    PluginSelection,
    ProtocolConfig,
    RetryConfig,
    SecretRequirement,
    SkillSelection,
    StrictModel,
    SubagentSelection,
)
from .errors import invalid_run_override


class MergedAgentRunConfig(StrictModel):
    """Typed non-secret config after applying one Run override to a Revision."""

    memory: MemorySelection | None = Field(default=None, exclude_if=lambda value: value is None)
    search: SearchSelection | None = Field(default=None, exclude_if=lambda value: value is None)
    permissions: ToolPermissions | None = Field(default=None, exclude_if=lambda value: value is None)
    reviewer: AgentReviewer | None = Field(default=None, exclude_if=lambda value: value is None)
    subagent_mode: Literal["inline", "async"] = "inline"
    model: AgentModel
    instructions: str
    input_adapter: InputAdapterConfig
    plugins: tuple[PluginSelection, ...] = Field(default=(), max_length=128)
    skills: tuple[SkillSelection, ...] = Field(default=(), max_length=512)
    connection_tools: tuple[ConnectionToolSelection, ...] = Field(default=(), max_length=128)
    subagents: dict[BoundedKey, SubagentSelection] = Field(default_factory=dict, max_length=128)
    client_tools: tuple[ClientToolDefinition, ...] = Field(default=(), max_length=128)
    output_spec: OutputSpec | None = None
    retries: RetryConfig | None = None
    secret_requirements: tuple[SecretRequirement, ...] = Field(default=(), max_length=128)
    asset_publication: AssetPublicationConfig | None = None
    protocol: ProtocolConfig

    @model_validator(mode="after")
    def validate_unique_selections(self) -> MergedAgentRunConfig:
        for path, values in (
            ("plugins", tuple(item.instance_name for item in self.plugins)),
            ("skills", tuple(item.skill_key for item in self.skills)),
            ("client_tools", tuple(item.name for item in self.client_tools)),
            ("connection_tools", tuple(item.connection_id for item in self.connection_tools)),
        ):
            if len(values) != len(set(values)):
                raise invalid_run_override(path, "duplicate_selection")
        return self


def merge_agent_run_override(
    base: AgentConfig,
    override: AgentRunOverride | None,
) -> MergedAgentRunConfig:
    """Apply the finite typed override contract without resolving managed resources."""

    if override is None:
        return MergedAgentRunConfig.model_validate(base.model_dump(mode="python", by_alias=True))

    fields = override.model_fields_set
    model = base.model
    if "model" in fields:
        if override.model is None:
            raise invalid_run_override("model", "null_not_allowed")
        model_fields = override.model.model_fields_set
        model_key = _required_patch_value(
            override.model.model_key,
            present="model_key" in model_fields,
            inherited=base.model.model_key,
            path="model.model_key",
        )
        settings = base.model.settings
        if "settings" in model_fields:
            settings = {} if override.model.settings is None else {**settings, **override.model.settings}
        characteristics = _required_patch_value(
            override.model.characteristics,
            present="characteristics" in model_fields,
            inherited=base.model.characteristics,
            path="model.characteristics",
        )
        model = AgentModel(
            model_key=model_key,
            settings=settings,
            characteristics=characteristics,
        )

    instructions = base.instructions
    if "instructions" in fields:
        if override.instructions is None:
            raise invalid_run_override("instructions", "null_not_allowed")
        instructions = override.instructions

    plugins = _replace_list(
        inherited=base.plugins,
        value=override.plugins,
        present="plugins" in fields,
        path="plugins",
    )
    skills = _replace_list(
        inherited=base.skills,
        value=override.skills,
        present="skills" in fields,
        path="skills",
    )
    connection_tools = _replace_list(
        inherited=base.connection_tools,
        value=override.connection_tools,
        present="connection_tools" in fields,
        path="connection_tools",
    )
    client_tools = _replace_list(
        inherited=base.client_tools,
        value=override.client_tools,
        present="client_tools" in fields,
        path="client_tools",
    )
    protocol = base.protocol
    if "client_tools" in fields:
        selected_client_tool_names = {item.name for item in client_tools}
        missing_required = tuple(
            item.name
            for item in base.protocol.client_tools
            if item.required and item.name not in selected_client_tool_names
        )
        if not missing_required:
            protocol = base.protocol.model_copy(
                update={
                    "client_tools": tuple(
                        item for item in base.protocol.client_tools if item.name in selected_client_tool_names
                    )
                }
            )

    subagents = dict(base.subagents)
    if "subagents" in fields:
        if override.subagents is None:
            subagents.clear()
        else:
            for name, patch in override.subagents.items():
                if patch is None:
                    subagents.pop(name, None)
                    continue
                patch_fields = patch.model_fields_set
                current = subagents.get(name)
                agent_id = _required_patch_value(
                    patch.agent_id,
                    present="agent_id" in patch_fields,
                    inherited=current.agent_id if current is not None else None,
                    path=f"subagents.{name}.agent_id",
                )
                defaults = SubagentSelection(agent_id=agent_id)
                version = patch.version if "version" in patch_fields else (current.version if current else None)
                description = (
                    patch.description
                    if "description" in patch_fields
                    else (current.description if current is not None else None)
                )
                context = _required_patch_value(
                    patch.context,
                    present="context" in patch_fields,
                    inherited=current.context if current is not None else defaults.context,
                    path=f"subagents.{name}.context",
                )
                usage_limits = (
                    patch.usage_limits
                    if "usage_limits" in patch_fields
                    else (current.usage_limits if current is not None else None)
                )
                child_environment = _required_patch_value(
                    patch.environment,
                    present="environment" in patch_fields,
                    inherited=current.environment if current is not None else defaults.environment,
                    path=f"subagents.{name}.environment",
                )
                subagents[name] = SubagentSelection(
                    agent_id=agent_id,
                    version=version,
                    description=description,
                    context=context,
                    usage_limits=usage_limits,
                    environment=child_environment,
                )

    output_spec = base.output_spec
    if "output_spec" in fields:
        output_spec = override.output_spec

    retries = base.retries
    if "retries" in fields:
        if override.retries is None:
            raise invalid_run_override("retries", "null_not_allowed")
        retry_fields = override.retries.model_fields_set
        if retry_fields:
            inherited_retries = base.retries or RetryConfig()
            tools = _required_patch_value(
                override.retries.tools,
                present="tools" in retry_fields,
                inherited=inherited_retries.tools,
                path="retries.tools",
            )
            output = _required_patch_value(
                override.retries.output,
                present="output" in retry_fields,
                inherited=inherited_retries.output,
                path="retries.output",
            )
            retries = RetryConfig(tools=tools, output=output)

    return MergedAgentRunConfig(
        subagent_mode=base.subagent_mode,
        model=model,
        instructions=instructions,
        input_adapter=base.input_adapter,
        plugins=plugins,
        skills=skills,
        connection_tools=connection_tools,
        subagents=subagents,
        client_tools=client_tools,
        output_spec=output_spec,
        retries=retries,
        secret_requirements=base.secret_requirements,
        asset_publication=base.asset_publication,
        memory=override.memory if "memory" in fields else base.memory,
        search=override.search if "search" in fields else base.search,
        permissions=override.permissions if "permissions" in fields else base.permissions,
        reviewer=override.reviewer if "reviewer" in fields else base.reviewer,
        protocol=protocol,
    )


def _replace_list(*, inherited: tuple, value: tuple | None, present: bool, path: str) -> tuple:
    if not present:
        return inherited
    if value is None:
        raise invalid_run_override(path, "null_not_allowed")
    return value


def _required_patch_value(
    value,
    *,
    present: bool,
    inherited,
    path: str,
    default_factory=None,
):
    if present:
        if value is None:
            raise invalid_run_override(path, "null_not_allowed")
        return value
    if inherited is not None:
        return inherited
    if default_factory is not None:
        return default_factory()
    raise invalid_run_override(path, "required")
