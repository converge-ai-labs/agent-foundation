from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..models.tool_permission_mode import ToolPermissionMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.tool_permissions_rules import ToolPermissionsRules


T = TypeVar("T", bound="ToolPermissions")


@_attrs_define(repr=False)
class ToolPermissions:
    """Portable configuration. Inherit resolves a tool default, never an execution decision.

    Attributes:
        default (Literal['inherit'] | ToolPermissionMode | Unset):
        rules (ToolPermissionsRules | Unset):
    """

    default: Literal["inherit"] | ToolPermissionMode | Unset = UNSET
    rules: ToolPermissionsRules | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        default: Literal["inherit"] | str | Unset
        if isinstance(self.default, Unset):
            default = UNSET
        elif isinstance(self.default, ToolPermissionMode):
            default = self.default.value
        else:
            default = self.default

        rules: dict[str, Any] | Unset = UNSET
        if not isinstance(self.rules, Unset):
            rules = self.rules.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if default is not UNSET:
            field_dict["default"] = default
        if rules is not UNSET:
            field_dict["rules"] = rules

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.tool_permissions_rules import ToolPermissionsRules

        d = dict(src_dict)

        def _parse_default(data: object) -> Literal["inherit"] | ToolPermissionMode | Unset:
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

        default = _parse_default(d.pop("default", UNSET))

        _rules = d.pop("rules", UNSET)
        rules: ToolPermissionsRules | Unset
        if isinstance(_rules, Unset):
            rules = UNSET
        else:
            rules = ToolPermissionsRules.from_dict(_rules)

        tool_permissions = cls(
            default=default,
            rules=rules,
        )

        return tool_permissions
