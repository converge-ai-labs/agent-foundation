from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_run_override_output_subagents_type_0 import AgentRunOverrideOutputSubagentsType0
    from ..models.client_tool_definition import ClientToolDefinition
    from ..models.connector_connection_tool_selection import ConnectorConnectionToolSelection
    from ..models.mcp_connection_tool_selection import MCPConnectionToolSelection
    from ..models.model_override import ModelOverride
    from ..models.output_spec import OutputSpec
    from ..models.plugin_selection import PluginSelection
    from ..models.retry_override import RetryOverride
    from ..models.search_selection import SearchSelection
    from ..models.skill_selection import SkillSelection


T = TypeVar("T", bound="AgentRunOverrideOutput")


@_attrs_define(repr=False)
class AgentRunOverrideOutput:
    """
    Attributes:
        client_tools (list[ClientToolDefinition] | None | Unset):
        connector_tools (list[ConnectorConnectionToolSelection] | None | Unset):
        instructions (None | str | Unset):
        mcp_tools (list[MCPConnectionToolSelection] | None | Unset):
        model (ModelOverride | None | Unset):
        output_spec (None | OutputSpec | Unset):
        plugins (list[PluginSelection] | None | Unset):
        retries (None | RetryOverride | Unset):
        search (None | SearchSelection | Unset):
        skills (list[SkillSelection] | None | Unset):
        subagents (AgentRunOverrideOutputSubagentsType0 | None | Unset):
    """

    client_tools: list[ClientToolDefinition] | Unset | None = UNSET
    connector_tools: list[ConnectorConnectionToolSelection] | Unset | None = UNSET
    instructions: str | Unset | None = UNSET
    mcp_tools: list[MCPConnectionToolSelection] | Unset | None = UNSET
    model: ModelOverride | Unset | None = UNSET
    output_spec: OutputSpec | Unset | None = UNSET
    plugins: list[PluginSelection] | Unset | None = UNSET
    retries: RetryOverride | Unset | None = UNSET
    search: SearchSelection | Unset | None = UNSET
    skills: list[SkillSelection] | Unset | None = UNSET
    subagents: AgentRunOverrideOutputSubagentsType0 | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_run_override_output_subagents_type_0 import (
            AgentRunOverrideOutputSubagentsType0,
        )
        from ..models.model_override import ModelOverride
        from ..models.output_spec import OutputSpec
        from ..models.retry_override import RetryOverride
        from ..models.search_selection import SearchSelection

        client_tools: list[dict[str, Any]] | Unset | None
        if isinstance(self.client_tools, Unset):
            client_tools = UNSET
        elif isinstance(self.client_tools, list):
            client_tools = []
            for client_tools_type_0_item_data in self.client_tools:
                client_tools_type_0_item = client_tools_type_0_item_data.to_dict()
                client_tools.append(client_tools_type_0_item)

        else:
            client_tools = self.client_tools

        connector_tools: list[dict[str, Any]] | Unset | None
        if isinstance(self.connector_tools, Unset):
            connector_tools = UNSET
        elif isinstance(self.connector_tools, list):
            connector_tools = []
            for connector_tools_type_0_item_data in self.connector_tools:
                connector_tools_type_0_item = connector_tools_type_0_item_data.to_dict()
                connector_tools.append(connector_tools_type_0_item)

        else:
            connector_tools = self.connector_tools

        instructions: str | Unset | None
        if isinstance(self.instructions, Unset):
            instructions = UNSET
        else:
            instructions = self.instructions

        mcp_tools: list[dict[str, Any]] | Unset | None
        if isinstance(self.mcp_tools, Unset):
            mcp_tools = UNSET
        elif isinstance(self.mcp_tools, list):
            mcp_tools = []
            for mcp_tools_type_0_item_data in self.mcp_tools:
                mcp_tools_type_0_item = mcp_tools_type_0_item_data.to_dict()
                mcp_tools.append(mcp_tools_type_0_item)

        else:
            mcp_tools = self.mcp_tools

        model: dict[str, Any] | Unset | None
        if isinstance(self.model, Unset):
            model = UNSET
        elif isinstance(self.model, ModelOverride):
            model = self.model.to_dict()
        else:
            model = self.model

        output_spec: dict[str, Any] | Unset | None
        if isinstance(self.output_spec, Unset):
            output_spec = UNSET
        elif isinstance(self.output_spec, OutputSpec):
            output_spec = self.output_spec.to_dict()
        else:
            output_spec = self.output_spec

        plugins: list[dict[str, Any]] | Unset | None
        if isinstance(self.plugins, Unset):
            plugins = UNSET
        elif isinstance(self.plugins, list):
            plugins = []
            for plugins_type_0_item_data in self.plugins:
                plugins_type_0_item = plugins_type_0_item_data.to_dict()
                plugins.append(plugins_type_0_item)

        else:
            plugins = self.plugins

        retries: dict[str, Any] | Unset | None
        if isinstance(self.retries, Unset):
            retries = UNSET
        elif isinstance(self.retries, RetryOverride):
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

        skills: list[dict[str, Any]] | Unset | None
        if isinstance(self.skills, Unset):
            skills = UNSET
        elif isinstance(self.skills, list):
            skills = []
            for skills_type_0_item_data in self.skills:
                skills_type_0_item = skills_type_0_item_data.to_dict()
                skills.append(skills_type_0_item)

        else:
            skills = self.skills

        subagents: dict[str, Any] | Unset | None
        if isinstance(self.subagents, Unset):
            subagents = UNSET
        elif isinstance(self.subagents, AgentRunOverrideOutputSubagentsType0):
            subagents = self.subagents.to_dict()
        else:
            subagents = self.subagents

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if client_tools is not UNSET:
            field_dict["client_tools"] = client_tools
        if connector_tools is not UNSET:
            field_dict["connector_tools"] = connector_tools
        if instructions is not UNSET:
            field_dict["instructions"] = instructions
        if mcp_tools is not UNSET:
            field_dict["mcp_tools"] = mcp_tools
        if model is not UNSET:
            field_dict["model"] = model
        if output_spec is not UNSET:
            field_dict["output_spec"] = output_spec
        if plugins is not UNSET:
            field_dict["plugins"] = plugins
        if retries is not UNSET:
            field_dict["retries"] = retries
        if search is not UNSET:
            field_dict["search"] = search
        if skills is not UNSET:
            field_dict["skills"] = skills
        if subagents is not UNSET:
            field_dict["subagents"] = subagents

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_run_override_output_subagents_type_0 import (
            AgentRunOverrideOutputSubagentsType0,
        )
        from ..models.client_tool_definition import ClientToolDefinition
        from ..models.connector_connection_tool_selection import ConnectorConnectionToolSelection
        from ..models.mcp_connection_tool_selection import MCPConnectionToolSelection
        from ..models.model_override import ModelOverride
        from ..models.output_spec import OutputSpec
        from ..models.plugin_selection import PluginSelection
        from ..models.retry_override import RetryOverride
        from ..models.search_selection import SearchSelection
        from ..models.skill_selection import SkillSelection

        d = dict(src_dict)

        def _parse_client_tools(data: object) -> list[ClientToolDefinition] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                client_tools_type_0 = []
                _client_tools_type_0 = data
                for client_tools_type_0_item_data in _client_tools_type_0:
                    client_tools_type_0_item = ClientToolDefinition.from_dict(client_tools_type_0_item_data)

                    client_tools_type_0.append(client_tools_type_0_item)

                return client_tools_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ClientToolDefinition] | Unset | None, data)

        client_tools = _parse_client_tools(d.pop("client_tools", UNSET))

        def _parse_connector_tools(data: object) -> list[ConnectorConnectionToolSelection] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                connector_tools_type_0 = []
                _connector_tools_type_0 = data
                for connector_tools_type_0_item_data in _connector_tools_type_0:
                    connector_tools_type_0_item = ConnectorConnectionToolSelection.from_dict(
                        connector_tools_type_0_item_data
                    )

                    connector_tools_type_0.append(connector_tools_type_0_item)

                return connector_tools_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ConnectorConnectionToolSelection] | Unset | None, data)

        connector_tools = _parse_connector_tools(d.pop("connector_tools", UNSET))

        def _parse_instructions(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        instructions = _parse_instructions(d.pop("instructions", UNSET))

        def _parse_mcp_tools(data: object) -> list[MCPConnectionToolSelection] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                mcp_tools_type_0 = []
                _mcp_tools_type_0 = data
                for mcp_tools_type_0_item_data in _mcp_tools_type_0:
                    mcp_tools_type_0_item = MCPConnectionToolSelection.from_dict(mcp_tools_type_0_item_data)

                    mcp_tools_type_0.append(mcp_tools_type_0_item)

                return mcp_tools_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[MCPConnectionToolSelection] | Unset | None, data)

        mcp_tools = _parse_mcp_tools(d.pop("mcp_tools", UNSET))

        def _parse_model(data: object) -> ModelOverride | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                model_type_0 = ModelOverride.from_dict(data)

                return model_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ModelOverride | Unset | None, data)

        model = _parse_model(d.pop("model", UNSET))

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

        def _parse_plugins(data: object) -> list[PluginSelection] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                plugins_type_0 = []
                _plugins_type_0 = data
                for plugins_type_0_item_data in _plugins_type_0:
                    plugins_type_0_item = PluginSelection.from_dict(plugins_type_0_item_data)

                    plugins_type_0.append(plugins_type_0_item)

                return plugins_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[PluginSelection] | Unset | None, data)

        plugins = _parse_plugins(d.pop("plugins", UNSET))

        def _parse_retries(data: object) -> RetryOverride | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                retries_type_0 = RetryOverride.from_dict(data)

                return retries_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(RetryOverride | Unset | None, data)

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

        def _parse_skills(data: object) -> list[SkillSelection] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                skills_type_0 = []
                _skills_type_0 = data
                for skills_type_0_item_data in _skills_type_0:
                    skills_type_0_item = SkillSelection.from_dict(skills_type_0_item_data)

                    skills_type_0.append(skills_type_0_item)

                return skills_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[SkillSelection] | Unset | None, data)

        skills = _parse_skills(d.pop("skills", UNSET))

        def _parse_subagents(data: object) -> AgentRunOverrideOutputSubagentsType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                subagents_type_0 = AgentRunOverrideOutputSubagentsType0.from_dict(data)

                return subagents_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentRunOverrideOutputSubagentsType0 | Unset | None, data)

        subagents = _parse_subagents(d.pop("subagents", UNSET))

        agent_run_override_output = cls(
            client_tools=client_tools,
            connector_tools=connector_tools,
            instructions=instructions,
            mcp_tools=mcp_tools,
            model=model,
            output_spec=output_spec,
            plugins=plugins,
            retries=retries,
            search=search,
            skills=skills,
            subagents=subagents,
        )

        return agent_run_override_output
