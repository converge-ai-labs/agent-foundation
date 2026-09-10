from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="UpdateAgentRequest")


@_attrs_define(repr=False)
class UpdateAgentRequest:
    """
    Attributes:
        default_environment_template_id (None | str | Unset):
        description (None | str | Unset):
        key (None | str | Unset):
        name (None | str | Unset):
    """

    default_environment_template_id: str | Unset | None = UNSET
    description: str | Unset | None = UNSET
    key: str | Unset | None = UNSET
    name: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        default_environment_template_id: str | Unset | None
        if isinstance(self.default_environment_template_id, Unset):
            default_environment_template_id = UNSET
        else:
            default_environment_template_id = self.default_environment_template_id

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        key: str | Unset | None
        if isinstance(self.key, Unset):
            key = UNSET
        else:
            key = self.key

        name: str | Unset | None
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if default_environment_template_id is not UNSET:
            field_dict["default_environment_template_id"] = default_environment_template_id
        if description is not UNSET:
            field_dict["description"] = description
        if key is not UNSET:
            field_dict["key"] = key
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_default_environment_template_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        default_environment_template_id = _parse_default_environment_template_id(
            d.pop("default_environment_template_id", UNSET)
        )

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_key(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        key = _parse_key(d.pop("key", UNSET))

        def _parse_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        name = _parse_name(d.pop("name", UNSET))

        update_agent_request = cls(
            default_environment_template_id=default_environment_template_id,
            description=description,
            key=key,
            name=name,
        )

        return update_agent_request
