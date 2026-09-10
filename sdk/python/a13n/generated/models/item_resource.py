from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="ItemResource")


@_attrs_define(repr=False)
class ItemResource:
    """
    Attributes:
        content (Any):
        first_stream_id (str):
        id (str):
        kind (str):
        last_stream_id (str):
        parent_item_id (None | str):
        state (str):
    """

    content: Any
    first_stream_id: str
    id: str
    kind: str
    last_stream_id: str
    parent_item_id: str | None
    state: str

    def to_dict(self) -> dict[str, Any]:
        content = self.content

        first_stream_id = self.first_stream_id

        id = self.id

        kind = self.kind

        last_stream_id = self.last_stream_id

        parent_item_id: str | None
        parent_item_id = self.parent_item_id

        state = self.state

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "content": content,
                "first_stream_id": first_stream_id,
                "id": id,
                "kind": kind,
                "last_stream_id": last_stream_id,
                "parent_item_id": parent_item_id,
                "state": state,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        content = d.pop("content")

        first_stream_id = d.pop("first_stream_id")

        id = d.pop("id")

        kind = d.pop("kind")

        last_stream_id = d.pop("last_stream_id")

        def _parse_parent_item_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        parent_item_id = _parse_parent_item_id(d.pop("parent_item_id"))

        state = d.pop("state")

        item_resource = cls(
            content=content,
            first_stream_id=first_stream_id,
            id=id,
            kind=kind,
            last_stream_id=last_stream_id,
            parent_item_id=parent_item_id,
            state=state,
        )

        return item_resource
