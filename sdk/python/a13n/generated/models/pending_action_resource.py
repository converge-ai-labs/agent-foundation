from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="PendingActionResource")


@_attrs_define(repr=False)
class PendingActionResource:
    """
    Attributes:
        call_id (str):
        kind (str):
        presentation (Any | None):
        provider_type (None | str):
        tool_name (None | str):
    """

    call_id: str
    kind: str
    presentation: Any | None
    provider_type: str | None
    tool_name: str | None

    def to_dict(self) -> dict[str, Any]:
        call_id = self.call_id

        kind = self.kind

        presentation: Any | None
        presentation = self.presentation

        provider_type: str | None
        provider_type = self.provider_type

        tool_name: str | None
        tool_name = self.tool_name

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "call_id": call_id,
                "kind": kind,
                "presentation": presentation,
                "provider_type": provider_type,
                "tool_name": tool_name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        call_id = d.pop("call_id")

        kind = d.pop("kind")

        def _parse_presentation(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        presentation = _parse_presentation(d.pop("presentation"))

        def _parse_provider_type(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        provider_type = _parse_provider_type(d.pop("provider_type"))

        def _parse_tool_name(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        tool_name = _parse_tool_name(d.pop("tool_name"))

        pending_action_resource = cls(
            call_id=call_id,
            kind=kind,
            presentation=presentation,
            provider_type=provider_type,
            tool_name=tool_name,
        )

        return pending_action_resource
