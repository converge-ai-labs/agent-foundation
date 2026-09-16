from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="LarkReplyReceipt")


@_attrs_define(repr=False)
class LarkReplyReceipt:
    """
    Attributes:
        message_id (str):
        request_id (str):
        root_id (None | str | Unset):
        thread_id (None | str | Unset):
    """

    message_id: str
    request_id: str
    root_id: str | Unset | None = UNSET
    thread_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        message_id = self.message_id

        request_id = self.request_id

        root_id: str | Unset | None
        if isinstance(self.root_id, Unset):
            root_id = UNSET
        else:
            root_id = self.root_id

        thread_id: str | Unset | None
        if isinstance(self.thread_id, Unset):
            thread_id = UNSET
        else:
            thread_id = self.thread_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "message_id": message_id,
                "request_id": request_id,
            }
        )
        if root_id is not UNSET:
            field_dict["root_id"] = root_id
        if thread_id is not UNSET:
            field_dict["thread_id"] = thread_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        message_id = d.pop("message_id")

        request_id = d.pop("request_id")

        def _parse_root_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        root_id = _parse_root_id(d.pop("root_id", UNSET))

        def _parse_thread_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        thread_id = _parse_thread_id(d.pop("thread_id", UNSET))

        lark_reply_receipt = cls(
            message_id=message_id,
            request_id=request_id,
            root_id=root_id,
            thread_id=thread_id,
        )

        return lark_reply_receipt
