from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.agent_config_input_subagent_mode import AgentConfigInputSubagentMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_config_input_subagents import AgentConfigInputSubagents
    from ..models.agent_model import AgentModel
    from ..models.asset_publication_config import AssetPublicationConfig
    from ..models.client_tool_definition import ClientToolDefinition
    from ..models.connector_connection_tool_selection import ConnectorConnectionToolSelection
    from ..models.input_adapter_config import InputAdapterConfig
    from ..models.mcp_connection_tool_selection import MCPConnectionToolSelection
    from ..models.output_spec import OutputSpec
    from ..models.plugin_selection import PluginSelection
    from ..models.protocol_config import ProtocolConfig
    from ..models.retry_config import RetryConfig
    from ..models.search_selection import SearchSelection
    from ..models.secret_requirement import SecretRequirement
    from ..models.skill_selection import SkillSelection


T = TypeVar("T", bound="AgentConfigInput")


@_attrs_define(repr=False)
class AgentConfigInput:
    """
    Attributes:
        input_adapter (InputAdapterConfig):
        model (AgentModel):
        protocol (ProtocolConfig):
        asset_publication (AssetPublicationConfig | None | Unset):
        client_tools (list[ClientToolDefinition] | Unset):
        connector_tools (list[ConnectorConnectionToolSelection] | Unset):
        instructions (str | Unset):
        mcp_tools (list[MCPConnectionToolSelection] | Unset):
        output_spec (None | OutputSpec | Unset):
        plugins (list[PluginSelection] | Unset):
        retries (None | RetryConfig | Unset):
        search (None | SearchSelection | Unset):
        secret_requirements (list[SecretRequirement] | Unset):
        skills (list[SkillSelection] | Unset):
        subagent_mode (AgentConfigInputSubagentMode | Unset):
        subagents (AgentConfigInputSubagents | Unset):
    """

    input_adapter: InputAdapterConfig
    model: AgentModel
    protocol: ProtocolConfig
    asset_publication: AssetPublicationConfig | Unset | None = UNSET
    client_tools: list[ClientToolDefinition] | Unset = UNSET
    connector_tools: list[ConnectorConnectionToolSelection] | Unset = UNSET
    instructions: str | Unset = UNSET
    mcp_tools: list[MCPConnectionToolSelection] | Unset = UNSET
    output_spec: OutputSpec | Unset | None = UNSET
    plugins: list[PluginSelection] | Unset = UNSET
    retries: RetryConfig | Unset | None = UNSET
    search: SearchSelection | Unset | None = UNSET
    secret_requirements: list[SecretRequirement] | Unset = UNSET
    skills: list[SkillSelection] | Unset = UNSET
    subagent_mode: AgentConfigInputSubagentMode | Unset = UNSET
    subagents: AgentConfigInputSubagents | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.asset_publication_config import AssetPublicationConfig
        from ..models.output_spec import OutputSpec
        from ..models.retry_config import RetryConfig
        from ..models.search_selection import SearchSelection

        input_adapter = self.input_adapter.to_dict()

        model = self.model.to_dict()

        protocol = self.protocol.to_dict()

        asset_publication: dict[str, Any] | Unset | None
        if isinstance(self.asset_publication, Unset):
            asset_publication = UNSET
        elif isinstance(self.asset_publication, AssetPublicationConfig):
            asset_publication = self.asset_publication.to_dict()
        else:
            asset_publication = self.asset_publication

        client_tools: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.client_tools, Unset):
            client_tools = []
            for client_tools_item_data in self.client_tools:
                client_tools_item = client_tools_item_data.to_dict()
                client_tools.append(client_tools_item)

        connector_tools: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.connector_tools, Unset):
            connector_tools = []
            for connector_tools_item_data in self.connector_tools:
                connector_tools_item = connector_tools_item_data.to_dict()
                connector_tools.append(connector_tools_item)

        instructions = self.instructions

        mcp_tools: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.mcp_tools, Unset):
            mcp_tools = []
            for mcp_tools_item_data in self.mcp_tools:
                mcp_tools_item = mcp_tools_item_data.to_dict()
                mcp_tools.append(mcp_tools_item)

        output_spec: dict[str, Any] | Unset | None
        if isinstance(self.output_spec, Unset):
            output_spec = UNSET
        elif isinstance(self.output_spec, OutputSpec):
            output_spec = self.output_spec.to_dict()
        else:
            output_spec = self.output_spec

        plugins: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.plugins, Unset):
            plugins = []
            for plugins_item_data in self.plugins:
                plugins_item = plugins_item_data.to_dict()
                plugins.append(plugins_item)

        retries: dict[str, Any] | Unset | None
        if isinstance(self.retries, Unset):
            retries = UNSET
        elif isinstance(self.retries, RetryConfig):
            retries = self.retries.to_dict()
        else:
            retries = self.retries

        search: dict[str, Any] | Unset | None
        if isinstance(self.search, Unset):
            search = UNSET
        elif isinstance(self.search, SearchSelection):
            search = self.search.to_dict()
        else:
            search = self.search

        secret_requirements: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.secret_requirements, Unset):
            secret_requirements = []
            for secret_requirements_item_data in self.secret_requirements:
                secret_requirements_item = secret_requirements_item_data.to_dict()
                secret_requirements.append(secret_requirements_item)

        skills: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.skills, Unset):
            skills = []
            for skills_item_data in self.skills:
                skills_item = skills_item_data.to_dict()
                skills.append(skills_item)

        subagent_mode: str | Unset = UNSET
        if not isinstance(self.subagent_mode, Unset):
            subagent_mode = self.subagent_mode.value

        subagents: dict[str, Any] | Unset = UNSET
        if not isinstance(self.subagents, Unset):
            subagents = self.subagents.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "input_adapter": input_adapter,
                "model": model,
                "protocol": protocol,
            }
        )
        if asset_publication is not UNSET:
            field_dict["asset_publication"] = asset_publication
        if client_tools is not UNSET:
            field_dict["client_tools"] = client_tools
        if connector_tools is not UNSET:
            field_dict["connector_tools"] = connector_tools
        if instructions is not UNSET:
            field_dict["instructions"] = instructions
        if mcp_tools is not UNSET:
            field_dict["mcp_tools"] = mcp_tools
        if output_spec is not UNSET:
            field_dict["output_spec"] = output_spec
        if plugins is not UNSET:
            field_dict["plugins"] = plugins
        if retries is not UNSET:
            field_dict["retries"] = retries
        if search is not UNSET:
            field_dict["search"] = search
        if secret_requirements is not UNSET:
            field_dict["secret_requirements"] = secret_requirements
        if skills is not UNSET:
            field_dict["skills"] = skills
        if subagent_mode is not UNSET:
            field_dict["subagent_mode"] = subagent_mode
        if subagents is not UNSET:
            field_dict["subagents"] = subagents

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_config_input_subagents import AgentConfigInputSubagents
        from ..models.agent_model import AgentModel
        from ..models.asset_publication_config import AssetPublicationConfig
        from ..models.client_tool_definition import ClientToolDefinition
        from ..models.connector_connection_tool_selection import ConnectorConnectionToolSelection
        from ..models.input_adapter_config import InputAdapterConfig
        from ..models.mcp_connection_tool_selection import MCPConnectionToolSelection
        from ..models.output_spec import OutputSpec
        from ..models.plugin_selection import PluginSelection
        from ..models.protocol_config import ProtocolConfig
        from ..models.retry_config import RetryConfig
        from ..models.search_selection import SearchSelection
        from ..models.secret_requirement import SecretRequirement
        from ..models.skill_selection import SkillSelection

        d = dict(src_dict)
        input_adapter = InputAdapterConfig.from_dict(d.pop("input_adapter"))

        model = AgentModel.from_dict(d.pop("model"))

        protocol = ProtocolConfig.from_dict(d.pop("protocol"))

        def _parse_asset_publication(data: object) -> AssetPublicationConfig | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                asset_publication_type_0 = AssetPublicationConfig.from_dict(data)

                return asset_publication_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AssetPublicationConfig | Unset | None, data)

        asset_publication = _parse_asset_publication(d.pop("asset_publication", UNSET))

        _client_tools = d.pop("client_tools", UNSET)
        client_tools: list[ClientToolDefinition] | Unset = UNSET
        if _client_tools is not UNSET:
            client_tools = []
            for client_tools_item_data in _client_tools:
                client_tools_item = ClientToolDefinition.from_dict(client_tools_item_data)

                client_tools.append(client_tools_item)

        _connector_tools = d.pop("connector_tools", UNSET)
        connector_tools: list[ConnectorConnectionToolSelection] | Unset = UNSET
        if _connector_tools is not UNSET:
            connector_tools = []
            for connector_tools_item_data in _connector_tools:
                connector_tools_item = ConnectorConnectionToolSelection.from_dict(connector_tools_item_data)

                connector_tools.append(connector_tools_item)

        instructions = d.pop("instructions", UNSET)

        _mcp_tools = d.pop("mcp_tools", UNSET)
        mcp_tools: list[MCPConnectionToolSelection] | Unset = UNSET
        if _mcp_tools is not UNSET:
            mcp_tools = []
            for mcp_tools_item_data in _mcp_tools:
                mcp_tools_item = MCPConnectionToolSelection.from_dict(mcp_tools_item_data)

                mcp_tools.append(mcp_tools_item)

        def _parse_output_spec(data: object) -> OutputSpec | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_spec_type_0 = OutputSpec.from_dict(data)

                return output_spec_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(OutputSpec | Unset | None, data)

        output_spec = _parse_output_spec(d.pop("output_spec", UNSET))

        _plugins = d.pop("plugins", UNSET)
        plugins: list[PluginSelection] | Unset = UNSET
        if _plugins is not UNSET:
            plugins = []
            for plugins_item_data in _plugins:
                plugins_item = PluginSelection.from_dict(plugins_item_data)

                plugins.append(plugins_item)

        def _parse_retries(data: object) -> RetryConfig | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                retries_type_0 = RetryConfig.from_dict(data)

                return retries_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(RetryConfig | Unset | None, data)

        retries = _parse_retries(d.pop("retries", UNSET))

        def _parse_search(data: object) -> SearchSelection | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                search_type_0 = SearchSelection.from_dict(data)

                return search_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(SearchSelection | Unset | None, data)

        search = _parse_search(d.pop("search", UNSET))

        _secret_requirements = d.pop("secret_requirements", UNSET)
        secret_requirements: list[SecretRequirement] | Unset = UNSET
        if _secret_requirements is not UNSET:
            secret_requirements = []
            for secret_requirements_item_data in _secret_requirements:
                secret_requirements_item = SecretRequirement.from_dict(secret_requirements_item_data)

                secret_requirements.append(secret_requirements_item)

        _skills = d.pop("skills", UNSET)
        skills: list[SkillSelection] | Unset = UNSET
        if _skills is not UNSET:
            skills = []
            for skills_item_data in _skills:
                skills_item = SkillSelection.from_dict(skills_item_data)

                skills.append(skills_item)

        _subagent_mode = d.pop("subagent_mode", UNSET)
        subagent_mode: AgentConfigInputSubagentMode | Unset
        if isinstance(_subagent_mode, Unset):
            subagent_mode = UNSET
        else:
            subagent_mode = AgentConfigInputSubagentMode(_subagent_mode)

        _subagents = d.pop("subagents", UNSET)
        subagents: AgentConfigInputSubagents | Unset
        if isinstance(_subagents, Unset):
            subagents = UNSET
        else:
            subagents = AgentConfigInputSubagents.from_dict(_subagents)

        agent_config_input = cls(
            input_adapter=input_adapter,
            model=model,
            protocol=protocol,
            asset_publication=asset_publication,
            client_tools=client_tools,
            connector_tools=connector_tools,
            instructions=instructions,
            mcp_tools=mcp_tools,
            output_spec=output_spec,
            plugins=plugins,
            retries=retries,
            search=search,
            secret_requirements=secret_requirements,
            skills=skills,
            subagent_mode=subagent_mode,
            subagents=subagents,
        )

        return agent_config_input
