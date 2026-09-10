from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.connector_connection_status import ConnectorConnectionStatus
from ..models.connector_connection_status_reason import ConnectorConnectionStatusReason

if TYPE_CHECKING:
    from ..models.connector_connection_safe_metadata import ConnectorConnectionSafeMetadata
    from ..models.principal_ref import PrincipalRef


T = TypeVar("T", bound="ConnectorConnection")


@_attrs_define(repr=False)
class ConnectorConnection:
    """
    Attributes:
        connector_key (str):
        connector_provider_id (str):
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        id (str):
        name (str):
        organization_id (str):
        safe_metadata (ConnectorConnectionSafeMetadata):
        status (ConnectorConnectionStatus):
        status_reason (ConnectorConnectionStatusReason | None):
        updated_at (datetime.datetime):
        version (int):
        workspace_id (str):
    """

    connector_key: str
    connector_provider_id: str
    created_at: datetime.datetime
    created_by: PrincipalRef
    id: str
    name: str
    organization_id: str
    safe_metadata: ConnectorConnectionSafeMetadata
    status: ConnectorConnectionStatus
    status_reason: ConnectorConnectionStatusReason | None
    updated_at: datetime.datetime
    version: int
    workspace_id: str

    def to_dict(self) -> dict[str, Any]:
        connector_key = self.connector_key

        connector_provider_id = self.connector_provider_id

        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        id = self.id

        name = self.name

        organization_id = self.organization_id

        safe_metadata = self.safe_metadata.to_dict()

        status = self.status.value

        status_reason: str | None
        if isinstance(self.status_reason, ConnectorConnectionStatusReason):
            status_reason = self.status_reason.value
        else:
            status_reason = self.status_reason

        updated_at = self.updated_at.isoformat()

        version = self.version

        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connector_key": connector_key,
                "connector_provider_id": connector_provider_id,
                "created_at": created_at,
                "created_by": created_by,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "safe_metadata": safe_metadata,
                "status": status,
                "status_reason": status_reason,
                "updated_at": updated_at,
                "version": version,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_connection_safe_metadata import ConnectorConnectionSafeMetadata
        from ..models.principal_ref import PrincipalRef

        d = dict(src_dict)
        connector_key = d.pop("connector_key")

        connector_provider_id = d.pop("connector_provider_id")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        id = d.pop("id")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        safe_metadata = ConnectorConnectionSafeMetadata.from_dict(d.pop("safe_metadata"))

        status = ConnectorConnectionStatus(d.pop("status"))

        def _parse_status_reason(data: object) -> ConnectorConnectionStatusReason | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                status_reason_type_0 = ConnectorConnectionStatusReason(data)

                return status_reason_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConnectorConnectionStatusReason | None, data)

        status_reason = _parse_status_reason(d.pop("status_reason"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        connector_connection = cls(
            connector_key=connector_key,
            connector_provider_id=connector_provider_id,
            created_at=created_at,
            created_by=created_by,
            id=id,
            name=name,
            organization_id=organization_id,
            safe_metadata=safe_metadata,
            status=status,
            status_reason=status_reason,
            updated_at=updated_at,
            version=version,
            workspace_id=workspace_id,
        )

        return connector_connection
