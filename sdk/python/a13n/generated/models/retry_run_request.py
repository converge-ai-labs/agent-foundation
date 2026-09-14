from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
    from ..models.retry_run_request_labels import RetryRunRequestLabels


T = TypeVar("T", bound="RetryRunRequest")


@_attrs_define(repr=False)
class RetryRunRequest:
    """
    Attributes:
        expected_thread_version (int):
        hook_subscription (InlineHookSubscriptionInput | None | Unset):
        labels (RetryRunRequestLabels | Unset):
    """

    expected_thread_version: int
    hook_subscription: InlineHookSubscriptionInput | Unset | None = UNSET
    labels: RetryRunRequestLabels | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput

        expected_thread_version = self.expected_thread_version

        hook_subscription: dict[str, Any] | Unset | None
        if isinstance(self.hook_subscription, Unset):
            hook_subscription = UNSET
        elif isinstance(self.hook_subscription, InlineHookSubscriptionInput):
            hook_subscription = self.hook_subscription.to_dict()
        else:
            hook_subscription = self.hook_subscription

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_thread_version": expected_thread_version,
            }
        )
        if hook_subscription is not UNSET:
            field_dict["hook_subscription"] = hook_subscription
        if labels is not UNSET:
            field_dict["labels"] = labels

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
        from ..models.retry_run_request_labels import RetryRunRequestLabels

        d = dict(src_dict)
        expected_thread_version = d.pop("expected_thread_version")

        def _parse_hook_subscription(data: object) -> InlineHookSubscriptionInput | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                hook_subscription_type_0 = InlineHookSubscriptionInput.from_dict(data)

                return hook_subscription_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InlineHookSubscriptionInput | Unset | None, data)

        hook_subscription = _parse_hook_subscription(d.pop("hook_subscription", UNSET))

        _labels = d.pop("labels", UNSET)
        labels: RetryRunRequestLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = RetryRunRequestLabels.from_dict(_labels)

        retry_run_request = cls(
            expected_thread_version=expected_thread_version,
            hook_subscription=hook_subscription,
            labels=labels,
        )

        return retry_run_request
