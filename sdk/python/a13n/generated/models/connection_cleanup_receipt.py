from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.connection_cleanup_receipt_local_status import ConnectionCleanupReceiptLocalStatus
from ..models.connection_cleanup_receipt_remote_status import ConnectionCleanupReceiptRemoteStatus

T = TypeVar("T", bound="ConnectionCleanupReceipt")


@_attrs_define(repr=False)
class ConnectionCleanupReceipt:
    """
    Attributes:
        connection_id (str):
        local_status (ConnectionCleanupReceiptLocalStatus):
        remote_status (ConnectionCleanupReceiptRemoteStatus):
    """

    connection_id: str
    local_status: ConnectionCleanupReceiptLocalStatus
    remote_status: ConnectionCleanupReceiptRemoteStatus

    def to_dict(self) -> dict[str, Any]:
        connection_id = self.connection_id

        local_status = self.local_status.value

        remote_status = self.remote_status.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connection_id": connection_id,
                "local_status": local_status,
                "remote_status": remote_status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        connection_id = d.pop("connection_id")

        local_status = ConnectionCleanupReceiptLocalStatus(d.pop("local_status"))

        remote_status = ConnectionCleanupReceiptRemoteStatus(d.pop("remote_status"))

        connection_cleanup_receipt = cls(
            connection_id=connection_id,
            local_status=local_status,
            remote_status=remote_status,
        )

        return connection_cleanup_receipt
