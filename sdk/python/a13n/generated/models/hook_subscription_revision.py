from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.principal_ref import PrincipalRef
    from ..models.webhook_destination_config import WebhookDestinationConfig


T = TypeVar("T", bound="HookSubscriptionRevision")


@_attrs_define(repr=False)
class HookSubscriptionRevision:
    """
    Attributes:
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        hook_names (list[str]):
        hook_subscription_id (str):
        id (str):
        version (int):
        webhook (WebhookDestinationConfig):
        run_id (None | str | Unset):
        session_id (None | str | Unset):
        thread_id (None | str | Unset):
    """

    created_at: datetime.datetime
    created_by: PrincipalRef
    hook_names: list[str]
    hook_subscription_id: str
    id: str
    version: int
    webhook: WebhookDestinationConfig
    run_id: str | Unset | None = UNSET
    session_id: str | Unset | None = UNSET
    thread_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        hook_names = self.hook_names

        hook_subscription_id = self.hook_subscription_id

        id = self.id

        version = self.version

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
                "created_at": created_at,
                "created_by": created_by,
                "hook_names": hook_names,
                "hook_subscription_id": hook_subscription_id,
                "id": id,
                "version": version,
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
        from ..models.principal_ref import PrincipalRef
        from ..models.webhook_destination_config import WebhookDestinationConfig

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        hook_names = cast(list[str], d.pop("hook_names"))

        hook_subscription_id = d.pop("hook_subscription_id")

        id = d.pop("id")

        version = d.pop("version")

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

        hook_subscription_revision = cls(
            created_at=created_at,
            created_by=created_by,
            hook_names=hook_names,
            hook_subscription_id=hook_subscription_id,
            id=id,
            version=version,
            webhook=webhook,
            run_id=run_id,
            session_id=session_id,
            thread_id=thread_id,
        )

        return hook_subscription_revision
