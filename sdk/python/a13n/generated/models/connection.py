from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.connection_status import ConnectionStatus
from ..models.connection_status_reason import ConnectionStatusReason
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connection_check import ConnectionCheck
    from ..models.connection_safe_metadata import ConnectionSafeMetadata
    from ..models.connector_source import ConnectorSource
    from ..models.mcp_source import MCPSource
    from ..models.principal_ref import PrincipalRef


T = TypeVar("T", bound="Connection")


@_attrs_define(repr=False)
class Connection:
    """
    Attributes:
        authorization_generation (int):
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        credential_configured (bool):
        id (str):
        name (str):
        organization_id (str):
        source (ConnectorSource | MCPSource):
        status (ConnectionStatus):
        updated_at (datetime.datetime):
        version (int):
        workspace_id (str):
        last_check (ConnectionCheck | None | Unset):
        safe_metadata (ConnectionSafeMetadata | Unset):
        status_reason (ConnectionStatusReason | None | Unset):
    """

    authorization_generation: int
    created_at: datetime.datetime
    created_by: PrincipalRef
    credential_configured: bool
    id: str
    name: str
    organization_id: str
    source: ConnectorSource | MCPSource
    status: ConnectionStatus
    updated_at: datetime.datetime
    version: int
    workspace_id: str
    last_check: ConnectionCheck | Unset | None = UNSET
    safe_metadata: ConnectionSafeMetadata | Unset = UNSET
    status_reason: ConnectionStatusReason | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.connection_check import ConnectionCheck
        from ..models.connector_source import ConnectorSource

        authorization_generation = self.authorization_generation

        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        credential_configured = self.credential_configured

        id = self.id

        name = self.name

        organization_id = self.organization_id

        source: dict[str, Any]
        if isinstance(self.source, ConnectorSource):
            source = self.source.to_dict()
        else:
            source = self.source.to_dict()

        status = self.status.value

        updated_at = self.updated_at.isoformat()

        version = self.version

        workspace_id = self.workspace_id

        last_check: dict[str, Any] | Unset | None
        if isinstance(self.last_check, Unset):
            last_check = UNSET
        elif isinstance(self.last_check, ConnectionCheck):
            last_check = self.last_check.to_dict()
        else:
            last_check = self.last_check

        safe_metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.safe_metadata, Unset):
            safe_metadata = self.safe_metadata.to_dict()

        status_reason: str | Unset | None
        if isinstance(self.status_reason, Unset):
            status_reason = UNSET
        elif isinstance(self.status_reason, ConnectionStatusReason):
            status_reason = self.status_reason.value
        else:
            status_reason = self.status_reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "authorization_generation": authorization_generation,
                "created_at": created_at,
                "created_by": created_by,
                "credential_configured": credential_configured,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "source": source,
                "status": status,
                "updated_at": updated_at,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if last_check is not UNSET:
            field_dict["last_check"] = last_check
        if safe_metadata is not UNSET:
            field_dict["safe_metadata"] = safe_metadata
        if status_reason is not UNSET:
            field_dict["status_reason"] = status_reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connection_check import ConnectionCheck
        from ..models.connection_safe_metadata import ConnectionSafeMetadata
        from ..models.connector_source import ConnectorSource
        from ..models.mcp_source import MCPSource
        from ..models.principal_ref import PrincipalRef

        d = dict(src_dict)
        authorization_generation = d.pop("authorization_generation")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        credential_configured = d.pop("credential_configured")

        id = d.pop("id")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        def _parse_source(data: object) -> ConnectorSource | MCPSource:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_type_0 = ConnectorSource.from_dict(data)

                return source_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            source_type_1 = MCPSource.from_dict(data)

            return source_type_1

        source = _parse_source(d.pop("source"))

        status = ConnectionStatus(d.pop("status"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        def _parse_last_check(data: object) -> ConnectionCheck | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                last_check_type_0 = ConnectionCheck.from_dict(data)

                return last_check_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConnectionCheck | Unset | None, data)

        last_check = _parse_last_check(d.pop("last_check", UNSET))

        _safe_metadata = d.pop("safe_metadata", UNSET)
        safe_metadata: ConnectionSafeMetadata | Unset
        if isinstance(_safe_metadata, Unset):
            safe_metadata = UNSET
        else:
            safe_metadata = ConnectionSafeMetadata.from_dict(_safe_metadata)

        def _parse_status_reason(data: object) -> ConnectionStatusReason | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                status_reason_type_0 = ConnectionStatusReason(data)

                return status_reason_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConnectionStatusReason | Unset | None, data)

        status_reason = _parse_status_reason(d.pop("status_reason", UNSET))

        connection = cls(
            authorization_generation=authorization_generation,
            created_at=created_at,
            created_by=created_by,
            credential_configured=credential_configured,
            id=id,
            name=name,
            organization_id=organization_id,
            source=source,
            status=status,
            updated_at=updated_at,
            version=version,
            workspace_id=workspace_id,
            last_check=last_check,
            safe_metadata=safe_metadata,
            status_reason=status_reason,
        )

        return connection
