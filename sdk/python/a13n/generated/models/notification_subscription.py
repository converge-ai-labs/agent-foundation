from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.notification_subscription_scope import NotificationSubscriptionScope
from ..models.notification_subscription_topics_item import NotificationSubscriptionTopicsItem

T = TypeVar("T", bound="NotificationSubscription")


@_attrs_define(repr=False)
class NotificationSubscription:
    """
    Attributes:
        resource_id (str):
        scope (NotificationSubscriptionScope):
        subscription_id (str):
        topics (list[NotificationSubscriptionTopicsItem]):
    """

    resource_id: str
    scope: NotificationSubscriptionScope
    subscription_id: str
    topics: list[NotificationSubscriptionTopicsItem]

    def to_dict(self) -> dict[str, Any]:
        resource_id = self.resource_id

        scope = self.scope.value

        subscription_id = self.subscription_id

        topics = []
        for topics_item_data in self.topics:
            topics_item = topics_item_data.value
            topics.append(topics_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "resource_id": resource_id,
                "scope": scope,
                "subscription_id": subscription_id,
                "topics": topics,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        resource_id = d.pop("resource_id")

        scope = NotificationSubscriptionScope(d.pop("scope"))

        subscription_id = d.pop("subscription_id")

        topics = []
        _topics = d.pop("topics")
        for topics_item_data in _topics:
            topics_item = NotificationSubscriptionTopicsItem(topics_item_data)

            topics.append(topics_item)

        notification_subscription = cls(
            resource_id=resource_id,
            scope=scope,
            subscription_id=subscription_id,
            topics=topics,
        )

        return notification_subscription
