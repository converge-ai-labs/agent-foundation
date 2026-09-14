from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_managed_environment_request_labels import CreateManagedEnvironmentRequestLabels


T = TypeVar("T", bound="CreateManagedEnvironmentRequest")


@_attrs_define(repr=False)
class CreateManagedEnvironmentRequest:
    """
    Attributes:
        template_id (str):
        labels (CreateManagedEnvironmentRequestLabels | Unset):
        name (None | str | Unset):
        version (int | None | Unset):
    """

    template_id: str
    labels: CreateManagedEnvironmentRequestLabels | Unset = UNSET
    name: str | Unset | None = UNSET
    version: int | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        template_id = self.template_id

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        name: str | Unset | None
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

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
        if name is not UNSET:
            field_dict["name"] = name
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_managed_environment_request_labels import (
            CreateManagedEnvironmentRequestLabels,
        )

        d = dict(src_dict)
        template_id = d.pop("template_id")

        _labels = d.pop("labels", UNSET)
        labels: CreateManagedEnvironmentRequestLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = CreateManagedEnvironmentRequestLabels.from_dict(_labels)

        def _parse_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_version(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        version = _parse_version(d.pop("version", UNSET))

        create_managed_environment_request = cls(
            template_id=template_id,
            labels=labels,
            name=name,
            version=version,
        )

        return create_managed_environment_request
