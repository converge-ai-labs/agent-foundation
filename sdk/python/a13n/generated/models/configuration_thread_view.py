from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.configuration_application_receipt import ConfigurationApplicationReceipt
    from ..models.configuration_draft import ConfigurationDraft
    from ..models.thread import Thread


T = TypeVar("T", bound="ConfigurationThreadView")


@_attrs_define(repr=False)
class ConfigurationThreadView:
    """
    Attributes:
        active_draft_id (None | str):
        latest_draft (ConfigurationDraft):
        previous_application_receipt (ConfigurationApplicationReceipt | None):
        thread (Thread):
    """

    active_draft_id: str | None
    latest_draft: ConfigurationDraft
    previous_application_receipt: ConfigurationApplicationReceipt | None
    thread: Thread

    def to_dict(self) -> dict[str, Any]:
        from ..models.configuration_application_receipt import ConfigurationApplicationReceipt

        active_draft_id: str | None
        active_draft_id = self.active_draft_id

        latest_draft = self.latest_draft.to_dict()

        previous_application_receipt: dict[str, Any] | None
        if isinstance(self.previous_application_receipt, ConfigurationApplicationReceipt):
            previous_application_receipt = self.previous_application_receipt.to_dict()
        else:
            previous_application_receipt = self.previous_application_receipt

        thread = self.thread.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "active_draft_id": active_draft_id,
                "latest_draft": latest_draft,
                "previous_application_receipt": previous_application_receipt,
                "thread": thread,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.configuration_application_receipt import ConfigurationApplicationReceipt
        from ..models.configuration_draft import ConfigurationDraft
        from ..models.thread import Thread

        d = dict(src_dict)

        def _parse_active_draft_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        active_draft_id = _parse_active_draft_id(d.pop("active_draft_id"))

        latest_draft = ConfigurationDraft.from_dict(d.pop("latest_draft"))

        def _parse_previous_application_receipt(data: object) -> ConfigurationApplicationReceipt | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                previous_application_receipt_type_0 = ConfigurationApplicationReceipt.from_dict(data)

                return previous_application_receipt_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConfigurationApplicationReceipt | None, data)

        previous_application_receipt = _parse_previous_application_receipt(d.pop("previous_application_receipt"))

        thread = Thread.from_dict(d.pop("thread"))

        configuration_thread_view = cls(
            active_draft_id=active_draft_id,
            latest_draft=latest_draft,
            previous_application_receipt=previous_application_receipt,
            thread=thread,
        )

        return configuration_thread_view
