from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connector_connection_tool_selection import ConnectorConnectionToolSelection
    from ..models.mcp_connection_tool_selection import MCPConnectionToolSelection
    from ..models.model_override import ModelOverride
    from ..models.skill_selection import SkillSelection


T = TypeVar("T", bound="InputOverride")


@_attrs_define(repr=False)
class InputOverride:
    """
    Attributes:
        connector_tools (list[ConnectorConnectionToolSelection] | None | Unset):
        mcp_tools (list[MCPConnectionToolSelection] | None | Unset):
        model (ModelOverride | None | Unset):
        skills (list[SkillSelection] | None | Unset):
    """

    connector_tools: list[ConnectorConnectionToolSelection] | Unset | None = UNSET
    mcp_tools: list[MCPConnectionToolSelection] | Unset | None = UNSET
    model: ModelOverride | Unset | None = UNSET
    skills: list[SkillSelection] | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.model_override import ModelOverride

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

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if connector_tools is not UNSET:
            field_dict["connector_tools"] = connector_tools
        if mcp_tools is not UNSET:
            field_dict["mcp_tools"] = mcp_tools
        if model is not UNSET:
            field_dict["model"] = model
        if skills is not UNSET:
            field_dict["skills"] = skills

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_connection_tool_selection import ConnectorConnectionToolSelection
        from ..models.mcp_connection_tool_selection import MCPConnectionToolSelection
        from ..models.model_override import ModelOverride
        from ..models.skill_selection import SkillSelection

        d = dict(src_dict)

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

        input_override = cls(
            connector_tools=connector_tools,
            mcp_tools=mcp_tools,
            model=model,
            skills=skills,
        )

        return input_override
