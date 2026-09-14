from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.agent_config_output_subagent_mode import AgentConfigOutputSubagentMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_config_output_subagents import AgentConfigOutputSubagents
    from ..models.agent_model import AgentModel
    from ..models.agent_reviewer import AgentReviewer
    from ..models.asset_publication_config import AssetPublicationConfig
    from ..models.client_tool_definition import ClientToolDefinition
    from ..models.connection_tool_selection import ConnectionToolSelection
    from ..models.input_adapter_config import InputAdapterConfig
    from ..models.output_spec import OutputSpec
    from ..models.plugin_selection import PluginSelection
    from ..models.protocol_config import ProtocolConfig
    from ..models.retry_config import RetryConfig
    from ..models.search_selection import SearchSelection
    from ..models.secret_requirement import SecretRequirement
    from ..models.skill_selection import SkillSelection
    from ..models.tool_permissions import ToolPermissions


T = TypeVar("T", bound="AgentConfigOutput")


@_attrs_define(repr=False)
class AgentConfigOutput:
    """
    Attributes:
        input_adapter (InputAdapterConfig):
        model (AgentModel):
        protocol (ProtocolConfig):
        asset_publication (AssetPublicationConfig | None | Unset):
        client_tools (list[ClientToolDefinition] | Unset):
        connection_tools (list[ConnectionToolSelection] | Unset):
        instructions (str | Unset):
        output_spec (None | OutputSpec | Unset):
        permissions (None | ToolPermissions | Unset):
        plugins (list[PluginSelection] | Unset):
        retries (None | RetryConfig | Unset):
        reviewer (AgentReviewer | None | Unset):
        search (None | SearchSelection | Unset):
        secret_requirements (list[SecretRequirement] | Unset):
        skills (list[SkillSelection] | Unset):
        subagent_mode (AgentConfigOutputSubagentMode | Unset):
        subagents (AgentConfigOutputSubagents | Unset):
    """

    input_adapter: InputAdapterConfig
    model: AgentModel
    protocol: ProtocolConfig
    asset_publication: AssetPublicationConfig | Unset | None = UNSET
    client_tools: list[ClientToolDefinition] | Unset = UNSET
    connection_tools: list[ConnectionToolSelection] | Unset = UNSET
    instructions: str | Unset = UNSET
    output_spec: OutputSpec | Unset | None = UNSET
    permissions: ToolPermissions | Unset | None = UNSET
    plugins: list[PluginSelection] | Unset = UNSET
    retries: RetryConfig | Unset | None = UNSET
    reviewer: AgentReviewer | Unset | None = UNSET
    search: SearchSelection | Unset | None = UNSET
    secret_requirements: list[SecretRequirement] | Unset = UNSET
    skills: list[SkillSelection] | Unset = UNSET
    subagent_mode: AgentConfigOutputSubagentMode | Unset = UNSET
    subagents: AgentConfigOutputSubagents | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_reviewer import AgentReviewer
        from ..models.asset_publication_config import AssetPublicationConfig
        from ..models.output_spec import OutputSpec
        from ..models.retry_config import RetryConfig
        from ..models.search_selection import SearchSelection
        from ..models.tool_permissions import ToolPermissions

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

        connection_tools: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.connection_tools, Unset):
            connection_tools = []
            for connection_tools_item_data in self.connection_tools:
                connection_tools_item = connection_tools_item_data.to_dict()
                connection_tools.append(connection_tools_item)

        instructions = self.instructions

        output_spec: dict[str, Any] | Unset | None
        if isinstance(self.output_spec, Unset):
            output_spec = UNSET
        elif isinstance(self.output_spec, OutputSpec):
            output_spec = self.output_spec.to_dict()
        else:
            output_spec = self.output_spec

        permissions: dict[str, Any] | Unset | None
        if isinstance(self.permissions, Unset):
            permissions = UNSET
        elif isinstance(self.permissions, ToolPermissions):
            permissions = self.permissions.to_dict()
        else:
            permissions = self.permissions

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

        reviewer: dict[str, Any] | Unset | None
        if isinstance(self.reviewer, Unset):
            reviewer = UNSET
        elif isinstance(self.reviewer, AgentReviewer):
            reviewer = self.reviewer.to_dict()
        else:
            reviewer = self.reviewer

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
        if connection_tools is not UNSET:
            field_dict["connection_tools"] = connection_tools
        if instructions is not UNSET:
            field_dict["instructions"] = instructions
        if output_spec is not UNSET:
            field_dict["output_spec"] = output_spec
        if permissions is not UNSET:
            field_dict["permissions"] = permissions
        if plugins is not UNSET:
            field_dict["plugins"] = plugins
        if retries is not UNSET:
            field_dict["retries"] = retries
        if reviewer is not UNSET:
            field_dict["reviewer"] = reviewer
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
        from ..models.agent_config_output_subagents import AgentConfigOutputSubagents
        from ..models.agent_model import AgentModel
        from ..models.agent_reviewer import AgentReviewer
        from ..models.asset_publication_config import AssetPublicationConfig
        from ..models.client_tool_definition import ClientToolDefinition
        from ..models.connection_tool_selection import ConnectionToolSelection
        from ..models.input_adapter_config import InputAdapterConfig
        from ..models.output_spec import OutputSpec
        from ..models.plugin_selection import PluginSelection
        from ..models.protocol_config import ProtocolConfig
        from ..models.retry_config import RetryConfig
        from ..models.search_selection import SearchSelection
        from ..models.secret_requirement import SecretRequirement
        from ..models.skill_selection import SkillSelection
        from ..models.tool_permissions import ToolPermissions

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

        _connection_tools = d.pop("connection_tools", UNSET)
        connection_tools: list[ConnectionToolSelection] | Unset = UNSET
        if _connection_tools is not UNSET:
            connection_tools = []
            for connection_tools_item_data in _connection_tools:
                connection_tools_item = ConnectionToolSelection.from_dict(connection_tools_item_data)

                connection_tools.append(connection_tools_item)

        instructions = d.pop("instructions", UNSET)

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

        def _parse_permissions(data: object) -> ToolPermissions | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                permissions_type_0 = ToolPermissions.from_dict(data)

                return permissions_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ToolPermissions | Unset | None, data)

        permissions = _parse_permissions(d.pop("permissions", UNSET))

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

        def _parse_reviewer(data: object) -> AgentReviewer | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                reviewer_type_0 = AgentReviewer.from_dict(data)

                return reviewer_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentReviewer | Unset | None, data)

        reviewer = _parse_reviewer(d.pop("reviewer", UNSET))

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
        subagent_mode: AgentConfigOutputSubagentMode | Unset
        if isinstance(_subagent_mode, Unset):
            subagent_mode = UNSET
        else:
            subagent_mode = AgentConfigOutputSubagentMode(_subagent_mode)

        _subagents = d.pop("subagents", UNSET)
        subagents: AgentConfigOutputSubagents | Unset
        if isinstance(_subagents, Unset):
            subagents = UNSET
        else:
            subagents = AgentConfigOutputSubagents.from_dict(_subagents)

        agent_config_output = cls(
            input_adapter=input_adapter,
            model=model,
            protocol=protocol,
            asset_publication=asset_publication,
            client_tools=client_tools,
            connection_tools=connection_tools,
            instructions=instructions,
            output_spec=output_spec,
            permissions=permissions,
            plugins=plugins,
            retries=retries,
            reviewer=reviewer,
            search=search,
            secret_requirements=secret_requirements,
            skills=skills,
            subagent_mode=subagent_mode,
            subagents=subagents,
        )

        return agent_config_output
