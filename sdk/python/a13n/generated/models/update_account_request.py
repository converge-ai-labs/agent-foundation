from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.reception_scope import ReceptionScope
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.input_batching_policy import InputBatchingPolicy
    from ..models.memory_settings import MemorySettings
    from ..models.update_account_request_provider_config_type_0 import UpdateAccountRequestProviderConfigType0
    from ..models.update_account_request_provider_policy_type_0 import UpdateAccountRequestProviderPolicyType0


T = TypeVar("T", bound="UpdateAccountRequest")


@_attrs_define(repr=False)
class UpdateAccountRequest:
    """
    Attributes:
        expected_version (int):
        default_agent_id (None | str | Unset):
        execution_service_account_id (None | str | Unset):
        input_batching (InputBatchingPolicy | None | Unset):
        memory (MemorySettings | None | Unset):
        name (None | str | Unset):
        provider_config (None | Unset | UpdateAccountRequestProviderConfigType0):
        provider_policy (None | Unset | UpdateAccountRequestProviderPolicyType0):
        receive_enabled (bool | None | Unset):
        reception_scope (None | ReceptionScope | Unset):
    """

    expected_version: int
    default_agent_id: str | Unset | None = UNSET
    execution_service_account_id: str | Unset | None = UNSET
    input_batching: InputBatchingPolicy | Unset | None = UNSET
    memory: MemorySettings | Unset | None = UNSET
    name: str | Unset | None = UNSET
    provider_config: Unset | UpdateAccountRequestProviderConfigType0 | None = UNSET
    provider_policy: Unset | UpdateAccountRequestProviderPolicyType0 | None = UNSET
    receive_enabled: bool | Unset | None = UNSET
    reception_scope: ReceptionScope | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.input_batching_policy import InputBatchingPolicy
        from ..models.memory_settings import MemorySettings
        from ..models.update_account_request_provider_config_type_0 import (
            UpdateAccountRequestProviderConfigType0,
        )
        from ..models.update_account_request_provider_policy_type_0 import (
            UpdateAccountRequestProviderPolicyType0,
        )

        expected_version = self.expected_version

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

        memory: dict[str, Any] | Unset | None
        if isinstance(self.memory, Unset):
            memory = UNSET
        elif isinstance(self.memory, MemorySettings):
            memory = self.memory.to_dict()
        else:
            memory = self.memory

        name: str | Unset | None
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        provider_config: dict[str, Any] | Unset | None
        if isinstance(self.provider_config, Unset):
            provider_config = UNSET
        elif isinstance(self.provider_config, UpdateAccountRequestProviderConfigType0):
            provider_config = self.provider_config.to_dict()
        else:
            provider_config = self.provider_config

        provider_policy: dict[str, Any] | Unset | None
        if isinstance(self.provider_policy, Unset):
            provider_policy = UNSET
        elif isinstance(self.provider_policy, UpdateAccountRequestProviderPolicyType0):
            provider_policy = self.provider_policy.to_dict()
        else:
            provider_policy = self.provider_policy

        receive_enabled: bool | Unset | None
        if isinstance(self.receive_enabled, Unset):
            receive_enabled = UNSET
        else:
            receive_enabled = self.receive_enabled

        reception_scope: str | Unset | None
        if isinstance(self.reception_scope, Unset):
            reception_scope = UNSET
        elif isinstance(self.reception_scope, ReceptionScope):
            reception_scope = self.reception_scope.value
        else:
            reception_scope = self.reception_scope

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
            }
        )
        if default_agent_id is not UNSET:
            field_dict["default_agent_id"] = default_agent_id
        if execution_service_account_id is not UNSET:
            field_dict["execution_service_account_id"] = execution_service_account_id
        if input_batching is not UNSET:
            field_dict["input_batching"] = input_batching
        if memory is not UNSET:
            field_dict["memory"] = memory
        if name is not UNSET:
            field_dict["name"] = name
        if provider_config is not UNSET:
            field_dict["provider_config"] = provider_config
        if provider_policy is not UNSET:
            field_dict["provider_policy"] = provider_policy
        if receive_enabled is not UNSET:
            field_dict["receive_enabled"] = receive_enabled
        if reception_scope is not UNSET:
            field_dict["reception_scope"] = reception_scope

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.input_batching_policy import InputBatchingPolicy
        from ..models.memory_settings import MemorySettings
        from ..models.update_account_request_provider_config_type_0 import (
            UpdateAccountRequestProviderConfigType0,
        )
        from ..models.update_account_request_provider_policy_type_0 import (
            UpdateAccountRequestProviderPolicyType0,
        )

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

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

        def _parse_memory(data: object) -> MemorySettings | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                memory_type_0 = MemorySettings.from_dict(data)

                return memory_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MemorySettings | Unset | None, data)

        memory = _parse_memory(d.pop("memory", UNSET))

        def _parse_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_provider_config(data: object) -> Unset | UpdateAccountRequestProviderConfigType0 | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                provider_config_type_0 = UpdateAccountRequestProviderConfigType0.from_dict(data)

                return provider_config_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Unset | UpdateAccountRequestProviderConfigType0 | None, data)

        provider_config = _parse_provider_config(d.pop("provider_config", UNSET))

        def _parse_provider_policy(data: object) -> Unset | UpdateAccountRequestProviderPolicyType0 | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                provider_policy_type_0 = UpdateAccountRequestProviderPolicyType0.from_dict(data)

                return provider_policy_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Unset | UpdateAccountRequestProviderPolicyType0 | None, data)

        provider_policy = _parse_provider_policy(d.pop("provider_policy", UNSET))

        def _parse_receive_enabled(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        receive_enabled = _parse_receive_enabled(d.pop("receive_enabled", UNSET))

        def _parse_reception_scope(data: object) -> ReceptionScope | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                reception_scope_type_0 = ReceptionScope(data)

                return reception_scope_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ReceptionScope | Unset | None, data)

        reception_scope = _parse_reception_scope(d.pop("reception_scope", UNSET))

        update_account_request = cls(
            expected_version=expected_version,
            default_agent_id=default_agent_id,
            execution_service_account_id=execution_service_account_id,
            input_batching=input_batching,
            memory=memory,
            name=name,
            provider_config=provider_config,
            provider_policy=provider_policy,
            receive_enabled=receive_enabled,
            reception_scope=reception_scope,
        )

        return update_account_request
