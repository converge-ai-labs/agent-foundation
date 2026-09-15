from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_reviewer import AgentReviewer
    from ..models.agent_run_override_output_subagents_type_0 import AgentRunOverrideOutputSubagentsType0
    from ..models.client_tool_definition import ClientToolDefinition
    from ..models.connection_tool_selection import ConnectionToolSelection
    from ..models.memory_selection import MemorySelection
    from ..models.model_override import ModelOverride
    from ..models.output_spec import OutputSpec
    from ..models.plugin_selection import PluginSelection
    from ..models.retry_override import RetryOverride
    from ..models.search_selection import SearchSelection
    from ..models.skill_selection import SkillSelection
    from ..models.tool_permissions import ToolPermissions


T = TypeVar("T", bound="AgentRunOverrideOutput")


@_attrs_define(repr=False)
class AgentRunOverrideOutput:
    """
    Attributes:
        client_tools (list[ClientToolDefinition] | None | Unset):
        connection_tools (list[ConnectionToolSelection] | None | Unset):
        instructions (None | str | Unset):
        memory (MemorySelection | None | Unset):
        model (ModelOverride | None | Unset):
        output_spec (None | OutputSpec | Unset):
        permissions (None | ToolPermissions | Unset):
        plugins (list[PluginSelection] | None | Unset):
        retries (None | RetryOverride | Unset):
        reviewer (AgentReviewer | None | Unset):
        search (None | SearchSelection | Unset):
        skills (list[SkillSelection] | None | Unset):
        subagents (AgentRunOverrideOutputSubagentsType0 | None | Unset):
    """

    client_tools: list[ClientToolDefinition] | Unset | None = UNSET
    connection_tools: list[ConnectionToolSelection] | Unset | None = UNSET
    instructions: str | Unset | None = UNSET
    memory: MemorySelection | Unset | None = UNSET
    model: ModelOverride | Unset | None = UNSET
    output_spec: OutputSpec | Unset | None = UNSET
    permissions: ToolPermissions | Unset | None = UNSET
    plugins: list[PluginSelection] | Unset | None = UNSET
    retries: RetryOverride | Unset | None = UNSET
    reviewer: AgentReviewer | Unset | None = UNSET
    search: SearchSelection | Unset | None = UNSET
    skills: list[SkillSelection] | Unset | None = UNSET
    subagents: AgentRunOverrideOutputSubagentsType0 | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_reviewer import AgentReviewer
        from ..models.agent_run_override_output_subagents_type_0 import (
            AgentRunOverrideOutputSubagentsType0,
        )
        from ..models.memory_selection import MemorySelection
        from ..models.model_override import ModelOverride
        from ..models.output_spec import OutputSpec
        from ..models.retry_override import RetryOverride
        from ..models.search_selection import SearchSelection
        from ..models.tool_permissions import ToolPermissions

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

        connection_tools: list[dict[str, Any]] | Unset | None
        if isinstance(self.connection_tools, Unset):
            connection_tools = UNSET
        elif isinstance(self.connection_tools, list):
            connection_tools = []
            for connection_tools_type_0_item_data in self.connection_tools:
                connection_tools_type_0_item = connection_tools_type_0_item_data.to_dict()
                connection_tools.append(connection_tools_type_0_item)

        else:
            connection_tools = self.connection_tools

        instructions: str | Unset | None
        if isinstance(self.instructions, Unset):
            instructions = UNSET
        else:
            instructions = self.instructions

        memory: dict[str, Any] | Unset | None
        if isinstance(self.memory, Unset):
            memory = UNSET
        elif isinstance(self.memory, MemorySelection):
            memory = self.memory.to_dict()
        else:
            memory = self.memory

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

        permissions: dict[str, Any] | Unset | None
        if isinstance(self.permissions, Unset):
            permissions = UNSET
        elif isinstance(self.permissions, ToolPermissions):
            permissions = self.permissions.to_dict()
        else:
            permissions = self.permissions

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
        if connection_tools is not UNSET:
            field_dict["connection_tools"] = connection_tools
        if instructions is not UNSET:
            field_dict["instructions"] = instructions
        if memory is not UNSET:
            field_dict["memory"] = memory
        if model is not UNSET:
            field_dict["model"] = model
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
        if skills is not UNSET:
            field_dict["skills"] = skills
        if subagents is not UNSET:
            field_dict["subagents"] = subagents

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_reviewer import AgentReviewer
        from ..models.agent_run_override_output_subagents_type_0 import (
            AgentRunOverrideOutputSubagentsType0,
        )
        from ..models.client_tool_definition import ClientToolDefinition
        from ..models.connection_tool_selection import ConnectionToolSelection
        from ..models.memory_selection import MemorySelection
        from ..models.model_override import ModelOverride
        from ..models.output_spec import OutputSpec
        from ..models.plugin_selection import PluginSelection
        from ..models.retry_override import RetryOverride
        from ..models.search_selection import SearchSelection
        from ..models.skill_selection import SkillSelection
        from ..models.tool_permissions import ToolPermissions

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

        def _parse_connection_tools(data: object) -> list[ConnectionToolSelection] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                connection_tools_type_0 = []
                _connection_tools_type_0 = data
                for connection_tools_type_0_item_data in _connection_tools_type_0:
                    connection_tools_type_0_item = ConnectionToolSelection.from_dict(connection_tools_type_0_item_data)

                    connection_tools_type_0.append(connection_tools_type_0_item)

                return connection_tools_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ConnectionToolSelection] | Unset | None, data)

        connection_tools = _parse_connection_tools(d.pop("connection_tools", UNSET))

        def _parse_instructions(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        instructions = _parse_instructions(d.pop("instructions", UNSET))

        def _parse_memory(data: object) -> MemorySelection | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                memory_type_0 = MemorySelection.from_dict(data)

                return memory_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MemorySelection | Unset | None, data)

        memory = _parse_memory(d.pop("memory", UNSET))

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
            connection_tools=connection_tools,
            instructions=instructions,
            memory=memory,
            model=model,
            output_spec=output_spec,
            permissions=permissions,
            plugins=plugins,
            retries=retries,
            reviewer=reviewer,
            search=search,
            skills=skills,
            subagents=subagents,
        )

        return agent_run_override_output
