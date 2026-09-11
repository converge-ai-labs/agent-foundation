from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_provider_configuration import ModelProviderConfiguration
    from ..models.principal_ref import PrincipalRef


T = TypeVar("T", bound="ModelProvider")


@_attrs_define(repr=False)
class ModelProvider:
    """
    Attributes:
        configuration (ModelProviderConfiguration):
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        credential_configured (bool):
        enabled (bool):
        id (str):
        name (str):
        organization_id (str):
        type_ (str):
        updated_at (datetime.datetime):
        updated_by (PrincipalRef):
        workspace_id (None | str):
        header_names (list[str] | Unset):
    """

    configuration: ModelProviderConfiguration
    created_at: datetime.datetime
    created_by: PrincipalRef
    credential_configured: bool
    enabled: bool
    id: str
    name: str
    organization_id: str
    type_: str
    updated_at: datetime.datetime
    updated_by: PrincipalRef
    workspace_id: str | None
    header_names: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        configuration = self.configuration.to_dict()

        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        credential_configured = self.credential_configured

        enabled = self.enabled

        id = self.id

        name = self.name

        organization_id = self.organization_id

        type_ = self.type_

        updated_at = self.updated_at.isoformat()

        updated_by = self.updated_by.to_dict()

        workspace_id: str | None
        workspace_id = self.workspace_id

        header_names: list[str] | Unset = UNSET
        if not isinstance(self.header_names, Unset):
            header_names = self.header_names

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration": configuration,
                "created_at": created_at,
                "created_by": created_by,
                "credential_configured": credential_configured,
                "enabled": enabled,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "type": type_,
                "updated_at": updated_at,
                "updated_by": updated_by,
                "workspace_id": workspace_id,
            }
        )
        if header_names is not UNSET:
            field_dict["header_names"] = header_names

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_provider_configuration import ModelProviderConfiguration
        from ..models.principal_ref import PrincipalRef

        d = dict(src_dict)
        configuration = ModelProviderConfiguration.from_dict(d.pop("configuration"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        credential_configured = d.pop("credential_configured")

        enabled = d.pop("enabled")

        id = d.pop("id")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        type_ = d.pop("type")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        updated_by = PrincipalRef.from_dict(d.pop("updated_by"))

        def _parse_workspace_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        workspace_id = _parse_workspace_id(d.pop("workspace_id"))

        header_names = cast(list[str], d.pop("header_names", UNSET))

        model_provider = cls(
            configuration=configuration,
            created_at=created_at,
            created_by=created_by,
            credential_configured=credential_configured,
            enabled=enabled,
            id=id,
            name=name,
            organization_id=organization_id,
            type_=type_,
            updated_at=updated_at,
            updated_by=updated_by,
            workspace_id=workspace_id,
            header_names=header_names,
        )

        return model_provider
