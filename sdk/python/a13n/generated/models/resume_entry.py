from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.resume_entry_status import ResumeEntryStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="ResumeEntry")


@_attrs_define(repr=False)
class ResumeEntry:
    """A per-interrupt response in the resume array of a RunAgentInput.

    Attributes:
        interrupt_id (str):
        status (ResumeEntryStatus):
        payload (Any | None | Unset):
    """

    interrupt_id: str
    status: ResumeEntryStatus
    payload: Any | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        interrupt_id = self.interrupt_id

        status = self.status.value

        payload: Any | Unset | None
        if isinstance(self.payload, Unset):
            payload = UNSET
        else:
            payload = self.payload

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "interruptId": interrupt_id,
                "status": status,
            }
        )
        if payload is not UNSET:
            field_dict["payload"] = payload

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        interrupt_id = d.pop("interruptId")

        status = ResumeEntryStatus(d.pop("status"))

        def _parse_payload(data: object) -> Any | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | Unset | None, data)

        payload = _parse_payload(d.pop("payload", UNSET))

        resume_entry = cls(
            interrupt_id=interrupt_id,
            status=status,
            payload=payload,
        )

        resume_entry.additional_properties = d
        return resume_entry

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
