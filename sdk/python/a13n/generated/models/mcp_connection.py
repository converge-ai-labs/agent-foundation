from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.mcp_auth_mode import MCPAuthMode
from ..models.mcp_connection_status import MCPConnectionStatus
from ..models.mcp_connection_status_reason import MCPConnectionStatusReason

if TYPE_CHECKING:
    from ..models.principal_ref import PrincipalRef


T = TypeVar("T", bound="MCPConnection")


@_attrs_define(repr=False)
class MCPConnection:
    """
    Attributes:
        auth_mode (MCPAuthMode):
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        credential_configured (bool):
        credential_generation (int):
        endpoint_url (str):
        id (str):
        name (str):
        organization_id (str):
        static_header_names (list[str]):
        status (MCPConnectionStatus):
        status_reason (MCPConnectionStatusReason | None):
        updated_at (datetime.datetime):
        version (int):
        workspace_id (str):
    """

    auth_mode: MCPAuthMode
    created_at: datetime.datetime
    created_by: PrincipalRef
    credential_configured: bool
    credential_generation: int
    endpoint_url: str
    id: str
    name: str
    organization_id: str
    static_header_names: list[str]
    status: MCPConnectionStatus
    status_reason: MCPConnectionStatusReason | None
    updated_at: datetime.datetime
    version: int
    workspace_id: str

    def to_dict(self) -> dict[str, Any]:
        auth_mode = self.auth_mode.value

        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        credential_configured = self.credential_configured

        credential_generation = self.credential_generation

        endpoint_url = self.endpoint_url

        id = self.id

        name = self.name

        organization_id = self.organization_id

        static_header_names = self.static_header_names

        status = self.status.value

        status_reason: str | None
        if isinstance(self.status_reason, MCPConnectionStatusReason):
            status_reason = self.status_reason.value
        else:
            status_reason = self.status_reason

        updated_at = self.updated_at.isoformat()

        version = self.version

        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "auth_mode": auth_mode,
                "created_at": created_at,
                "created_by": created_by,
                "credential_configured": credential_configured,
                "credential_generation": credential_generation,
                "endpoint_url": endpoint_url,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "static_header_names": static_header_names,
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
        from ..models.principal_ref import PrincipalRef

        d = dict(src_dict)
        auth_mode = MCPAuthMode(d.pop("auth_mode"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        credential_configured = d.pop("credential_configured")

        credential_generation = d.pop("credential_generation")

        endpoint_url = d.pop("endpoint_url")

        id = d.pop("id")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        static_header_names = cast(list[str], d.pop("static_header_names"))

        status = MCPConnectionStatus(d.pop("status"))

        def _parse_status_reason(data: object) -> MCPConnectionStatusReason | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                status_reason_type_0 = MCPConnectionStatusReason(data)

                return status_reason_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MCPConnectionStatusReason | None, data)

        status_reason = _parse_status_reason(d.pop("status_reason"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        mcp_connection = cls(
            auth_mode=auth_mode,
            created_at=created_at,
            created_by=created_by,
            credential_configured=credential_configured,
            credential_generation=credential_generation,
            endpoint_url=endpoint_url,
            id=id,
            name=name,
            organization_id=organization_id,
            static_header_names=static_header_names,
            status=status,
            status_reason=status_reason,
            updated_at=updated_at,
            version=version,
            workspace_id=workspace_id,
        )

        return mcp_connection
