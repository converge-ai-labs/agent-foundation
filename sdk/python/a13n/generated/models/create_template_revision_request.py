from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.create_template_revision_request_preparation import CreateTemplateRevisionRequestPreparation
from ..models.environment_access import EnvironmentAccess
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_template_revision_request_configuration import CreateTemplateRevisionRequestConfiguration
    from ..models.retention_policy import RetentionPolicy


T = TypeVar("T", bound="CreateTemplateRevisionRequest")


@_attrs_define(repr=False)
class CreateTemplateRevisionRequest:
    """
    Attributes:
        configuration (CreateTemplateRevisionRequestConfiguration):
        expected_version (int):
        provider_id (str):
        retention (RetentionPolicy):
        access (EnvironmentAccess | Unset):
        configuration_schema_version (str | Unset):
        preparation (CreateTemplateRevisionRequestPreparation | Unset):
    """

    configuration: CreateTemplateRevisionRequestConfiguration
    expected_version: int
    provider_id: str
    retention: RetentionPolicy
    access: EnvironmentAccess | Unset = UNSET
    configuration_schema_version: str | Unset = UNSET
    preparation: CreateTemplateRevisionRequestPreparation | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        configuration = self.configuration.to_dict()

        expected_version = self.expected_version

        provider_id = self.provider_id

        retention = self.retention.to_dict()

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
                "expected_version": expected_version,
                "provider_id": provider_id,
                "retention": retention,
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
        from ..models.create_template_revision_request_configuration import (
            CreateTemplateRevisionRequestConfiguration,
        )
        from ..models.retention_policy import RetentionPolicy

        d = dict(src_dict)
        configuration = CreateTemplateRevisionRequestConfiguration.from_dict(d.pop("configuration"))

        expected_version = d.pop("expected_version")

        provider_id = d.pop("provider_id")

        retention = RetentionPolicy.from_dict(d.pop("retention"))

        _access = d.pop("access", UNSET)
        access: EnvironmentAccess | Unset
        if isinstance(_access, Unset):
            access = UNSET
        else:
            access = EnvironmentAccess(_access)

        configuration_schema_version = d.pop("configuration_schema_version", UNSET)

        _preparation = d.pop("preparation", UNSET)
        preparation: CreateTemplateRevisionRequestPreparation | Unset
        if isinstance(_preparation, Unset):
            preparation = UNSET
        else:
            preparation = CreateTemplateRevisionRequestPreparation(_preparation)

        create_template_revision_request = cls(
            configuration=configuration,
            expected_version=expected_version,
            provider_id=provider_id,
            retention=retention,
            access=access,
            configuration_schema_version=configuration_schema_version,
            preparation=preparation,
        )

        return create_template_revision_request
