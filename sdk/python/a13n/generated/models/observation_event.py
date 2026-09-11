from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.observation_event_attributes import ObservationEventAttributes


T = TypeVar("T", bound="ObservationEvent")


@_attrs_define(repr=False)
class ObservationEvent:
    """
    Attributes:
        attributes (ObservationEventAttributes):
        name (str):
        occurred_at (datetime.datetime):
    """

    attributes: ObservationEventAttributes
    name: str
    occurred_at: datetime.datetime

    def to_dict(self) -> dict[str, Any]:
        attributes = self.attributes.to_dict()

        name = self.name

        occurred_at = self.occurred_at.isoformat()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "attributes": attributes,
                "name": name,
                "occurred_at": occurred_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observation_event_attributes import ObservationEventAttributes

        d = dict(src_dict)
        attributes = ObservationEventAttributes.from_dict(d.pop("attributes"))

        name = d.pop("name")

        occurred_at = datetime.datetime.fromisoformat(d.pop("occurred_at"))

        observation_event = cls(
            attributes=attributes,
            name=name,
            occurred_at=occurred_at,
        )

        return observation_event
