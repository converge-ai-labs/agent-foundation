from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..models.connector_provider_test_result_verified_access_item import ConnectorProviderTestResultVerifiedAccessItem
from ..types import UNSET, Unset

T = TypeVar("T", bound="ConnectorProviderTestResult")


@_attrs_define(repr=False)
class ConnectorProviderTestResult:
    """
    Attributes:
        connector_provider_id (str):
        connector_provider_version (int):
        tested_at (datetime.datetime):
        verified_access (list[ConnectorProviderTestResultVerifiedAccessItem]):
        status (Literal['succeeded'] | Unset):
    """

    connector_provider_id: str
    connector_provider_version: int
    tested_at: datetime.datetime
    verified_access: list[ConnectorProviderTestResultVerifiedAccessItem]
    status: Literal["succeeded"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        connector_provider_id = self.connector_provider_id

        connector_provider_version = self.connector_provider_version

        tested_at = self.tested_at.isoformat()

        verified_access = []
        for verified_access_item_data in self.verified_access:
            verified_access_item = verified_access_item_data.value
            verified_access.append(verified_access_item)

        status = self.status

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connector_provider_id": connector_provider_id,
                "connector_provider_version": connector_provider_version,
                "tested_at": tested_at,
                "verified_access": verified_access,
            }
        )
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        connector_provider_id = d.pop("connector_provider_id")

        connector_provider_version = d.pop("connector_provider_version")

        tested_at = datetime.datetime.fromisoformat(d.pop("tested_at"))

        verified_access = []
        _verified_access = d.pop("verified_access")
        for verified_access_item_data in _verified_access:
            verified_access_item = ConnectorProviderTestResultVerifiedAccessItem(verified_access_item_data)

            verified_access.append(verified_access_item)

        status = cast(Literal["succeeded"] | Unset, d.pop("status", UNSET))
        if status != "succeeded" and not isinstance(status, Unset):
            raise ValueError(f"status must match const 'succeeded', got '{status}'")

        connector_provider_test_result = cls(
            connector_provider_id=connector_provider_id,
            connector_provider_version=connector_provider_version,
            tested_at=tested_at,
            verified_access=verified_access,
            status=status,
        )

        return connector_provider_test_result
