from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.environment_access import EnvironmentAccess
from ..models.environment_template_revision_preparation import EnvironmentTemplateRevisionPreparation
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.environment_template_revision_configuration import EnvironmentTemplateRevisionConfiguration
    from ..models.retention_policy import RetentionPolicy


T = TypeVar("T", bound="EnvironmentTemplateRevision")


@_attrs_define(repr=False)
class EnvironmentTemplateRevision:
    """
    Attributes:
        configuration (EnvironmentTemplateRevisionConfiguration):
        created_at (datetime.datetime):
        id (str):
        organization_id (str):
        provider_id (str):
        retention (RetentionPolicy):
        template_id (str):
        version (int):
        workspace_id (None | str):
        access (EnvironmentAccess | Unset):
        configuration_schema_version (str | Unset):
        preparation (EnvironmentTemplateRevisionPreparation | Unset):
    """

    configuration: EnvironmentTemplateRevisionConfiguration
    created_at: datetime.datetime
    id: str
    organization_id: str
    provider_id: str
    retention: RetentionPolicy
    template_id: str
    version: int
    workspace_id: str | None
    access: EnvironmentAccess | Unset = UNSET
    configuration_schema_version: str | Unset = UNSET
    preparation: EnvironmentTemplateRevisionPreparation | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        configuration = self.configuration.to_dict()

        created_at = self.created_at.isoformat()

        id = self.id

        organization_id = self.organization_id

        provider_id = self.provider_id

        retention = self.retention.to_dict()

        template_id = self.template_id

        version = self.version

        workspace_id: str | None
        workspace_id = self.workspace_id

        access: str | Unset = UNSET
        if not isinstance(self.access, Unset):
            access = self.access.value

        configuration_schema_version = self.configuration_schema_version

        preparation: str | Unset = UNSET
        if not isinstance(self.preparation, Unset):
            preparation = self.preparation.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration": configuration,
                "created_at": created_at,
                "id": id,
                "organization_id": organization_id,
                "provider_id": provider_id,
                "retention": retention,
                "template_id": template_id,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if access is not UNSET:
            field_dict["access"] = access
        if configuration_schema_version is not UNSET:
            field_dict["configuration_schema_version"] = configuration_schema_version
        if preparation is not UNSET:
            field_dict["preparation"] = preparation

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.environment_template_revision_configuration import (
            EnvironmentTemplateRevisionConfiguration,
        )
        from ..models.retention_policy import RetentionPolicy

        d = dict(src_dict)
        configuration = EnvironmentTemplateRevisionConfiguration.from_dict(d.pop("configuration"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        organization_id = d.pop("organization_id")

        provider_id = d.pop("provider_id")

        retention = RetentionPolicy.from_dict(d.pop("retention"))

        template_id = d.pop("template_id")

        version = d.pop("version")

        def _parse_workspace_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        workspace_id = _parse_workspace_id(d.pop("workspace_id"))

        _access = d.pop("access", UNSET)
        access: EnvironmentAccess | Unset
        if isinstance(_access, Unset):
            access = UNSET
        else:
            access = EnvironmentAccess(_access)

        configuration_schema_version = d.pop("configuration_schema_version", UNSET)

        _preparation = d.pop("preparation", UNSET)
        preparation: EnvironmentTemplateRevisionPreparation | Unset
        if isinstance(_preparation, Unset):
            preparation = UNSET
        else:
            preparation = EnvironmentTemplateRevisionPreparation(_preparation)

        environment_template_revision = cls(
            configuration=configuration,
            created_at=created_at,
            id=id,
            organization_id=organization_id,
            provider_id=provider_id,
            retention=retention,
            template_id=template_id,
            version=version,
            workspace_id=workspace_id,
            access=access,
            configuration_schema_version=configuration_schema_version,
            preparation=preparation,
        )

        return environment_template_revision
