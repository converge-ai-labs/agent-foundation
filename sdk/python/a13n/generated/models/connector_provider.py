from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.connector_provider_status import ConnectorProviderStatus

if TYPE_CHECKING:
    from ..models.connector_provider_configuration import ConnectorProviderConfiguration
    from ..models.principal_ref import PrincipalRef


T = TypeVar("T", bound="ConnectorProvider")


@_attrs_define(repr=False)
class ConnectorProvider:
    """
    Attributes:
        configuration (ConnectorProviderConfiguration):
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        credential_configured (bool):
        credential_generation (int):
        id (str):
        name (str):
        organization_id (str):
        status (ConnectorProviderStatus):
        type_ (str):
        updated_at (datetime.datetime):
        version (int):
        workspace_id (None | str):
    """

    configuration: ConnectorProviderConfiguration
    created_at: datetime.datetime
    created_by: PrincipalRef
    credential_configured: bool
    credential_generation: int
    id: str
    name: str
    organization_id: str
    status: ConnectorProviderStatus
    type_: str
    updated_at: datetime.datetime
    version: int
    workspace_id: str | None

    def to_dict(self) -> dict[str, Any]:
        configuration = self.configuration.to_dict()

        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        credential_configured = self.credential_configured

        credential_generation = self.credential_generation

        id = self.id

        name = self.name

        organization_id = self.organization_id

        status = self.status.value

        type_ = self.type_

        updated_at = self.updated_at.isoformat()

        version = self.version

        workspace_id: str | None
        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration": configuration,
                "created_at": created_at,
                "created_by": created_by,
                "credential_configured": credential_configured,
                "credential_generation": credential_generation,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "status": status,
                "type": type_,
                "updated_at": updated_at,
                "version": version,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_provider_configuration import ConnectorProviderConfiguration
        from ..models.principal_ref import PrincipalRef

        d = dict(src_dict)
        configuration = ConnectorProviderConfiguration.from_dict(d.pop("configuration"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        credential_configured = d.pop("credential_configured")

        credential_generation = d.pop("credential_generation")

        id = d.pop("id")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        status = ConnectorProviderStatus(d.pop("status"))

        type_ = d.pop("type")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        def _parse_workspace_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        workspace_id = _parse_workspace_id(d.pop("workspace_id"))

        connector_provider = cls(
            configuration=configuration,
            created_at=created_at,
            created_by=created_by,
            credential_configured=credential_configured,
            credential_generation=credential_generation,
            id=id,
            name=name,
            organization_id=organization_id,
            status=status,
            type_=type_,
            updated_at=updated_at,
            version=version,
            workspace_id=workspace_id,
        )

        return connector_provider
