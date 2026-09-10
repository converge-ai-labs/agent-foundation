from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.replace_mcp_credentials_request_static_headers_type_0 import (
        ReplaceMCPCredentialsRequestStaticHeadersType0,
    )


T = TypeVar("T", bound="ReplaceMCPCredentialsRequest")


@_attrs_define(repr=False)
class ReplaceMCPCredentialsRequest:
    """
    Attributes:
        expected_version (int):
        bearer (None | str | Unset):
        static_headers (None | ReplaceMCPCredentialsRequestStaticHeadersType0 | Unset):
    """

    expected_version: int
    bearer: str | Unset | None = UNSET
    static_headers: ReplaceMCPCredentialsRequestStaticHeadersType0 | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.replace_mcp_credentials_request_static_headers_type_0 import (
            ReplaceMCPCredentialsRequestStaticHeadersType0,
        )

        expected_version = self.expected_version

        bearer: str | Unset | None
        if isinstance(self.bearer, Unset):
            bearer = UNSET
        else:
            bearer = self.bearer

        static_headers: dict[str, Any] | Unset | None
        if isinstance(self.static_headers, Unset):
            static_headers = UNSET
        elif isinstance(self.static_headers, ReplaceMCPCredentialsRequestStaticHeadersType0):
            static_headers = self.static_headers.to_dict()
        else:
            static_headers = self.static_headers

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
            }
        )
        if bearer is not UNSET:
            field_dict["bearer"] = bearer
        if static_headers is not UNSET:
            field_dict["static_headers"] = static_headers

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.replace_mcp_credentials_request_static_headers_type_0 import (
            ReplaceMCPCredentialsRequestStaticHeadersType0,
        )

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        def _parse_bearer(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        bearer = _parse_bearer(d.pop("bearer", UNSET))

        def _parse_static_headers(data: object) -> ReplaceMCPCredentialsRequestStaticHeadersType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                static_headers_type_0 = ReplaceMCPCredentialsRequestStaticHeadersType0.from_dict(data)

                return static_headers_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ReplaceMCPCredentialsRequestStaticHeadersType0 | Unset | None, data)

        static_headers = _parse_static_headers(d.pop("static_headers", UNSET))

        replace_mcp_credentials_request = cls(
            expected_version=expected_version,
            bearer=bearer,
            static_headers=static_headers,
        )

        return replace_mcp_credentials_request
