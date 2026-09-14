from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.account_provider_definition_target_kinds_item import AccountProviderDefinitionTargetKindsItem

if TYPE_CHECKING:
    from ..models.account_provider_definition_configuration_schema import AccountProviderDefinitionConfigurationSchema
    from ..models.account_provider_definition_credential_schema import AccountProviderDefinitionCredentialSchema
    from ..models.account_provider_definition_reception_policy_schema import (
        AccountProviderDefinitionReceptionPolicySchema,
    )


T = TypeVar("T", bound="AccountProviderDefinition")


@_attrs_define(repr=False)
class AccountProviderDefinition:
    """
    Attributes:
        config_version (str):
        configuration_schema (AccountProviderDefinitionConfigurationSchema):
        credential_schema (AccountProviderDefinitionCredentialSchema):
        provider_key (str):
        reception_policy_schema (AccountProviderDefinitionReceptionPolicySchema):
        target_kinds (list[AccountProviderDefinitionTargetKindsItem]):
    """

    config_version: str
    configuration_schema: AccountProviderDefinitionConfigurationSchema
    credential_schema: AccountProviderDefinitionCredentialSchema
    provider_key: str
    reception_policy_schema: AccountProviderDefinitionReceptionPolicySchema
    target_kinds: list[AccountProviderDefinitionTargetKindsItem]

    def to_dict(self) -> dict[str, Any]:
        config_version = self.config_version

        configuration_schema = self.configuration_schema.to_dict()

        credential_schema = self.credential_schema.to_dict()

        provider_key = self.provider_key

        reception_policy_schema = self.reception_policy_schema.to_dict()

        target_kinds = []
        for target_kinds_item_data in self.target_kinds:
            target_kinds_item = target_kinds_item_data.value
            target_kinds.append(target_kinds_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "config_version": config_version,
                "configuration_schema": configuration_schema,
                "credential_schema": credential_schema,
                "provider_key": provider_key,
                "reception_policy_schema": reception_policy_schema,
                "target_kinds": target_kinds,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.account_provider_definition_configuration_schema import (
            AccountProviderDefinitionConfigurationSchema,
        )
        from ..models.account_provider_definition_credential_schema import (
            AccountProviderDefinitionCredentialSchema,
        )
        from ..models.account_provider_definition_reception_policy_schema import (
            AccountProviderDefinitionReceptionPolicySchema,
        )

        d = dict(src_dict)
        config_version = d.pop("config_version")

        configuration_schema = AccountProviderDefinitionConfigurationSchema.from_dict(d.pop("configuration_schema"))

        credential_schema = AccountProviderDefinitionCredentialSchema.from_dict(d.pop("credential_schema"))

        provider_key = d.pop("provider_key")

        reception_policy_schema = AccountProviderDefinitionReceptionPolicySchema.from_dict(
            d.pop("reception_policy_schema")
        )

        target_kinds = []
        _target_kinds = d.pop("target_kinds")
        for target_kinds_item_data in _target_kinds:
            target_kinds_item = AccountProviderDefinitionTargetKindsItem(target_kinds_item_data)

            target_kinds.append(target_kinds_item)

        account_provider_definition = cls(
            config_version=config_version,
            configuration_schema=configuration_schema,
            credential_schema=credential_schema,
            provider_key=provider_key,
            reception_policy_schema=reception_policy_schema,
            target_kinds=target_kinds,
        )

        return account_provider_definition
