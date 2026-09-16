from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="SlackReplyReceipt")


@_attrs_define(repr=False)
class SlackReplyReceipt:
    """
    Attributes:
        channel_id (str):
        message_ts (str):
        request_id (str):
        root_thread_ts (str):
    """

    channel_id: str
    message_ts: str
    request_id: str
    root_thread_ts: str

    def to_dict(self) -> dict[str, Any]:
        channel_id = self.channel_id

        message_ts = self.message_ts

        request_id = self.request_id

        root_thread_ts = self.root_thread_ts

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "channel_id": channel_id,
                "message_ts": message_ts,
                "request_id": request_id,
                "root_thread_ts": root_thread_ts,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        channel_id = d.pop("channel_id")

        message_ts = d.pop("message_ts")

        request_id = d.pop("request_id")

        root_thread_ts = d.pop("root_thread_ts")

        slack_reply_receipt = cls(
            channel_id=channel_id,
            message_ts=message_ts,
            request_id=request_id,
            root_thread_ts=root_thread_ts,
        )

        return slack_reply_receipt
