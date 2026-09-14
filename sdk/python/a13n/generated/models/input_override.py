from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connection_tool_selection import ConnectionToolSelection
    from ..models.model_override import ModelOverride
    from ..models.skill_selection import SkillSelection


T = TypeVar("T", bound="InputOverride")


@_attrs_define(repr=False)
class InputOverride:
    """
    Attributes:
        connection_tools (list[ConnectionToolSelection] | None | Unset):
        model (ModelOverride | None | Unset):
        skills (list[SkillSelection] | None | Unset):
    """

    connection_tools: list[ConnectionToolSelection] | Unset | None = UNSET
    model: ModelOverride | Unset | None = UNSET
    skills: list[SkillSelection] | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.model_override import ModelOverride

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
        if connection_tools is not UNSET:
            field_dict["connection_tools"] = connection_tools
        if model is not UNSET:
            field_dict["model"] = model
        if skills is not UNSET:
            field_dict["skills"] = skills

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connection_tool_selection import ConnectionToolSelection
        from ..models.model_override import ModelOverride
        from ..models.skill_selection import SkillSelection

        d = dict(src_dict)

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
            connection_tools=connection_tools,
            model=model,
            skills=skills,
        )

        return input_override
