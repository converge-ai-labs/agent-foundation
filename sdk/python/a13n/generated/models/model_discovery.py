from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.model_candidate import ModelCandidate
    from ..models.model_discovery_settings_schemas import ModelDiscoverySettingsSchemas


T = TypeVar("T", bound="ModelDiscovery")


@_attrs_define(repr=False)
class ModelDiscovery:
    """
    Attributes:
        items (list[ModelCandidate]):
        settings_schemas (ModelDiscoverySettingsSchemas):
    """

    items: list[ModelCandidate]
    settings_schemas: ModelDiscoverySettingsSchemas

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        settings_schemas = self.settings_schemas.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "items": items,
                "settings_schemas": settings_schemas,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_candidate import ModelCandidate
        from ..models.model_discovery_settings_schemas import ModelDiscoverySettingsSchemas

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = ModelCandidate.from_dict(items_item_data)

            items.append(items_item)

        settings_schemas = ModelDiscoverySettingsSchemas.from_dict(d.pop("settings_schemas"))

        model_discovery = cls(
            items=items,
            settings_schemas=settings_schemas,
        )

        return model_discovery
