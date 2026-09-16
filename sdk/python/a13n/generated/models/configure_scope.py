from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConfigureScope")


@_attrs_define(repr=False)
class ConfigureScope:
    """
    Attributes:
        external_conversation_id (str):
        enabled (bool | Unset):
        expected_version (int | None | Unset):
        save_on_request (bool | Unset):
        timezone (str | Unset):
        use_memory (bool | Unset):
    """

    external_conversation_id: str
    enabled: bool | Unset = UNSET
    expected_version: int | Unset | None = UNSET
    save_on_request: bool | Unset = UNSET
    timezone: str | Unset = UNSET
    use_memory: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        external_conversation_id = self.external_conversation_id

        enabled = self.enabled

        expected_version: int | Unset | None
        if isinstance(self.expected_version, Unset):
            expected_version = UNSET
        else:
            expected_version = self.expected_version

        save_on_request = self.save_on_request

        timezone = self.timezone

        use_memory = self.use_memory

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "external_conversation_id": external_conversation_id,
            }
        )
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if expected_version is not UNSET:
            field_dict["expected_version"] = expected_version
        if save_on_request is not UNSET:
            field_dict["save_on_request"] = save_on_request
        if timezone is not UNSET:
            field_dict["timezone"] = timezone
        if use_memory is not UNSET:
            field_dict["use_memory"] = use_memory

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        external_conversation_id = d.pop("external_conversation_id")

        enabled = d.pop("enabled", UNSET)

        def _parse_expected_version(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        expected_version = _parse_expected_version(d.pop("expected_version", UNSET))

        save_on_request = d.pop("save_on_request", UNSET)

        timezone = d.pop("timezone", UNSET)

        use_memory = d.pop("use_memory", UNSET)

        configure_scope = cls(
            external_conversation_id=external_conversation_id,
            enabled=enabled,
            expected_version=expected_version,
            save_on_request=save_on_request,
            timezone=timezone,
            use_memory=use_memory,
        )

        return configure_scope
