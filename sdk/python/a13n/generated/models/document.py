from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.document_kind import DocumentKind
from ..models.document_state import DocumentState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.document_access_reason import DocumentAccessReason


T = TypeVar("T", bound="Document")


@_attrs_define(repr=False)
class Document:
    """
    Attributes:
        activity_date (datetime.date):
        description (str):
        id (str):
        kind (DocumentKind):
        path (str):
        saved_at (datetime.datetime | None):
        scope_id (str):
        state (DocumentState):
        text (str):
        timezone (str):
        title (str):
        access_reasons (list[DocumentAccessReason] | Unset):
        correction_of (None | str | Unset):
        more_access_reasons (bool | Unset):
        owner_name (None | str | Unset):
        publication_source_id (None | str | Unset):
        shared (bool | Unset):
        version (int | Unset):
    """

    activity_date: datetime.date
    description: str
    id: str
    kind: DocumentKind
    path: str
    saved_at: datetime.datetime | None
    scope_id: str
    state: DocumentState
    text: str
    timezone: str
    title: str
    access_reasons: list[DocumentAccessReason] | Unset = UNSET
    correction_of: str | Unset | None = UNSET
    more_access_reasons: bool | Unset = UNSET
    owner_name: str | Unset | None = UNSET
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

        text = self.text

        timezone = self.timezone

        title = self.title

        access_reasons: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.access_reasons, Unset):
            access_reasons = []
            for access_reasons_item_data in self.access_reasons:
                access_reasons_item = access_reasons_item_data.to_dict()
                access_reasons.append(access_reasons_item)

        correction_of: str | Unset | None
        if isinstance(self.correction_of, Unset):
            correction_of = UNSET
        else:
            correction_of = self.correction_of

        more_access_reasons = self.more_access_reasons

        owner_name: str | Unset | None
        if isinstance(self.owner_name, Unset):
            owner_name = UNSET
        else:
            owner_name = self.owner_name

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
                "text": text,
                "timezone": timezone,
                "title": title,
            }
        )
        if access_reasons is not UNSET:
            field_dict["access_reasons"] = access_reasons
        if correction_of is not UNSET:
            field_dict["correction_of"] = correction_of
        if more_access_reasons is not UNSET:
            field_dict["more_access_reasons"] = more_access_reasons
        if owner_name is not UNSET:
            field_dict["owner_name"] = owner_name
        if publication_source_id is not UNSET:
            field_dict["publication_source_id"] = publication_source_id
        if shared is not UNSET:
            field_dict["shared"] = shared
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.document_access_reason import DocumentAccessReason

        d = dict(src_dict)
        activity_date = datetime.date.fromisoformat(d.pop("activity_date"))

        description = d.pop("description")

        id = d.pop("id")

        kind = DocumentKind(d.pop("kind"))

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

        state = DocumentState(d.pop("state"))

        text = d.pop("text")

        timezone = d.pop("timezone")

        title = d.pop("title")

        _access_reasons = d.pop("access_reasons", UNSET)
        access_reasons: list[DocumentAccessReason] | Unset = UNSET
        if _access_reasons is not UNSET:
            access_reasons = []
            for access_reasons_item_data in _access_reasons:
                access_reasons_item = DocumentAccessReason.from_dict(access_reasons_item_data)

                access_reasons.append(access_reasons_item)

        def _parse_correction_of(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        correction_of = _parse_correction_of(d.pop("correction_of", UNSET))

        more_access_reasons = d.pop("more_access_reasons", UNSET)

        def _parse_owner_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        owner_name = _parse_owner_name(d.pop("owner_name", UNSET))

        def _parse_publication_source_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        publication_source_id = _parse_publication_source_id(d.pop("publication_source_id", UNSET))

        shared = d.pop("shared", UNSET)

        version = d.pop("version", UNSET)

        document = cls(
            activity_date=activity_date,
            description=description,
            id=id,
            kind=kind,
            path=path,
            saved_at=saved_at,
            scope_id=scope_id,
            state=state,
            text=text,
            timezone=timezone,
            title=title,
            access_reasons=access_reasons,
            correction_of=correction_of,
            more_access_reasons=more_access_reasons,
            owner_name=owner_name,
            publication_source_id=publication_source_id,
            shared=shared,
            version=version,
        )

        return document
