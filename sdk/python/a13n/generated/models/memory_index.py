from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.document_entry import DocumentEntry


T = TypeVar("T", bound="MemoryIndex")


@_attrs_define(repr=False)
class MemoryIndex:
    """
    Attributes:
        entries (list[DocumentEntry]):
        text (str):
        next_cursor (None | str | Unset):
        path (Literal['MEMORY.md'] | Unset):
    """

    entries: list[DocumentEntry]
    text: str
    next_cursor: str | Unset | None = UNSET
    path: Literal["MEMORY.md"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        entries = []
        for entries_item_data in self.entries:
            entries_item = entries_item_data.to_dict()
            entries.append(entries_item)

        text = self.text

        next_cursor: str | Unset | None
        if isinstance(self.next_cursor, Unset):
            next_cursor = UNSET
        else:
            next_cursor = self.next_cursor

        path = self.path

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "entries": entries,
                "text": text,
            }
        )
        if next_cursor is not UNSET:
            field_dict["next_cursor"] = next_cursor
        if path is not UNSET:
            field_dict["path"] = path

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.document_entry import DocumentEntry

        d = dict(src_dict)
        entries = []
        _entries = d.pop("entries")
        for entries_item_data in _entries:
            entries_item = DocumentEntry.from_dict(entries_item_data)

            entries.append(entries_item)

        text = d.pop("text")

        def _parse_next_cursor(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor", UNSET))

        path = cast(Literal["MEMORY.md"] | Unset, d.pop("path", UNSET))
        if path != "MEMORY.md" and not isinstance(path, Unset):
            raise ValueError(f"path must match const 'MEMORY.md', got '{path}'")

        memory_index = cls(
            entries=entries,
            text=text,
            next_cursor=next_cursor,
            path=path,
        )

        return memory_index
