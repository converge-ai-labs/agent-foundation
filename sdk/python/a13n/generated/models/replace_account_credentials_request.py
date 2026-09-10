from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.replace_account_credentials_request_credentials import ReplaceAccountCredentialsRequestCredentials


T = TypeVar("T", bound="ReplaceAccountCredentialsRequest")


@_attrs_define(repr=False)
class ReplaceAccountCredentialsRequest:
    """
    Attributes:
        credentials (ReplaceAccountCredentialsRequestCredentials):
        expected_version (int):
    """

    credentials: ReplaceAccountCredentialsRequestCredentials
    expected_version: int

    def to_dict(self) -> dict[str, Any]:
        credentials = self.credentials.to_dict()

        expected_version = self.expected_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "credentials": credentials,
                "expected_version": expected_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.replace_account_credentials_request_credentials import (
            ReplaceAccountCredentialsRequestCredentials,
        )

        d = dict(src_dict)
        credentials = ReplaceAccountCredentialsRequestCredentials.from_dict(d.pop("credentials"))

        expected_version = d.pop("expected_version")

        replace_account_credentials_request = cls(
            credentials=credentials,
            expected_version=expected_version,
        )

        return replace_account_credentials_request
