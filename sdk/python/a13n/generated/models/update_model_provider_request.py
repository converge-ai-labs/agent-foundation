from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.update_model_provider_request_configuration_type_0 import UpdateModelProviderRequestConfigurationType0


T = TypeVar("T", bound="UpdateModelProviderRequest")


@_attrs_define(repr=False)
class UpdateModelProviderRequest:
    """
    Attributes:
        configuration (None | Unset | UpdateModelProviderRequestConfigurationType0):
        credential (None | str | Unset):
        enabled (bool | None | Unset):
        name (None | str | Unset):
    """

    configuration: Unset | UpdateModelProviderRequestConfigurationType0 | None = UNSET
    credential: str | Unset | None = UNSET
    enabled: bool | Unset | None = UNSET
    name: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.update_model_provider_request_configuration_type_0 import (
            UpdateModelProviderRequestConfigurationType0,
        )

        configuration: dict[str, Any] | Unset | None
        if isinstance(self.configuration, Unset):
            configuration = UNSET
        elif isinstance(self.configuration, UpdateModelProviderRequestConfigurationType0):
            configuration = self.configuration.to_dict()
        else:
            configuration = self.configuration

        credential: str | Unset | None
        if isinstance(self.credential, Unset):
            credential = UNSET
        else:
            credential = self.credential

        enabled: bool | Unset | None
        if isinstance(self.enabled, Unset):
            enabled = UNSET
        else:
            enabled = self.enabled

        name: str | Unset | None
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if configuration is not UNSET:
            field_dict["configuration"] = configuration
        if credential is not UNSET:
            field_dict["credential"] = credential
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.update_model_provider_request_configuration_type_0 import (
            UpdateModelProviderRequestConfigurationType0,
        )

        d = dict(src_dict)

        def _parse_configuration(data: object) -> Unset | UpdateModelProviderRequestConfigurationType0 | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                configuration_type_0 = UpdateModelProviderRequestConfigurationType0.from_dict(data)

                return configuration_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Unset | UpdateModelProviderRequestConfigurationType0 | None, data)

        configuration = _parse_configuration(d.pop("configuration", UNSET))

        def _parse_credential(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        credential = _parse_credential(d.pop("credential", UNSET))

        def _parse_enabled(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        enabled = _parse_enabled(d.pop("enabled", UNSET))

        def _parse_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        name = _parse_name(d.pop("name", UNSET))

        update_model_provider_request = cls(
            configuration=configuration,
            credential=credential,
            enabled=enabled,
            name=name,
        )

        return update_model_provider_request
