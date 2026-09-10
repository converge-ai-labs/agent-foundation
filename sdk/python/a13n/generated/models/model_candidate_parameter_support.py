from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.model_candidate_parameter_support_additional_property import (
    ModelCandidateParameterSupportAdditionalProperty,
)

T = TypeVar("T", bound="ModelCandidateParameterSupport")


@_attrs_define(repr=False)
class ModelCandidateParameterSupport:
    additional_properties: dict[str, ModelCandidateParameterSupportAdditionalProperty] = _attrs_field(
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
        model_candidate_parameter_support = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():
            additional_property = ModelCandidateParameterSupportAdditionalProperty(prop_dict)

            additional_properties[prop_name] = additional_property

        model_candidate_parameter_support.additional_properties = additional_properties
        return model_candidate_parameter_support

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> ModelCandidateParameterSupportAdditionalProperty:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: ModelCandidateParameterSupportAdditionalProperty) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
