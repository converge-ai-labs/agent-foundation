from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.authorization_action_type import AuthorizationActionType
from ..types import UNSET, Unset

T = TypeVar("T", bound="AuthorizationAction")


@_attrs_define(repr=False)
class AuthorizationAction:
    """
    Attributes:
        type_ (AuthorizationActionType):
        url (None | str | Unset):
    """

    type_: AuthorizationActionType
    url: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        type_ = self.type_.value

        url: str | Unset | None
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "type": type_,
            }
        )
        if url is not UNSET:
            field_dict["url"] = url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        type_ = AuthorizationActionType(d.pop("type"))

        def _parse_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        url = _parse_url(d.pop("url", UNSET))

        authorization_action = cls(
            type_=type_,
            url=url,
        )

        return authorization_action
