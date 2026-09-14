from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.new_environment_selection_labels import NewEnvironmentSelectionLabels


T = TypeVar("T", bound="NewEnvironmentSelection")


@_attrs_define(repr=False)
class NewEnvironmentSelection:
    """
    Attributes:
        template_id (str):
        labels (NewEnvironmentSelectionLabels | Unset):
        version (int | None | Unset):
    """

    template_id: str
    labels: NewEnvironmentSelectionLabels | Unset = UNSET
    version: int | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        template_id = self.template_id

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        version: int | Unset | None
        if isinstance(self.version, Unset):
            version = UNSET
        else:
            version = self.version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "template_id": template_id,
            }
        )
        if labels is not UNSET:
            field_dict["labels"] = labels
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.new_environment_selection_labels import NewEnvironmentSelectionLabels

        d = dict(src_dict)
        template_id = d.pop("template_id")

        _labels = d.pop("labels", UNSET)
        labels: NewEnvironmentSelectionLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = NewEnvironmentSelectionLabels.from_dict(_labels)

        def _parse_version(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        version = _parse_version(d.pop("version", UNSET))

        new_environment_selection = cls(
            template_id=template_id,
            labels=labels,
            version=version,
        )

        return new_environment_selection
