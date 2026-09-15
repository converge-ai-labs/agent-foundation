from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.labels_body_labels import LabelsBodyLabels


T = TypeVar("T", bound="LabelsBody")


@_attrs_define(repr=False)
class LabelsBody:
    """Complete replacement body for a resource label map.

    Attributes:
        labels (LabelsBodyLabels):
    """

    labels: LabelsBodyLabels

    def to_dict(self) -> dict[str, Any]:
        labels = self.labels.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "labels": labels,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.labels_body_labels import LabelsBodyLabels

        d = dict(src_dict)
        labels = LabelsBodyLabels.from_dict(d.pop("labels"))

        labels_body = cls(
            labels=labels,
        )

        return labels_body
