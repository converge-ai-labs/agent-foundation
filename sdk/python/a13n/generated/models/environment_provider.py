from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.environment_provider_configuration import EnvironmentProviderConfiguration


T = TypeVar("T", bound="EnvironmentProvider")


@_attrs_define(repr=False)
class EnvironmentProvider:
    """
    Attributes:
        configuration (EnvironmentProviderConfiguration):
        created_at (datetime.datetime):
        credential_configured (bool):
        enabled (bool):
        id (str):
        name (str):
        organization_id (str):
        type_ (str):
        updated_at (datetime.datetime):
        workspace_id (None | str):
    """

    configuration: EnvironmentProviderConfiguration
    created_at: datetime.datetime
    credential_configured: bool
    enabled: bool
    id: str
    name: str
    organization_id: str
    type_: str
    updated_at: datetime.datetime
    workspace_id: str | None

    def to_dict(self) -> dict[str, Any]:
        configuration = self.configuration.to_dict()

        created_at = self.created_at.isoformat()

        credential_configured = self.credential_configured

        enabled = self.enabled

        id = self.id

        name = self.name

        organization_id = self.organization_id

        type_ = self.type_

        updated_at = self.updated_at.isoformat()

        workspace_id: str | None
        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration": configuration,
                "created_at": created_at,
                "credential_configured": credential_configured,
                "enabled": enabled,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "type": type_,
                "updated_at": updated_at,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.environment_provider_configuration import EnvironmentProviderConfiguration

        d = dict(src_dict)
        configuration = EnvironmentProviderConfiguration.from_dict(d.pop("configuration"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        credential_configured = d.pop("credential_configured")

        enabled = d.pop("enabled")

        id = d.pop("id")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        type_ = d.pop("type")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        def _parse_workspace_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        workspace_id = _parse_workspace_id(d.pop("workspace_id"))

        environment_provider = cls(
            configuration=configuration,
            created_at=created_at,
            credential_configured=credential_configured,
            enabled=enabled,
            id=id,
            name=name,
            organization_id=organization_id,
            type_=type_,
            updated_at=updated_at,
            workspace_id=workspace_id,
        )

        return environment_provider
