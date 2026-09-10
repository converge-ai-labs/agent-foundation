from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.webhook_destination_config import WebhookDestinationConfig


T = TypeVar("T", bound="CreateHookSubscriptionRequest")


@_attrs_define(repr=False)
class CreateHookSubscriptionRequest:
    """
    Attributes:
        hook_names (list[str]):
        webhook (WebhookDestinationConfig):
        run_id (None | str | Unset):
        session_id (None | str | Unset):
        thread_id (None | str | Unset):
    """

    hook_names: list[str]
    webhook: WebhookDestinationConfig
    run_id: str | Unset | None = UNSET
    session_id: str | Unset | None = UNSET
    thread_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        hook_names = self.hook_names

        webhook = self.webhook.to_dict()

        run_id: str | Unset | None
        if isinstance(self.run_id, Unset):
            run_id = UNSET
        else:
            run_id = self.run_id

        session_id: str | Unset | None
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        thread_id: str | Unset | None
        if isinstance(self.thread_id, Unset):
            thread_id = UNSET
        else:
            thread_id = self.thread_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "hook_names": hook_names,
                "webhook": webhook,
            }
        )
        if run_id is not UNSET:
            field_dict["run_id"] = run_id
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if thread_id is not UNSET:
            field_dict["thread_id"] = thread_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.webhook_destination_config import WebhookDestinationConfig

        d = dict(src_dict)
        hook_names = cast(list[str], d.pop("hook_names"))

        webhook = WebhookDestinationConfig.from_dict(d.pop("webhook"))

        def _parse_run_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        run_id = _parse_run_id(d.pop("run_id", UNSET))

        def _parse_session_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        def _parse_thread_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        thread_id = _parse_thread_id(d.pop("thread_id", UNSET))

        create_hook_subscription_request = cls(
            hook_names=hook_names,
            webhook=webhook,
            run_id=run_id,
            session_id=session_id,
            thread_id=thread_id,
        )

        return create_hook_subscription_request
