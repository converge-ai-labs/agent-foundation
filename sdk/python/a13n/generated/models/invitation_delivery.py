from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.invitation_delivery_delivery import InvitationDeliveryDelivery
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.invitation import Invitation


T = TypeVar("T", bound="InvitationDelivery")


@_attrs_define(repr=False)
class InvitationDelivery:
    """
    Attributes:
        delivery (InvitationDeliveryDelivery):
        invitation (Invitation):
        invitation_url (None | str | Unset):
    """

    delivery: InvitationDeliveryDelivery
    invitation: Invitation
    invitation_url: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        delivery = self.delivery.value

        invitation = self.invitation.to_dict()

        invitation_url: str | Unset | None
        if isinstance(self.invitation_url, Unset):
            invitation_url = UNSET
        else:
            invitation_url = self.invitation_url

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "delivery": delivery,
                "invitation": invitation,
            }
        )
        if invitation_url is not UNSET:
            field_dict["invitation_url"] = invitation_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.invitation import Invitation

        d = dict(src_dict)
        delivery = InvitationDeliveryDelivery(d.pop("delivery"))

        invitation = Invitation.from_dict(d.pop("invitation"))

        def _parse_invitation_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        invitation_url = _parse_invitation_url(d.pop("invitation_url", UNSET))

        invitation_delivery = cls(
            delivery=delivery,
            invitation=invitation,
            invitation_url=invitation_url,
        )

        return invitation_delivery
