from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.create_search_provider_request_type import CreateSearchProviderRequestType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.search_configuration import SearchConfiguration


T = TypeVar("T", bound="CreateSearchProviderRequest")


@_attrs_define(repr=False)
class CreateSearchProviderRequest:
    """
    Attributes:
        credential (str):
        name (str):
        type_ (CreateSearchProviderRequestType):
        configuration (SearchConfiguration | Unset):
        enabled (bool | Unset):
    """

    credential: str
    name: str
    type_: CreateSearchProviderRequestType
    configuration: SearchConfiguration | Unset = UNSET
    enabled: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        credential = self.credential

        name = self.name

        type_ = self.type_.value

        configuration: dict[str, Any] | Unset = UNSET
        if not isinstance(self.configuration, Unset):
            configuration = self.configuration.to_dict()

        enabled = self.enabled

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "credential": credential,
                "name": name,
                "type": type_,
            }
        )
        if configuration is not UNSET:
            field_dict["configuration"] = configuration
        if enabled is not UNSET:
            field_dict["enabled"] = enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.search_configuration import SearchConfiguration

        d = dict(src_dict)
        credential = d.pop("credential")

        name = d.pop("name")

        type_ = CreateSearchProviderRequestType(d.pop("type"))

        _configuration = d.pop("configuration", UNSET)
        configuration: SearchConfiguration | Unset
        if isinstance(_configuration, Unset):
            configuration = UNSET
        else:
            configuration = SearchConfiguration.from_dict(_configuration)

        enabled = d.pop("enabled", UNSET)

        create_search_provider_request = cls(
            credential=credential,
            name=name,
            type_=type_,
            configuration=configuration,
            enabled=enabled,
        )

        return create_search_provider_request
