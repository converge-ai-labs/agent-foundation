from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.auth_session import AuthSession
    from ..models.user import User


T = TypeVar("T", bound="LoginResult")


@_attrs_define(repr=False)
class LoginResult:
    """
    Attributes:
        csrf_token (str):
        session (AuthSession):
        user (User):
    """

    csrf_token: str
    session: AuthSession
    user: User
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        csrf_token = self.csrf_token

        session = self.session.to_dict()

        user = self.user.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "csrf_token": csrf_token,
                "session": session,
                "user": user,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.auth_session import AuthSession
        from ..models.user import User

        d = dict(src_dict)
        csrf_token = d.pop("csrf_token")

        session = AuthSession.from_dict(d.pop("session"))

        user = User.from_dict(d.pop("user"))

        login_result = cls(
            csrf_token=csrf_token,
            session=session,
            user=user,
        )

        login_result.additional_properties = d
        return login_result

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
