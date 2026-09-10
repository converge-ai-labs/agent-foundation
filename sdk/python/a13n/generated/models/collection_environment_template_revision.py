from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.environment_template_revision import EnvironmentTemplateRevision


T = TypeVar("T", bound="CollectionEnvironmentTemplateRevision")


@_attrs_define(repr=False)
class CollectionEnvironmentTemplateRevision:
    """
    Attributes:
        items (list[EnvironmentTemplateRevision]):
        next_cursor (None | str | Unset):
    """

    items: list[EnvironmentTemplateRevision]
    next_cursor: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        next_cursor: str | Unset | None
        if isinstance(self.next_cursor, Unset):
            next_cursor = UNSET
        else:
            next_cursor = self.next_cursor

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "items": items,
            }
        )
        if next_cursor is not UNSET:
            field_dict["next_cursor"] = next_cursor

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.environment_template_revision import EnvironmentTemplateRevision

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = EnvironmentTemplateRevision.from_dict(items_item_data)

            items.append(items_item)

        def _parse_next_cursor(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor", UNSET))

        collection_environment_template_revision = cls(
            items=items,
            next_cursor=next_cursor,
        )

        return collection_environment_template_revision
