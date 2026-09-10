from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.model_description_parameter_support_additional_property import (
    ModelDescriptionParameterSupportAdditionalProperty,
)

T = TypeVar("T", bound="ModelDescriptionParameterSupport")


@_attrs_define(repr=False)
class ModelDescriptionParameterSupport:
    additional_properties: dict[str, ModelDescriptionParameterSupportAdditionalProperty] = _attrs_field(
        init=False, factory=dict
    )

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            field_dict[prop_name] = prop.value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        model_description_parameter_support = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():
            additional_property = ModelDescriptionParameterSupportAdditionalProperty(prop_dict)

            additional_properties[prop_name] = additional_property

        model_description_parameter_support.additional_properties = additional_properties
        return model_description_parameter_support

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> ModelDescriptionParameterSupportAdditionalProperty:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: ModelDescriptionParameterSupportAdditionalProperty) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
