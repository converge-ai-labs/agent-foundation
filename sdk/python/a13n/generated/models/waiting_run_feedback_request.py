from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.approve_pending_resolution import ApprovePendingResolution
    from ..models.complete_pending_resolution import CompletePendingResolution
    from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
    from ..models.reject_pending_resolution import RejectPendingResolution
    from ..models.respond_pending_resolution import RespondPendingResolution
    from ..models.waiting_run_feedback_request_labels import WaitingRunFeedbackRequestLabels


T = TypeVar("T", bound="WaitingRunFeedbackRequest")


@_attrs_define(repr=False)
class WaitingRunFeedbackRequest:
    """
    Attributes:
        expected_thread_version (int):
        sealed_state_digest_sha256 (str):
        hook_subscription (InlineHookSubscriptionInput | None | Unset):
        labels (WaitingRunFeedbackRequestLabels | Unset):
        resolutions (list[ApprovePendingResolution | CompletePendingResolution | RejectPendingResolution |
            RespondPendingResolution] | Unset):
    """

    expected_thread_version: int
    sealed_state_digest_sha256: str
    hook_subscription: InlineHookSubscriptionInput | Unset | None = UNSET
    labels: WaitingRunFeedbackRequestLabels | Unset = UNSET
    resolutions: (
        list[ApprovePendingResolution | CompletePendingResolution | RejectPendingResolution | RespondPendingResolution]
        | Unset
    ) = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.approve_pending_resolution import ApprovePendingResolution
        from ..models.complete_pending_resolution import CompletePendingResolution
        from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
        from ..models.reject_pending_resolution import RejectPendingResolution

        expected_thread_version = self.expected_thread_version

        sealed_state_digest_sha256 = self.sealed_state_digest_sha256

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

        resolutions: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.resolutions, Unset):
            resolutions = []
            for resolutions_item_data in self.resolutions:
                resolutions_item: dict[str, Any]
                if isinstance(resolutions_item_data, ApprovePendingResolution):
                    resolutions_item = resolutions_item_data.to_dict()
                elif isinstance(resolutions_item_data, RejectPendingResolution):
                    resolutions_item = resolutions_item_data.to_dict()
                elif isinstance(resolutions_item_data, CompletePendingResolution):
                    resolutions_item = resolutions_item_data.to_dict()
                else:
                    resolutions_item = resolutions_item_data.to_dict()

                resolutions.append(resolutions_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_thread_version": expected_thread_version,
                "sealed_state_digest_sha256": sealed_state_digest_sha256,
            }
        )
        if hook_subscription is not UNSET:
            field_dict["hook_subscription"] = hook_subscription
        if labels is not UNSET:
            field_dict["labels"] = labels
        if resolutions is not UNSET:
            field_dict["resolutions"] = resolutions

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.approve_pending_resolution import ApprovePendingResolution
        from ..models.complete_pending_resolution import CompletePendingResolution
        from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
        from ..models.reject_pending_resolution import RejectPendingResolution
        from ..models.respond_pending_resolution import RespondPendingResolution
        from ..models.waiting_run_feedback_request_labels import WaitingRunFeedbackRequestLabels

        d = dict(src_dict)
        expected_thread_version = d.pop("expected_thread_version")

        sealed_state_digest_sha256 = d.pop("sealed_state_digest_sha256")

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
        labels: WaitingRunFeedbackRequestLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = WaitingRunFeedbackRequestLabels.from_dict(_labels)

        _resolutions = d.pop("resolutions", UNSET)
        resolutions: (
            list[
                ApprovePendingResolution
                | CompletePendingResolution
                | RejectPendingResolution
                | RespondPendingResolution
            ]
            | Unset
        ) = UNSET
        if _resolutions is not UNSET:
            resolutions = []
            for resolutions_item_data in _resolutions:

                def _parse_resolutions_item(
                    data: object,
                ) -> (
                    ApprovePendingResolution
                    | CompletePendingResolution
                    | RejectPendingResolution
                    | RespondPendingResolution
                ):
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        resolutions_item_type_0 = ApprovePendingResolution.from_dict(data)

                        return resolutions_item_type_0
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        resolutions_item_type_1 = RejectPendingResolution.from_dict(data)

                        return resolutions_item_type_1
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        resolutions_item_type_2 = CompletePendingResolution.from_dict(data)

                        return resolutions_item_type_2
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    if not isinstance(data, dict):
                        raise TypeError()
                    resolutions_item_type_3 = RespondPendingResolution.from_dict(data)

                    return resolutions_item_type_3

                resolutions_item = _parse_resolutions_item(resolutions_item_data)

                resolutions.append(resolutions_item)

        waiting_run_feedback_request = cls(
            expected_thread_version=expected_thread_version,
            sealed_state_digest_sha256=sealed_state_digest_sha256,
            hook_subscription=hook_subscription,
            labels=labels,
            resolutions=resolutions,
        )

        return waiting_run_feedback_request
