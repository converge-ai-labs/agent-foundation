from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.environment_access import EnvironmentAccess
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.environment_state import EnvironmentState
    from ..models.register_environment_request_configuration import RegisterEnvironmentRequestConfiguration
    from ..models.register_environment_request_labels import RegisterEnvironmentRequestLabels


T = TypeVar("T", bound="RegisterEnvironmentRequest")


@_attrs_define(repr=False)
class RegisterEnvironmentRequest:
    """
    Attributes:
        configuration (RegisterEnvironmentRequestConfiguration):
        provider_id (str):
        access (EnvironmentAccess | Unset):
        configuration_schema_version (str | Unset):
        labels (RegisterEnvironmentRequestLabels | Unset):
        name (None | str | Unset):
        state (EnvironmentState | None | Unset):
    """

    configuration: RegisterEnvironmentRequestConfiguration
    provider_id: str
    access: EnvironmentAccess | Unset = UNSET
    configuration_schema_version: str | Unset = UNSET
    labels: RegisterEnvironmentRequestLabels | Unset = UNSET
    name: str | Unset | None = UNSET
    state: EnvironmentState | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.environment_state import EnvironmentState

        configuration = self.configuration.to_dict()

        provider_id = self.provider_id

        access: str | Unset = UNSET
        if not isinstance(self.access, Unset):
            access = self.access.value

        configuration_schema_version = self.configuration_schema_version

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        name: str | Unset | None
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        state: dict[str, Any] | Unset | None
        if isinstance(self.state, Unset):
            state = UNSET
        elif isinstance(self.state, EnvironmentState):
            state = self.state.to_dict()
        else:
            state = self.state

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration": configuration,
                "provider_id": provider_id,
            }
        )
        if access is not UNSET:
            field_dict["access"] = access
        if configuration_schema_version is not UNSET:
            field_dict["configuration_schema_version"] = configuration_schema_version
        if labels is not UNSET:
            field_dict["labels"] = labels
        if name is not UNSET:
            field_dict["name"] = name
        if state is not UNSET:
            field_dict["state"] = state

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.environment_state import EnvironmentState
        from ..models.register_environment_request_configuration import (
            RegisterEnvironmentRequestConfiguration,
        )
        from ..models.register_environment_request_labels import RegisterEnvironmentRequestLabels

        d = dict(src_dict)
        configuration = RegisterEnvironmentRequestConfiguration.from_dict(d.pop("configuration"))

        provider_id = d.pop("provider_id")

        _access = d.pop("access", UNSET)
        access: EnvironmentAccess | Unset
        if isinstance(_access, Unset):
            access = UNSET
        else:
            access = EnvironmentAccess(_access)

        configuration_schema_version = d.pop("configuration_schema_version", UNSET)

        _labels = d.pop("labels", UNSET)
        labels: RegisterEnvironmentRequestLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = RegisterEnvironmentRequestLabels.from_dict(_labels)

        def _parse_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_state(data: object) -> EnvironmentState | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                state_type_0 = EnvironmentState.from_dict(data)

                return state_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(EnvironmentState | Unset | None, data)

        state = _parse_state(d.pop("state", UNSET))

        register_environment_request = cls(
            configuration=configuration,
            provider_id=provider_id,
            access=access,
            configuration_schema_version=configuration_schema_version,
            labels=labels,
            name=name,
            state=state,
        )

        return register_environment_request
