from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.create_document_kind import CreateDocumentKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="CreateDocument")


@_attrs_define(repr=False)
class CreateDocument:
    """
    Attributes:
        text (str):
        title (str):
        activity_date (datetime.date | None | Unset):
        correction_of (None | str | Unset):
        description (str | Unset):
        kind (CreateDocumentKind | Unset):
    """

    text: str
    title: str
    activity_date: datetime.date | Unset | None = UNSET
    correction_of: str | Unset | None = UNSET
    description: str | Unset = UNSET
    kind: CreateDocumentKind | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        text = self.text

        title = self.title

        activity_date: str | Unset | None
        if isinstance(self.activity_date, Unset):
            activity_date = UNSET
        elif isinstance(self.activity_date, datetime.date):
            activity_date = self.activity_date.isoformat()
        else:
            activity_date = self.activity_date

        correction_of: str | Unset | None
        if isinstance(self.correction_of, Unset):
            correction_of = UNSET
        else:
            correction_of = self.correction_of

        description = self.description

        kind: str | Unset = UNSET
        if not isinstance(self.kind, Unset):
            kind = self.kind.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "text": text,
                "title": title,
            }
        )
        if activity_date is not UNSET:
            field_dict["activity_date"] = activity_date
        if correction_of is not UNSET:
            field_dict["correction_of"] = correction_of
        if description is not UNSET:
            field_dict["description"] = description
        if kind is not UNSET:
            field_dict["kind"] = kind

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        text = d.pop("text")

        title = d.pop("title")

        def _parse_activity_date(data: object) -> datetime.date | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                activity_date_type_0 = datetime.date.fromisoformat(data)

                return activity_date_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | Unset | None, data)

        activity_date = _parse_activity_date(d.pop("activity_date", UNSET))

        def _parse_correction_of(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        correction_of = _parse_correction_of(d.pop("correction_of", UNSET))

        description = d.pop("description", UNSET)

        _kind = d.pop("kind", UNSET)
        kind: CreateDocumentKind | Unset
        if isinstance(_kind, Unset):
            kind = UNSET
        else:
            kind = CreateDocumentKind(_kind)

        create_document = cls(
            text=text,
            title=title,
            activity_date=activity_date,
            correction_of=correction_of,
            description=description,
            kind=kind,
        )

        return create_document
