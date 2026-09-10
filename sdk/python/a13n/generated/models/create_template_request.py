from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.create_template_request_preparation import CreateTemplateRequestPreparation
from ..models.environment_access import EnvironmentAccess
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_template_request_configuration import CreateTemplateRequestConfiguration
    from ..models.retention_policy import RetentionPolicy


T = TypeVar("T", bound="CreateTemplateRequest")


@_attrs_define(repr=False)
class CreateTemplateRequest:
    """
    Attributes:
        configuration (CreateTemplateRequestConfiguration):
        name (str):
        provider_id (str):
        retention (RetentionPolicy):
        access (EnvironmentAccess | Unset):
        configuration_schema_version (str | Unset):
        description (None | str | Unset):
        preparation (CreateTemplateRequestPreparation | Unset):
    """

    configuration: CreateTemplateRequestConfiguration
    name: str
    provider_id: str
    retention: RetentionPolicy
    access: EnvironmentAccess | Unset = UNSET
    configuration_schema_version: str | Unset = UNSET
    description: str | Unset | None = UNSET
    preparation: CreateTemplateRequestPreparation | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        configuration = self.configuration.to_dict()

        name = self.name

        provider_id = self.provider_id

        retention = self.retention.to_dict()

        access: str | Unset = UNSET
        if not isinstance(self.access, Unset):
            access = self.access.value

        configuration_schema_version = self.configuration_schema_version

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        preparation: str | Unset = UNSET
        if not isinstance(self.preparation, Unset):
            preparation = self.preparation.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration": configuration,
                "name": name,
                "provider_id": provider_id,
                "retention": retention,
            }
        )
        if access is not UNSET:
            field_dict["access"] = access
        if configuration_schema_version is not UNSET:
            field_dict["configuration_schema_version"] = configuration_schema_version
        if description is not UNSET:
            field_dict["description"] = description
        if preparation is not UNSET:
            field_dict["preparation"] = preparation

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_template_request_configuration import CreateTemplateRequestConfiguration
        from ..models.retention_policy import RetentionPolicy

        d = dict(src_dict)
        configuration = CreateTemplateRequestConfiguration.from_dict(d.pop("configuration"))

        name = d.pop("name")

        provider_id = d.pop("provider_id")

        retention = RetentionPolicy.from_dict(d.pop("retention"))

        _access = d.pop("access", UNSET)
        access: EnvironmentAccess | Unset
        if isinstance(_access, Unset):
            access = UNSET
        else:
            access = EnvironmentAccess(_access)

        configuration_schema_version = d.pop("configuration_schema_version", UNSET)

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        _preparation = d.pop("preparation", UNSET)
        preparation: CreateTemplateRequestPreparation | Unset
        if isinstance(_preparation, Unset):
            preparation = UNSET
        else:
            preparation = CreateTemplateRequestPreparation(_preparation)

        create_template_request = cls(
            configuration=configuration,
            name=name,
            provider_id=provider_id,
            retention=retention,
            access=access,
            configuration_schema_version=configuration_schema_version,
            description=description,
            preparation=preparation,
        )

        return create_template_request
