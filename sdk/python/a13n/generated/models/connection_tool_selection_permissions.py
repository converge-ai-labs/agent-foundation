from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.tool_permission_mode import ToolPermissionMode

T = TypeVar("T", bound="ConnectionToolSelectionPermissions")


@_attrs_define(repr=False)
class ConnectionToolSelectionPermissions:
    additional_properties: dict[str, Literal["auto"] | ToolPermissionMode] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            if isinstance(prop, ToolPermissionMode):
                field_dict[prop_name] = prop.value
            else:
                field_dict[prop_name] = prop

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        connection_tool_selection_permissions = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():

            def _parse_additional_property(data: object) -> Literal["auto"] | ToolPermissionMode:
                try:
                    if not isinstance(data, str):
                        raise TypeError()
                    componentsschemas_tool_permission_setting_type_0 = ToolPermissionMode(data)

                    return componentsschemas_tool_permission_setting_type_0
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                componentsschemas_tool_permission_setting_type_1 = cast(Literal["auto"], data)
                if componentsschemas_tool_permission_setting_type_1 != "auto":
                    raise ValueError(
                        f"/components/schemas/ToolPermissionSetting_type_1 must match const 'auto', got '{componentsschemas_tool_permission_setting_type_1}'"
                    )
                return componentsschemas_tool_permission_setting_type_1

            additional_property = _parse_additional_property(prop_dict)

            additional_properties[prop_name] = additional_property

        connection_tool_selection_permissions.additional_properties = additional_properties
        return connection_tool_selection_permissions

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Literal["auto"] | ToolPermissionMode:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Literal["auto"] | ToolPermissionMode) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
