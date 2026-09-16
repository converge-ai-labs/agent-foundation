from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.source_selection import SourceSelection


T = TypeVar("T", bound="CreateSessionRequest")


@_attrs_define(repr=False)
class CreateSessionRequest:
    """
    Attributes:
        source (None | SourceSelection | Unset):
        target_agent_id (None | str | Unset):
    """

    source: SourceSelection | Unset | None = UNSET
    target_agent_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.source_selection import SourceSelection

        source: dict[str, Any] | Unset | None
        if isinstance(self.source, Unset):
            source = UNSET
        elif isinstance(self.source, SourceSelection):
            source = self.source.to_dict()
        else:
            source = self.source

        target_agent_id: str | Unset | None
        if isinstance(self.target_agent_id, Unset):
            target_agent_id = UNSET
        else:
            target_agent_id = self.target_agent_id

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if source is not UNSET:
            field_dict["source"] = source
        if target_agent_id is not UNSET:
            field_dict["target_agent_id"] = target_agent_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.source_selection import SourceSelection

        d = dict(src_dict)

        def _parse_source(data: object) -> SourceSelection | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_type_0 = SourceSelection.from_dict(data)

                return source_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(SourceSelection | Unset | None, data)

        source = _parse_source(d.pop("source", UNSET))

        def _parse_target_agent_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        target_agent_id = _parse_target_agent_id(d.pop("target_agent_id", UNSET))

        create_session_request = cls(
            source=source,
            target_agent_id=target_agent_id,
        )

        return create_session_request
