from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="MCPAuthorizationLaunch")


@_attrs_define(repr=False)
class MCPAuthorizationLaunch:
    """
    Attributes:
        authorization_url (str):
        expires_at (datetime.datetime):
        id (str):
        status (Literal['pending'] | Unset):
    """

    authorization_url: str
    expires_at: datetime.datetime
    id: str
    status: Literal["pending"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        authorization_url = self.authorization_url

        expires_at = self.expires_at.isoformat()

        id = self.id

        status = self.status

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "authorization_url": authorization_url,
                "expires_at": expires_at,
                "id": id,
            }
        )
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        authorization_url = d.pop("authorization_url")

        expires_at = datetime.datetime.fromisoformat(d.pop("expires_at"))

        id = d.pop("id")

        status = cast(Literal["pending"] | Unset, d.pop("status", UNSET))
        if status != "pending" and not isinstance(status, Unset):
            raise ValueError(f"status must match const 'pending', got '{status}'")

        mcp_authorization_launch = cls(
            authorization_url=authorization_url,
            expires_at=expires_at,
            id=id,
            status=status,
        )

        return mcp_authorization_launch
