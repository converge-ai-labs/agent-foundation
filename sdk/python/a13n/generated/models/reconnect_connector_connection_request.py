from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.reconnect_connector_connection_request_setup import ReconnectConnectorConnectionRequestSetup


T = TypeVar("T", bound="ReconnectConnectorConnectionRequest")


@_attrs_define(repr=False)
class ReconnectConnectorConnectionRequest:
    """
    Attributes:
        expected_version (int):
        return_path (str):
        setup (ReconnectConnectorConnectionRequestSetup):
        browser_nonce (None | str | Unset):
    """

    expected_version: int
    return_path: str
    setup: ReconnectConnectorConnectionRequestSetup
    browser_nonce: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        return_path = self.return_path

        setup = self.setup.to_dict()

        browser_nonce: str | Unset | None
        if isinstance(self.browser_nonce, Unset):
            browser_nonce = UNSET
        else:
            browser_nonce = self.browser_nonce

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "return_path": return_path,
                "setup": setup,
            }
        )
        if browser_nonce is not UNSET:
            field_dict["browser_nonce"] = browser_nonce

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.reconnect_connector_connection_request_setup import (
            ReconnectConnectorConnectionRequestSetup,
        )

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        return_path = d.pop("return_path")

        setup = ReconnectConnectorConnectionRequestSetup.from_dict(d.pop("setup"))

        def _parse_browser_nonce(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        browser_nonce = _parse_browser_nonce(d.pop("browser_nonce", UNSET))

        reconnect_connector_connection_request = cls(
            expected_version=expected_version,
            return_path=return_path,
            setup=setup,
            browser_nonce=browser_nonce,
        )

        return reconnect_connector_connection_request
