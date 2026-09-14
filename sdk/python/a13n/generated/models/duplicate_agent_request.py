from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.duplicate_agent_request_labels import DuplicateAgentRequestLabels


T = TypeVar("T", bound="DuplicateAgentRequest")


@_attrs_define(repr=False)
class DuplicateAgentRequest:
    """
    Attributes:
        expected_version (int):
        name (str):
        description (None | str | Unset):
        key (None | str | Unset):
        labels (DuplicateAgentRequestLabels | Unset):
    """

    expected_version: int
    name: str
    description: str | Unset | None = UNSET
    key: str | Unset | None = UNSET
    labels: DuplicateAgentRequestLabels | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        name = self.name

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        key: str | Unset | None
        if isinstance(self.key, Unset):
            key = UNSET
        else:
            key = self.key

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if key is not UNSET:
            field_dict["key"] = key
        if labels is not UNSET:
            field_dict["labels"] = labels

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.duplicate_agent_request_labels import DuplicateAgentRequestLabels

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        name = d.pop("name")

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_key(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        key = _parse_key(d.pop("key", UNSET))

        _labels = d.pop("labels", UNSET)
        labels: DuplicateAgentRequestLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = DuplicateAgentRequestLabels.from_dict(_labels)

        duplicate_agent_request = cls(
            expected_version=expected_version,
            name=name,
            description=description,
            key=key,
            labels=labels,
        )

        return duplicate_agent_request
