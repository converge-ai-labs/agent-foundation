from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="LaunchAuthorizationRequest")


@_attrs_define(repr=False)
class LaunchAuthorizationRequest:
    """
    Attributes:
        browser_nonce (str):
        token (str):
    """

    browser_nonce: str
    token: str

    def to_dict(self) -> dict[str, Any]:
        browser_nonce = self.browser_nonce

        token = self.token

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "browser_nonce": browser_nonce,
                "token": token,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        browser_nonce = d.pop("browser_nonce")

        token = d.pop("token")

        launch_authorization_request = cls(
            browser_nonce=browser_nonce,
            token=token,
        )

        return launch_authorization_request
