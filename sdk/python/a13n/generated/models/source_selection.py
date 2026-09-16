from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.source_selection_selector import SourceSelectionSelector
from ..types import UNSET, Unset

T = TypeVar("T", bound="SourceSelection")


@_attrs_define(repr=False)
class SourceSelection:
    """
    Attributes:
        revision_id (None | str | Unset):
        selector (SourceSelectionSelector | Unset):
    """

    revision_id: str | Unset | None = UNSET
    selector: SourceSelectionSelector | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        revision_id: str | Unset | None
        if isinstance(self.revision_id, Unset):
            revision_id = UNSET
        else:
            revision_id = self.revision_id

        selector: str | Unset = UNSET
        if not isinstance(self.selector, Unset):
            selector = self.selector.value

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if revision_id is not UNSET:
            field_dict["revision_id"] = revision_id
        if selector is not UNSET:
            field_dict["selector"] = selector

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_revision_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        revision_id = _parse_revision_id(d.pop("revision_id", UNSET))

        _selector = d.pop("selector", UNSET)
        selector: SourceSelectionSelector | Unset
        if isinstance(_selector, Unset):
            selector = UNSET
        else:
            selector = SourceSelectionSelector(_selector)

        source_selection = cls(
            revision_id=revision_id,
            selector=selector,
        )

        return source_selection
