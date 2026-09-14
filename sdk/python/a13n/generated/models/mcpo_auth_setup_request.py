from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="MCPOAuthSetupRequest")


@_attrs_define(repr=False)
class MCPOAuthSetupRequest:
    """
    Attributes:
        redirect_uri (None | str | Unset):
    """

    redirect_uri: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        redirect_uri: str | Unset | None
        if isinstance(self.redirect_uri, Unset):
            redirect_uri = UNSET
        else:
            redirect_uri = self.redirect_uri

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if redirect_uri is not UNSET:
            field_dict["redirect_uri"] = redirect_uri

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_redirect_uri(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        redirect_uri = _parse_redirect_uri(d.pop("redirect_uri", UNSET))

        mcpo_auth_setup_request = cls(
            redirect_uri=redirect_uri,
        )

        return mcpo_auth_setup_request
