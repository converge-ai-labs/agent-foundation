from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..models.tool_permission_mode import ToolPermissionMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.tool_selection_config import ToolSelectionConfig


T = TypeVar("T", bound="ToolSelection")


@_attrs_define(repr=False)
class ToolSelection:
    """
    Attributes:
        config (ToolSelectionConfig | Unset):
        enabled (bool | Unset):
        permission (Literal['auto'] | ToolPermissionMode | Unset):
    """

    config: ToolSelectionConfig | Unset = UNSET
    enabled: bool | Unset = UNSET
    permission: Literal["auto"] | ToolPermissionMode | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        enabled = self.enabled

        permission: Literal["auto"] | str | Unset
        if isinstance(self.permission, Unset):
            permission = UNSET
        elif isinstance(self.permission, ToolPermissionMode):
            permission = self.permission.value
        else:
            permission = self.permission

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if config is not UNSET:
            field_dict["config"] = config
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if permission is not UNSET:
            field_dict["permission"] = permission

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.tool_selection_config import ToolSelectionConfig

        d = dict(src_dict)
        _config = d.pop("config", UNSET)
        config: ToolSelectionConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = ToolSelectionConfig.from_dict(_config)

        enabled = d.pop("enabled", UNSET)

        def _parse_permission(data: object) -> Literal["auto"] | ToolPermissionMode | Unset:
            if isinstance(data, Unset):
                return data
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

        permission = _parse_permission(d.pop("permission", UNSET))

        tool_selection = cls(
            config=config,
            enabled=enabled,
            permission=permission,
        )

        return tool_selection
