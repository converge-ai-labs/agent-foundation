from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="PublishDocument")


@_attrs_define(repr=False)
class PublishDocument:
    """
    Attributes:
        recipient_scope_ids (list[str]):
        text (str):
        title (str):
        description (str | Unset):
    """

    recipient_scope_ids: list[str]
    text: str
    title: str
    description: str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        recipient_scope_ids = self.recipient_scope_ids

        text = self.text

        title = self.title

        description = self.description

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "recipient_scope_ids": recipient_scope_ids,
                "text": text,
                "title": title,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        recipient_scope_ids = cast(list[str], d.pop("recipient_scope_ids"))

        text = d.pop("text")

        title = d.pop("title")

        description = d.pop("description", UNSET)

        publish_document = cls(
            recipient_scope_ids=recipient_scope_ids,
            text=text,
            title=title,
            description=description,
        )

        return publish_document
