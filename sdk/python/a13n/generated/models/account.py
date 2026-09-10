from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.account_status import AccountStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.account_provider_config import AccountProviderConfig
    from ..models.account_provider_policy_type_0 import AccountProviderPolicyType0
    from ..models.input_batching_policy import InputBatchingPolicy
    from ..models.principal_ref import PrincipalRef


T = TypeVar("T", bound="Account")


@_attrs_define(repr=False)
class Account:
    """
    Attributes:
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        credential_configured (bool):
        credential_generation (int):
        id (str):
        name (str):
        organization_id (str):
        provider_config (AccountProviderConfig):
        provider_config_version (str):
        provider_key (str):
        status (AccountStatus):
        updated_at (datetime.datetime):
        version (int):
        workspace_id (str):
        default_agent_id (None | str | Unset):
        execution_service_account_id (None | str | Unset):
        input_batching (InputBatchingPolicy | None | Unset):
        provider_policy (AccountProviderPolicyType0 | None | Unset):
        receive_enabled (bool | Unset):
    """

    created_at: datetime.datetime
    created_by: PrincipalRef
    credential_configured: bool
    credential_generation: int
    id: str
    name: str
    organization_id: str
    provider_config: AccountProviderConfig
    provider_config_version: str
    provider_key: str
    status: AccountStatus
    updated_at: datetime.datetime
    version: int
    workspace_id: str
    default_agent_id: str | Unset | None = UNSET
    execution_service_account_id: str | Unset | None = UNSET
    input_batching: InputBatchingPolicy | Unset | None = UNSET
    provider_policy: AccountProviderPolicyType0 | Unset | None = UNSET
    receive_enabled: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.account_provider_policy_type_0 import AccountProviderPolicyType0
        from ..models.input_batching_policy import InputBatchingPolicy

        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        credential_configured = self.credential_configured

        credential_generation = self.credential_generation

        id = self.id

        name = self.name

        organization_id = self.organization_id

        provider_config = self.provider_config.to_dict()

        provider_config_version = self.provider_config_version

        provider_key = self.provider_key

        status = self.status.value

        updated_at = self.updated_at.isoformat()

        version = self.version

        workspace_id = self.workspace_id

        default_agent_id: str | Unset | None
        if isinstance(self.default_agent_id, Unset):
            default_agent_id = UNSET
        else:
            default_agent_id = self.default_agent_id

        execution_service_account_id: str | Unset | None
        if isinstance(self.execution_service_account_id, Unset):
            execution_service_account_id = UNSET
        else:
            execution_service_account_id = self.execution_service_account_id

        input_batching: dict[str, Any] | Unset | None
        if isinstance(self.input_batching, Unset):
            input_batching = UNSET
        elif isinstance(self.input_batching, InputBatchingPolicy):
            input_batching = self.input_batching.to_dict()
        else:
            input_batching = self.input_batching

        provider_policy: dict[str, Any] | Unset | None
        if isinstance(self.provider_policy, Unset):
            provider_policy = UNSET
        elif isinstance(self.provider_policy, AccountProviderPolicyType0):
            provider_policy = self.provider_policy.to_dict()
        else:
            provider_policy = self.provider_policy

        receive_enabled = self.receive_enabled

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "created_at": created_at,
                "created_by": created_by,
                "credential_configured": credential_configured,
                "credential_generation": credential_generation,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "provider_config": provider_config,
                "provider_config_version": provider_config_version,
                "provider_key": provider_key,
                "status": status,
                "updated_at": updated_at,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if default_agent_id is not UNSET:
            field_dict["default_agent_id"] = default_agent_id
        if execution_service_account_id is not UNSET:
            field_dict["execution_service_account_id"] = execution_service_account_id
        if input_batching is not UNSET:
            field_dict["input_batching"] = input_batching
        if provider_policy is not UNSET:
            field_dict["provider_policy"] = provider_policy
        if receive_enabled is not UNSET:
            field_dict["receive_enabled"] = receive_enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.account_provider_config import AccountProviderConfig
        from ..models.account_provider_policy_type_0 import AccountProviderPolicyType0
        from ..models.input_batching_policy import InputBatchingPolicy
        from ..models.principal_ref import PrincipalRef

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        credential_configured = d.pop("credential_configured")

        credential_generation = d.pop("credential_generation")

        id = d.pop("id")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        provider_config = AccountProviderConfig.from_dict(d.pop("provider_config"))

        provider_config_version = d.pop("provider_config_version")

        provider_key = d.pop("provider_key")

        status = AccountStatus(d.pop("status"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        def _parse_default_agent_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        default_agent_id = _parse_default_agent_id(d.pop("default_agent_id", UNSET))

        def _parse_execution_service_account_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        execution_service_account_id = _parse_execution_service_account_id(d.pop("execution_service_account_id", UNSET))

        def _parse_input_batching(data: object) -> InputBatchingPolicy | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_batching_type_0 = InputBatchingPolicy.from_dict(data)

                return input_batching_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InputBatchingPolicy | Unset | None, data)

        input_batching = _parse_input_batching(d.pop("input_batching", UNSET))

        def _parse_provider_policy(data: object) -> AccountProviderPolicyType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                provider_policy_type_0 = AccountProviderPolicyType0.from_dict(data)

                return provider_policy_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AccountProviderPolicyType0 | Unset | None, data)

        provider_policy = _parse_provider_policy(d.pop("provider_policy", UNSET))

        receive_enabled = d.pop("receive_enabled", UNSET)

        account = cls(
            created_at=created_at,
            created_by=created_by,
            credential_configured=credential_configured,
            credential_generation=credential_generation,
            id=id,
            name=name,
            organization_id=organization_id,
            provider_config=provider_config,
            provider_config_version=provider_config_version,
            provider_key=provider_key,
            status=status,
            updated_at=updated_at,
            version=version,
            workspace_id=workspace_id,
            default_agent_id=default_agent_id,
            execution_service_account_id=execution_service_account_id,
            input_batching=input_batching,
            provider_policy=provider_policy,
            receive_enabled=receive_enabled,
        )

        return account
