"""Run-time Agent selection and effective-config construction."""

from __future__ import annotations

from pydantic import Field, model_validator

from .domain import (
    AgentConfig,
    AgentModel,
    AgentRunOverride,
    AssetPublicationConfig,
    BoundedKey,
    ClientToolDefinition,
    ConnectorConnectionToolSelection,
    EnvironmentOverride,
    InputAdapterConfig,
    MCPConnectionToolSelection,
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

    model: AgentModel
    instructions: str
    input_adapter: InputAdapterConfig
    plugins: tuple[PluginSelection, ...] = Field(default=(), max_length=128)
    skills: tuple[SkillSelection, ...] = Field(default=(), max_length=512)
    connector_tools: tuple[ConnectorConnectionToolSelection, ...] = Field(default=(), max_length=128)
    mcp_tools: tuple[MCPConnectionToolSelection, ...] = Field(default=(), max_length=128)
    environment: EnvironmentOverride | None = None
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
            ("connector_tools", tuple(item.connector_connection_id for item in self.connector_tools)),
            ("mcp_tools", tuple(item.mcp_connection_id for item in self.mcp_tools)),
        ):
            if len(values) != len(set(values)):
                raise invalid_run_override(path, "duplicate_selection")
        return self


class AgentRunSensitiveValues(StrictModel):
    """Ephemeral sensitive leaves extracted before effective config is persisted."""


class MergedAgentRun(StrictModel):
    """Merged invocation input before managed resources are resolved and frozen."""

    config: MergedAgentRunConfig
    sensitive_values: AgentRunSensitiveValues = Field(repr=False)


def merge_agent_run_override(
    base: AgentConfig,
    override: AgentRunOverride | None,
) -> MergedAgentRun:
    """Apply the finite typed override contract without resolving managed resources."""

    if override is None:
        return MergedAgentRun(
            config=MergedAgentRunConfig.model_validate(base.model_dump(mode="python", by_alias=True)),
            sensitive_values=AgentRunSensitiveValues(),
        )

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
    connector_tools = _replace_list(
        inherited=base.connector_tools,
        value=override.connector_tools,
        present="connector_tools" in fields,
        path="connector_tools",
    )
    mcp_tools = _replace_list(
        inherited=base.mcp_tools,
        value=override.mcp_tools,
        present="mcp_tools" in fields,
        path="mcp_tools",
    )
    client_tools = _replace_list(
        inherited=base.client_tools,
        value=override.client_tools,
        present="client_tools" in fields,
        path="client_tools",
    )

    environment: EnvironmentOverride | None = base.environment
    if "environment" in fields:
        environment = override.environment

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

    return MergedAgentRun(
        config=MergedAgentRunConfig(
            model=model,
            instructions=instructions,
            input_adapter=base.input_adapter,
            plugins=plugins,
            skills=skills,
            connector_tools=connector_tools,
            mcp_tools=mcp_tools,
            environment=environment,
            subagents=subagents,
            client_tools=client_tools,
            output_spec=output_spec,
            retries=retries,
            secret_requirements=base.secret_requirements,
            asset_publication=base.asset_publication,
            protocol=base.protocol,
        ),
        sensitive_values=AgentRunSensitiveValues(),
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
