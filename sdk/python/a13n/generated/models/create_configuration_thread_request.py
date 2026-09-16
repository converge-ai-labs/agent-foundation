from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.source_selection import SourceSelection


T = TypeVar("T", bound="CreateConfigurationThreadRequest")


@_attrs_define(repr=False)
class CreateConfigurationThreadRequest:
    """
    Attributes:
        fork_from_run_id (str):
        source (None | SourceSelection | Unset):
    """

    fork_from_run_id: str
    source: SourceSelection | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.source_selection import SourceSelection

        fork_from_run_id = self.fork_from_run_id

        source: dict[str, Any] | Unset | None
        if isinstance(self.source, Unset):
            source = UNSET
        elif isinstance(self.source, SourceSelection):
            source = self.source.to_dict()
        else:
            source = self.source

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "fork_from_run_id": fork_from_run_id,
            }
        )
        if source is not UNSET:
            field_dict["source"] = source

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.source_selection import SourceSelection

        d = dict(src_dict)
        fork_from_run_id = d.pop("fork_from_run_id")

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

        create_configuration_thread_request = cls(
            fork_from_run_id=fork_from_run_id,
            source=source,
        )

        return create_configuration_thread_request
