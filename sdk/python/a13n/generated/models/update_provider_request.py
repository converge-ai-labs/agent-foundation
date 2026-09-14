from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.update_provider_request_credential_type_0 import UpdateProviderRequestCredentialType0


T = TypeVar("T", bound="UpdateProviderRequest")


@_attrs_define(repr=False)
class UpdateProviderRequest:
    """
    Attributes:
        credential (None | Unset | UpdateProviderRequestCredentialType0):
        enabled (bool | None | Unset):
        name (None | str | Unset):
    """

    credential: Unset | UpdateProviderRequestCredentialType0 | None = UNSET
    enabled: bool | Unset | None = UNSET
    name: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.update_provider_request_credential_type_0 import (
            UpdateProviderRequestCredentialType0,
        )

        credential: dict[str, Any] | Unset | None
        if isinstance(self.credential, Unset):
            credential = UNSET
        elif isinstance(self.credential, UpdateProviderRequestCredentialType0):
            credential = self.credential.to_dict()
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
        if credential is not UNSET:
            field_dict["credential"] = credential
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.update_provider_request_credential_type_0 import (
            UpdateProviderRequestCredentialType0,
        )

        d = dict(src_dict)

        def _parse_credential(data: object) -> Unset | UpdateProviderRequestCredentialType0 | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credential_type_0 = UpdateProviderRequestCredentialType0.from_dict(data)

                return credential_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Unset | UpdateProviderRequestCredentialType0 | None, data)

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

        update_provider_request = cls(
            credential=credential,
            enabled=enabled,
            name=name,
        )

        return update_provider_request
