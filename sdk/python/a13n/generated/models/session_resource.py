from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.session_preview import SessionPreview


T = TypeVar("T", bound="SessionResource")


@_attrs_define(repr=False)
class SessionResource:
    """
    Attributes:
        created_at (datetime.datetime):
        id (str):
        preview (None | SessionPreview):
        run_count (int | None):
        updated_at (datetime.datetime):
        workspace_id (str):
    """

    created_at: datetime.datetime
    id: str
    preview: SessionPreview | None
    run_count: int | None
    updated_at: datetime.datetime
    workspace_id: str

    def to_dict(self) -> dict[str, Any]:
        from ..models.session_preview import SessionPreview

        created_at = self.created_at.isoformat()

        id = self.id

        preview: dict[str, Any] | None
        if isinstance(self.preview, SessionPreview):
            preview = self.preview.to_dict()
        else:
            preview = self.preview

        run_count: int | None
        run_count = self.run_count

        updated_at = self.updated_at.isoformat()

        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "preview": preview,
                "run_count": run_count,
                "updated_at": updated_at,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.session_preview import SessionPreview

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        def _parse_preview(data: object) -> SessionPreview | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                preview_type_0 = SessionPreview.from_dict(data)

                return preview_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(SessionPreview | None, data)

        preview = _parse_preview(d.pop("preview"))

        def _parse_run_count(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        run_count = _parse_run_count(d.pop("run_count"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        workspace_id = d.pop("workspace_id")

        session_resource = cls(
            created_at=created_at,
            id=id,
            preview=preview,
            run_count=run_count,
            updated_at=updated_at,
            workspace_id=workspace_id,
        )

        return session_resource
