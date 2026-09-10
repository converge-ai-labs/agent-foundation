from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="CompleteConnectorSetupRequest")


@_attrs_define(repr=False)
class CompleteConnectorSetupRequest:
    """
    Attributes:
        attempt_id (str):
        browser_nonce (str):
        session_uri (str):
    """

    attempt_id: str
    browser_nonce: str
    session_uri: str

    def to_dict(self) -> dict[str, Any]:
        attempt_id = self.attempt_id

        browser_nonce = self.browser_nonce

        session_uri = self.session_uri

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "attempt_id": attempt_id,
                "browser_nonce": browser_nonce,
                "session_uri": session_uri,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        attempt_id = d.pop("attempt_id")

        browser_nonce = d.pop("browser_nonce")

        session_uri = d.pop("session_uri")

        complete_connector_setup_request = cls(
            attempt_id=attempt_id,
            browser_nonce=browser_nonce,
            session_uri=session_uri,
        )

        return complete_connector_setup_request
