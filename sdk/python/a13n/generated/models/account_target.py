from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.account_target_target_kind import AccountTargetTargetKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.account_target_provider_policy_type_0 import AccountTargetProviderPolicyType0
    from ..models.input_batching_policy import InputBatchingPolicy
    from ..models.input_override import InputOverride
    from ..models.principal_ref import PrincipalRef


T = TypeVar("T", bound="AccountTarget")


@_attrs_define(repr=False)
class AccountTarget:
    """
    Attributes:
        account_id (str):
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        external_target_id (str):
        id (str):
        organization_id (str):
        target_kind (AccountTargetTargetKind):
        updated_at (datetime.datetime):
        version (int):
        workspace_id (str):
        agent_id (None | str | Unset):
        config_override (InputOverride | None | Unset):
        input_batching (InputBatchingPolicy | None | Unset):
        provider_policy (AccountTargetProviderPolicyType0 | None | Unset):
        receive_enabled (bool | Unset):
    """

    account_id: str
    created_at: datetime.datetime
    created_by: PrincipalRef
    external_target_id: str
    id: str
    organization_id: str
    target_kind: AccountTargetTargetKind
    updated_at: datetime.datetime
    version: int
    workspace_id: str
    agent_id: str | Unset | None = UNSET
    config_override: InputOverride | Unset | None = UNSET
    input_batching: InputBatchingPolicy | Unset | None = UNSET
    provider_policy: AccountTargetProviderPolicyType0 | Unset | None = UNSET
    receive_enabled: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.account_target_provider_policy_type_0 import AccountTargetProviderPolicyType0
        from ..models.input_batching_policy import InputBatchingPolicy
        from ..models.input_override import InputOverride

        account_id = self.account_id

        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        external_target_id = self.external_target_id

        id = self.id

        organization_id = self.organization_id

        target_kind = self.target_kind.value

        updated_at = self.updated_at.isoformat()

        version = self.version

        workspace_id = self.workspace_id

        agent_id: str | Unset | None
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        else:
            agent_id = self.agent_id

        config_override: dict[str, Any] | Unset | None
        if isinstance(self.config_override, Unset):
            config_override = UNSET
        elif isinstance(self.config_override, InputOverride):
            config_override = self.config_override.to_dict()
        else:
            config_override = self.config_override

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
        elif isinstance(self.provider_policy, AccountTargetProviderPolicyType0):
            provider_policy = self.provider_policy.to_dict()
        else:
            provider_policy = self.provider_policy

        receive_enabled = self.receive_enabled

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "account_id": account_id,
                "created_at": created_at,
                "created_by": created_by,
                "external_target_id": external_target_id,
                "id": id,
                "organization_id": organization_id,
                "target_kind": target_kind,
                "updated_at": updated_at,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if config_override is not UNSET:
            field_dict["config_override"] = config_override
        if input_batching is not UNSET:
            field_dict["input_batching"] = input_batching
        if provider_policy is not UNSET:
            field_dict["provider_policy"] = provider_policy
        if receive_enabled is not UNSET:
            field_dict["receive_enabled"] = receive_enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.account_target_provider_policy_type_0 import AccountTargetProviderPolicyType0
        from ..models.input_batching_policy import InputBatchingPolicy
        from ..models.input_override import InputOverride
        from ..models.principal_ref import PrincipalRef

        d = dict(src_dict)
        account_id = d.pop("account_id")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        external_target_id = d.pop("external_target_id")

        id = d.pop("id")

        organization_id = d.pop("organization_id")

        target_kind = AccountTargetTargetKind(d.pop("target_kind"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        def _parse_agent_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

        def _parse_config_override(data: object) -> InputOverride | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_override_type_0 = InputOverride.from_dict(data)

                return config_override_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InputOverride | Unset | None, data)

        config_override = _parse_config_override(d.pop("config_override", UNSET))

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

        def _parse_provider_policy(data: object) -> AccountTargetProviderPolicyType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                provider_policy_type_0 = AccountTargetProviderPolicyType0.from_dict(data)

                return provider_policy_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AccountTargetProviderPolicyType0 | Unset | None, data)

        provider_policy = _parse_provider_policy(d.pop("provider_policy", UNSET))

        receive_enabled = d.pop("receive_enabled", UNSET)

        account_target = cls(
            account_id=account_id,
            created_at=created_at,
            created_by=created_by,
            external_target_id=external_target_id,
            id=id,
            organization_id=organization_id,
            target_kind=target_kind,
            updated_at=updated_at,
            version=version,
            workspace_id=workspace_id,
            agent_id=agent_id,
            config_override=config_override,
            input_batching=input_batching,
            provider_policy=provider_policy,
            receive_enabled=receive_enabled,
        )

        return account_target
