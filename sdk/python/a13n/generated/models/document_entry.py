from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.document_entry_kind import DocumentEntryKind
from ..models.document_entry_state import DocumentEntryState
from ..types import UNSET, Unset

T = TypeVar("T", bound="DocumentEntry")


@_attrs_define(repr=False)
class DocumentEntry:
    """
    Attributes:
        activity_date (datetime.date):
        description (str):
        id (str):
        kind (DocumentEntryKind):
        path (str):
        saved_at (datetime.datetime | None):
        scope_id (str):
        state (DocumentEntryState):
        timezone (str):
        title (str):
        correction_of (None | str | Unset):
        publication_source_id (None | str | Unset):
        shared (bool | Unset):
        version (int | Unset):
    """

    activity_date: datetime.date
    description: str
    id: str
    kind: DocumentEntryKind
    path: str
    saved_at: datetime.datetime | None
    scope_id: str
    state: DocumentEntryState
    timezone: str
    title: str
    correction_of: str | Unset | None = UNSET
    publication_source_id: str | Unset | None = UNSET
    shared: bool | Unset = UNSET
    version: int | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        activity_date = self.activity_date.isoformat()

        description = self.description

        id = self.id

        kind = self.kind.value

        path = self.path

        saved_at: str | None
        if isinstance(self.saved_at, datetime.datetime):
            saved_at = self.saved_at.isoformat()
        else:
            saved_at = self.saved_at

        scope_id = self.scope_id

        state = self.state.value

        timezone = self.timezone

        title = self.title

        correction_of: str | Unset | None
        if isinstance(self.correction_of, Unset):
            correction_of = UNSET
        else:
            correction_of = self.correction_of

        publication_source_id: str | Unset | None
        if isinstance(self.publication_source_id, Unset):
            publication_source_id = UNSET
        else:
            publication_source_id = self.publication_source_id

        shared = self.shared

        version = self.version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "activity_date": activity_date,
                "description": description,
                "id": id,
                "kind": kind,
                "path": path,
                "saved_at": saved_at,
                "scope_id": scope_id,
                "state": state,
                "timezone": timezone,
                "title": title,
            }
        )
        if correction_of is not UNSET:
            field_dict["correction_of"] = correction_of
        if publication_source_id is not UNSET:
            field_dict["publication_source_id"] = publication_source_id
        if shared is not UNSET:
            field_dict["shared"] = shared
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        activity_date = datetime.date.fromisoformat(d.pop("activity_date"))

        description = d.pop("description")

        id = d.pop("id")

        kind = DocumentEntryKind(d.pop("kind"))

        path = d.pop("path")

        def _parse_saved_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                saved_at_type_0 = datetime.datetime.fromisoformat(data)

                return saved_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        saved_at = _parse_saved_at(d.pop("saved_at"))

        scope_id = d.pop("scope_id")

        state = DocumentEntryState(d.pop("state"))

        timezone = d.pop("timezone")

        title = d.pop("title")

        def _parse_correction_of(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        correction_of = _parse_correction_of(d.pop("correction_of", UNSET))

        def _parse_publication_source_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        publication_source_id = _parse_publication_source_id(d.pop("publication_source_id", UNSET))

        shared = d.pop("shared", UNSET)

        version = d.pop("version", UNSET)

        document_entry = cls(
            activity_date=activity_date,
            description=description,
            id=id,
            kind=kind,
            path=path,
            saved_at=saved_at,
            scope_id=scope_id,
            state=state,
            timezone=timezone,
            title=title,
            correction_of=correction_of,
            publication_source_id=publication_source_id,
            shared=shared,
            version=version,
        )

        return document_entry
