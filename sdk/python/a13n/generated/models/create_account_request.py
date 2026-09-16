from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.reception_scope import ReceptionScope
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_account_request_credentials import CreateAccountRequestCredentials
    from ..models.create_account_request_provider_config import CreateAccountRequestProviderConfig
    from ..models.create_account_request_provider_policy_type_0 import CreateAccountRequestProviderPolicyType0
    from ..models.input_batching_policy import InputBatchingPolicy


T = TypeVar("T", bound="CreateAccountRequest")


@_attrs_define(repr=False)
class CreateAccountRequest:
    """
    Attributes:
        credentials (CreateAccountRequestCredentials):
        name (str):
        provider_config (CreateAccountRequestProviderConfig):
        provider_config_version (str):
        provider_key (str):
        default_agent_id (None | str | Unset):
        execution_service_account_id (None | str | Unset):
        input_batching (InputBatchingPolicy | None | Unset):
        provider_policy (CreateAccountRequestProviderPolicyType0 | None | Unset):
        receive_enabled (bool | Unset):
        reception_scope (ReceptionScope | Unset):
    """

    credentials: CreateAccountRequestCredentials
    name: str
    provider_config: CreateAccountRequestProviderConfig
    provider_config_version: str
    provider_key: str
    default_agent_id: str | Unset | None = UNSET
    execution_service_account_id: str | Unset | None = UNSET
    input_batching: InputBatchingPolicy | Unset | None = UNSET
    provider_policy: CreateAccountRequestProviderPolicyType0 | Unset | None = UNSET
    receive_enabled: bool | Unset = UNSET
    reception_scope: ReceptionScope | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.create_account_request_provider_policy_type_0 import CreateAccountRequestProviderPolicyType0
        from ..models.input_batching_policy import InputBatchingPolicy

        credentials = self.credentials.to_dict()

        name = self.name

        provider_config = self.provider_config.to_dict()

        provider_config_version = self.provider_config_version

        provider_key = self.provider_key

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
        elif isinstance(self.provider_policy, CreateAccountRequestProviderPolicyType0):
            provider_policy = self.provider_policy.to_dict()
        else:
            provider_policy = self.provider_policy

        receive_enabled = self.receive_enabled

        reception_scope: str | Unset = UNSET
        if not isinstance(self.reception_scope, Unset):
            reception_scope = self.reception_scope.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "credentials": credentials,
                "name": name,
                "provider_config": provider_config,
                "provider_config_version": provider_config_version,
                "provider_key": provider_key,
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
        if reception_scope is not UNSET:
            field_dict["reception_scope"] = reception_scope

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_account_request_credentials import CreateAccountRequestCredentials
        from ..models.create_account_request_provider_config import CreateAccountRequestProviderConfig
        from ..models.create_account_request_provider_policy_type_0 import (
            CreateAccountRequestProviderPolicyType0,
        )
        from ..models.input_batching_policy import InputBatchingPolicy

        d = dict(src_dict)
        credentials = CreateAccountRequestCredentials.from_dict(d.pop("credentials"))

        name = d.pop("name")

        provider_config = CreateAccountRequestProviderConfig.from_dict(d.pop("provider_config"))

        provider_config_version = d.pop("provider_config_version")

        provider_key = d.pop("provider_key")

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

        def _parse_provider_policy(data: object) -> CreateAccountRequestProviderPolicyType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                provider_policy_type_0 = CreateAccountRequestProviderPolicyType0.from_dict(data)

                return provider_policy_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CreateAccountRequestProviderPolicyType0 | Unset | None, data)

        provider_policy = _parse_provider_policy(d.pop("provider_policy", UNSET))

        receive_enabled = d.pop("receive_enabled", UNSET)

        _reception_scope = d.pop("reception_scope", UNSET)
        reception_scope: ReceptionScope | Unset
        if isinstance(_reception_scope, Unset):
            reception_scope = UNSET
        else:
            reception_scope = ReceptionScope(_reception_scope)

        create_account_request = cls(
            credentials=credentials,
            name=name,
            provider_config=provider_config,
            provider_config_version=provider_config_version,
            provider_key=provider_key,
            default_agent_id=default_agent_id,
            execution_service_account_id=execution_service_account_id,
            input_batching=input_batching,
            provider_policy=provider_policy,
            receive_enabled=receive_enabled,
            reception_scope=reception_scope,
        )

        return create_account_request
