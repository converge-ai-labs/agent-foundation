from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.messaging_policy_interaction_mode import MessagingPolicyInteractionMode
from ..models.messaging_policy_reply_mode import MessagingPolicyReplyMode

T = TypeVar("T", bound="MessagingPolicy")


@_attrs_define(repr=False)
class MessagingPolicy:
    """
    Attributes:
        interaction_mode (MessagingPolicyInteractionMode):
        reply_mode (MessagingPolicyReplyMode):
    """

    interaction_mode: MessagingPolicyInteractionMode
    reply_mode: MessagingPolicyReplyMode

    def to_dict(self) -> dict[str, Any]:
        interaction_mode = self.interaction_mode.value

        reply_mode = self.reply_mode.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "interaction_mode": interaction_mode,
                "reply_mode": reply_mode,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        interaction_mode = MessagingPolicyInteractionMode(d.pop("interaction_mode"))

        reply_mode = MessagingPolicyReplyMode(d.pop("reply_mode"))

        messaging_policy = cls(
            interaction_mode=interaction_mode,
            reply_mode=reply_mode,
        )

        return messaging_policy
