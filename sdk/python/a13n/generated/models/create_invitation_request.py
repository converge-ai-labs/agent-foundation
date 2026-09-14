from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.grant import Grant


T = TypeVar("T", bound="CreateInvitationRequest")


@_attrs_define(repr=False)
class CreateInvitationRequest:
    """
    Attributes:
        email (str):
        grants (list[Grant]):
    """

    email: str
    grants: list[Grant]

    def to_dict(self) -> dict[str, Any]:
        email = self.email

        grants = []
        for grants_item_data in self.grants:
            grants_item = grants_item_data.to_dict()
            grants.append(grants_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "email": email,
                "grants": grants,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.grant import Grant

        d = dict(src_dict)
        email = d.pop("email")

        grants = []
        _grants = d.pop("grants")
        for grants_item_data in _grants:
            grants_item = Grant.from_dict(grants_item_data)

            grants.append(grants_item)

        create_invitation_request = cls(
            email=email,
            grants=grants,
        )

        return create_invitation_request
