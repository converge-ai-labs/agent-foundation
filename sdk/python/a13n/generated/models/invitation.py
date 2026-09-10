from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.grant import Grant


T = TypeVar("T", bound="Invitation")


@_attrs_define(repr=False)
class Invitation:
    """
    Attributes:
        accepted_at (datetime.datetime | None):
        created_at (datetime.datetime):
        created_by_user_id (None | str):
        email (str):
        expires_at (datetime.datetime):
        grants (list[Grant]):
        id (str):
        organization_id (str):
        revoked_at (datetime.datetime | None):
        updated_at (datetime.datetime):
        verification_mode (str):
        version (int):
    """

    accepted_at: datetime.datetime | None
    created_at: datetime.datetime
    created_by_user_id: str | None
    email: str
    expires_at: datetime.datetime
    grants: list[Grant]
    id: str
    organization_id: str
    revoked_at: datetime.datetime | None
    updated_at: datetime.datetime
    verification_mode: str
    version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        accepted_at: str | None
        if isinstance(self.accepted_at, datetime.datetime):
            accepted_at = self.accepted_at.isoformat()
        else:
            accepted_at = self.accepted_at

        created_at = self.created_at.isoformat()

        created_by_user_id: str | None
        created_by_user_id = self.created_by_user_id

        email = self.email

        expires_at = self.expires_at.isoformat()

        grants = []
        for grants_item_data in self.grants:
            grants_item = grants_item_data.to_dict()
            grants.append(grants_item)

        id = self.id

        organization_id = self.organization_id

        revoked_at: str | None
        if isinstance(self.revoked_at, datetime.datetime):
            revoked_at = self.revoked_at.isoformat()
        else:
            revoked_at = self.revoked_at

        updated_at = self.updated_at.isoformat()

        verification_mode = self.verification_mode

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "accepted_at": accepted_at,
                "created_at": created_at,
                "created_by_user_id": created_by_user_id,
                "email": email,
                "expires_at": expires_at,
                "grants": grants,
                "id": id,
                "organization_id": organization_id,
                "revoked_at": revoked_at,
                "updated_at": updated_at,
                "verification_mode": verification_mode,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.grant import Grant

        d = dict(src_dict)

        def _parse_accepted_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                accepted_at_type_0 = datetime.datetime.fromisoformat(data)

                return accepted_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        accepted_at = _parse_accepted_at(d.pop("accepted_at"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_created_by_user_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        created_by_user_id = _parse_created_by_user_id(d.pop("created_by_user_id"))

        email = d.pop("email")

        expires_at = datetime.datetime.fromisoformat(d.pop("expires_at"))

        grants = []
        _grants = d.pop("grants")
        for grants_item_data in _grants:
            grants_item = Grant.from_dict(grants_item_data)

            grants.append(grants_item)

        id = d.pop("id")

        organization_id = d.pop("organization_id")

        def _parse_revoked_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                revoked_at_type_0 = datetime.datetime.fromisoformat(data)

                return revoked_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        revoked_at = _parse_revoked_at(d.pop("revoked_at"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        verification_mode = d.pop("verification_mode")

        version = d.pop("version")

        invitation = cls(
            accepted_at=accepted_at,
            created_at=created_at,
            created_by_user_id=created_by_user_id,
            email=email,
            expires_at=expires_at,
            grants=grants,
            id=id,
            organization_id=organization_id,
            revoked_at=revoked_at,
            updated_at=updated_at,
            verification_mode=verification_mode,
            version=version,
        )

        invitation.additional_properties = d
        return invitation

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
