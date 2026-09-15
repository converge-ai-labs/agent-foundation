from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..models.tool_permission_mode import ToolPermissionMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connection_tool_selection_permissions import ConnectionToolSelectionPermissions


T = TypeVar("T", bound="ConnectionToolSelection")


@_attrs_define(repr=False)
class ConnectionToolSelection:
    """
    Attributes:
        connection_id (str):
        defer_loading (bool | Unset):
        permission (Literal['inherit'] | ToolPermissionMode | Unset):
        permissions (ConnectionToolSelectionPermissions | Unset):
        tools (list[str] | None | Unset):
    """

    connection_id: str
    defer_loading: bool | Unset = UNSET
    permission: Literal["inherit"] | ToolPermissionMode | Unset = UNSET
    permissions: ConnectionToolSelectionPermissions | Unset = UNSET
    tools: list[str] | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        connection_id = self.connection_id

        defer_loading = self.defer_loading

        permission: Literal["inherit"] | str | Unset
        if isinstance(self.permission, Unset):
            permission = UNSET
        elif isinstance(self.permission, ToolPermissionMode):
            permission = self.permission.value
        else:
            permission = self.permission

        permissions: dict[str, Any] | Unset = UNSET
        if not isinstance(self.permissions, Unset):
            permissions = self.permissions.to_dict()

        tools: list[str] | Unset | None
        if isinstance(self.tools, Unset):
            tools = UNSET
        elif isinstance(self.tools, list):
            tools = self.tools

        else:
            tools = self.tools

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connection_id": connection_id,
            }
        )
        if defer_loading is not UNSET:
            field_dict["defer_loading"] = defer_loading
        if permission is not UNSET:
            field_dict["permission"] = permission
        if permissions is not UNSET:
            field_dict["permissions"] = permissions
        if tools is not UNSET:
            field_dict["tools"] = tools

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connection_tool_selection_permissions import ConnectionToolSelectionPermissions

        d = dict(src_dict)
        connection_id = d.pop("connection_id")

        defer_loading = d.pop("defer_loading", UNSET)

        def _parse_permission(data: object) -> Literal["inherit"] | ToolPermissionMode | Unset:
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                componentsschemas_tool_permission_setting_type_0 = ToolPermissionMode(data)

                return componentsschemas_tool_permission_setting_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            componentsschemas_tool_permission_setting_type_1 = cast(Literal["inherit"], data)
            if componentsschemas_tool_permission_setting_type_1 != "inherit":
                raise ValueError(
                    f"/components/schemas/ToolPermissionSetting_type_1 must match const 'inherit', got '{componentsschemas_tool_permission_setting_type_1}'"
                )
            return componentsschemas_tool_permission_setting_type_1

        permission = _parse_permission(d.pop("permission", UNSET))

        _permissions = d.pop("permissions", UNSET)
        permissions: ConnectionToolSelectionPermissions | Unset
        if isinstance(_permissions, Unset):
            permissions = UNSET
        else:
            permissions = ConnectionToolSelectionPermissions.from_dict(_permissions)

        def _parse_tools(data: object) -> list[str] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tools_type_0 = cast(list[str], data)

                return tools_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | Unset | None, data)

        tools = _parse_tools(d.pop("tools", UNSET))

        connection_tool_selection = cls(
            connection_id=connection_id,
            defer_loading=defer_loading,
            permission=permission,
            permissions=permissions,
            tools=tools,
        )

        return connection_tool_selection
