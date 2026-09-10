from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.hook_subscription_revision import HookSubscriptionRevision
    from ..models.principal_ref import PrincipalRef


T = TypeVar("T", bound="HookSubscription")


@_attrs_define(repr=False)
class HookSubscription:
    """
    Attributes:
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        current_revision (HookSubscriptionRevision):
        current_revision_id (str):
        enabled (bool):
        id (str):
        updated_at (datetime.datetime):
        updated_by (PrincipalRef):
        version (int):
        workspace_id (str):
        deleted_at (datetime.datetime | None | Unset):
        expired_at (datetime.datetime | None | Unset):
        inline_run_id (None | str | Unset):
    """

    created_at: datetime.datetime
    created_by: PrincipalRef
    current_revision: HookSubscriptionRevision
    current_revision_id: str
    enabled: bool
    id: str
    updated_at: datetime.datetime
    updated_by: PrincipalRef
    version: int
    workspace_id: str
    deleted_at: datetime.datetime | Unset | None = UNSET
    expired_at: datetime.datetime | Unset | None = UNSET
    inline_run_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        current_revision = self.current_revision.to_dict()

        current_revision_id = self.current_revision_id

        enabled = self.enabled

        id = self.id

        updated_at = self.updated_at.isoformat()

        updated_by = self.updated_by.to_dict()

        version = self.version

        workspace_id = self.workspace_id

        deleted_at: str | Unset | None
        if isinstance(self.deleted_at, Unset):
            deleted_at = UNSET
        elif isinstance(self.deleted_at, datetime.datetime):
            deleted_at = self.deleted_at.isoformat()
        else:
            deleted_at = self.deleted_at

        expired_at: str | Unset | None
        if isinstance(self.expired_at, Unset):
            expired_at = UNSET
        elif isinstance(self.expired_at, datetime.datetime):
            expired_at = self.expired_at.isoformat()
        else:
            expired_at = self.expired_at

        inline_run_id: str | Unset | None
        if isinstance(self.inline_run_id, Unset):
            inline_run_id = UNSET
        else:
            inline_run_id = self.inline_run_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "created_at": created_at,
                "created_by": created_by,
                "current_revision": current_revision,
                "current_revision_id": current_revision_id,
                "enabled": enabled,
                "id": id,
                "updated_at": updated_at,
                "updated_by": updated_by,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if deleted_at is not UNSET:
            field_dict["deleted_at"] = deleted_at
        if expired_at is not UNSET:
            field_dict["expired_at"] = expired_at
        if inline_run_id is not UNSET:
            field_dict["inline_run_id"] = inline_run_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.hook_subscription_revision import HookSubscriptionRevision
        from ..models.principal_ref import PrincipalRef

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        current_revision = HookSubscriptionRevision.from_dict(d.pop("current_revision"))

        current_revision_id = d.pop("current_revision_id")

        enabled = d.pop("enabled")

        id = d.pop("id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        updated_by = PrincipalRef.from_dict(d.pop("updated_by"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        def _parse_deleted_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                deleted_at_type_0 = datetime.datetime.fromisoformat(data)

                return deleted_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | Unset | None, data)

        deleted_at = _parse_deleted_at(d.pop("deleted_at", UNSET))

        def _parse_expired_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                expired_at_type_0 = datetime.datetime.fromisoformat(data)

                return expired_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | Unset | None, data)

        expired_at = _parse_expired_at(d.pop("expired_at", UNSET))

        def _parse_inline_run_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        inline_run_id = _parse_inline_run_id(d.pop("inline_run_id", UNSET))

        hook_subscription = cls(
            created_at=created_at,
            created_by=created_by,
            current_revision=current_revision,
            current_revision_id=current_revision_id,
            enabled=enabled,
            id=id,
            updated_at=updated_at,
            updated_by=updated_by,
            version=version,
            workspace_id=workspace_id,
            deleted_at=deleted_at,
            expired_at=expired_at,
            inline_run_id=inline_run_id,
        )

        return hook_subscription
