from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.webhook_destination_config import WebhookDestinationConfig


T = TypeVar("T", bound="InlineHookSubscriptionInput")


@_attrs_define(repr=False)
class InlineHookSubscriptionInput:
    """
    Attributes:
        hook_names (list[str]):
        webhook (WebhookDestinationConfig):
    """

    hook_names: list[str]
    webhook: WebhookDestinationConfig

    def to_dict(self) -> dict[str, Any]:
        hook_names = self.hook_names

        webhook = self.webhook.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "hook_names": hook_names,
                "webhook": webhook,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.webhook_destination_config import WebhookDestinationConfig

        d = dict(src_dict)
        hook_names = cast(list[str], d.pop("hook_names"))

        webhook = WebhookDestinationConfig.from_dict(d.pop("webhook"))

        inline_hook_subscription_input = cls(
            hook_names=hook_names,
            webhook=webhook,
        )

        return inline_hook_subscription_input
