from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.tool_definition_supported_permissions_item import ToolDefinitionSupportedPermissionsItem
from ..models.tool_permission_mode import ToolPermissionMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.tool_definition_config_schema import ToolDefinitionConfigSchema
    from ..models.tool_resource_selector import ToolResourceSelector


T = TypeVar("T", bound="ToolDefinition")


@_attrs_define(repr=False)
class ToolDefinition:
    """
    Attributes:
        config_schema (ToolDefinitionConfigSchema):
        default_enabled (bool):
        default_permission (ToolPermissionMode):
        display_name (str):
        execution_id (str):
        key (str):
        model_name (str):
        supported_permissions (list[ToolDefinitionSupportedPermissionsItem]):
        deployment_supported (bool | Unset):
        resource_selector (None | ToolResourceSelector | Unset):
    """

    config_schema: ToolDefinitionConfigSchema
    default_enabled: bool
    default_permission: ToolPermissionMode
    display_name: str
    execution_id: str
    key: str
    model_name: str
    supported_permissions: list[ToolDefinitionSupportedPermissionsItem]
    deployment_supported: bool | Unset = UNSET
    resource_selector: ToolResourceSelector | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.tool_resource_selector import ToolResourceSelector

        config_schema = self.config_schema.to_dict()

        default_enabled = self.default_enabled

        default_permission = self.default_permission.value

        display_name = self.display_name

        execution_id = self.execution_id

        key = self.key

        model_name = self.model_name

        supported_permissions = []
        for supported_permissions_item_data in self.supported_permissions:
            supported_permissions_item = supported_permissions_item_data.value
            supported_permissions.append(supported_permissions_item)

        deployment_supported = self.deployment_supported

        resource_selector: dict[str, Any] | Unset | None
        if isinstance(self.resource_selector, Unset):
            resource_selector = UNSET
        elif isinstance(self.resource_selector, ToolResourceSelector):
            resource_selector = self.resource_selector.to_dict()
        else:
            resource_selector = self.resource_selector

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "config_schema": config_schema,
                "default_enabled": default_enabled,
                "default_permission": default_permission,
                "display_name": display_name,
                "execution_id": execution_id,
                "key": key,
                "model_name": model_name,
                "supported_permissions": supported_permissions,
            }
        )
        if deployment_supported is not UNSET:
            field_dict["deployment_supported"] = deployment_supported
        if resource_selector is not UNSET:
            field_dict["resource_selector"] = resource_selector

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.tool_definition_config_schema import ToolDefinitionConfigSchema
        from ..models.tool_resource_selector import ToolResourceSelector

        d = dict(src_dict)
        config_schema = ToolDefinitionConfigSchema.from_dict(d.pop("config_schema"))

        default_enabled = d.pop("default_enabled")

        default_permission = ToolPermissionMode(d.pop("default_permission"))

        display_name = d.pop("display_name")

        execution_id = d.pop("execution_id")

        key = d.pop("key")

        model_name = d.pop("model_name")

        supported_permissions = []
        _supported_permissions = d.pop("supported_permissions")
        for supported_permissions_item_data in _supported_permissions:
            supported_permissions_item = ToolDefinitionSupportedPermissionsItem(supported_permissions_item_data)

            supported_permissions.append(supported_permissions_item)

        deployment_supported = d.pop("deployment_supported", UNSET)

        def _parse_resource_selector(data: object) -> ToolResourceSelector | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                resource_selector_type_0 = ToolResourceSelector.from_dict(data)

                return resource_selector_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ToolResourceSelector | Unset | None, data)

        resource_selector = _parse_resource_selector(d.pop("resource_selector", UNSET))

        tool_definition = cls(
            config_schema=config_schema,
            default_enabled=default_enabled,
            default_permission=default_permission,
            display_name=display_name,
            execution_id=execution_id,
            key=key,
            model_name=model_name,
            supported_permissions=supported_permissions,
            deployment_supported=deployment_supported,
            resource_selector=resource_selector,
        )

        return tool_definition
