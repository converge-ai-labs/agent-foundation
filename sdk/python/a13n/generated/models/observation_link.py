from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.observation_link_attributes_type_0 import ObservationLinkAttributesType0


T = TypeVar("T", bound="ObservationLink")


@_attrs_define(repr=False)
class ObservationLink:
    """
    Attributes:
        attributes (None | ObservationLinkAttributesType0):
        observation_id (str):
        trace_id (str):
    """

    attributes: ObservationLinkAttributesType0 | None
    observation_id: str
    trace_id: str

    def to_dict(self) -> dict[str, Any]:
        from ..models.observation_link_attributes_type_0 import ObservationLinkAttributesType0

        attributes: dict[str, Any] | None
        if isinstance(self.attributes, ObservationLinkAttributesType0):
            attributes = self.attributes.to_dict()
        else:
            attributes = self.attributes

        observation_id = self.observation_id

        trace_id = self.trace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "attributes": attributes,
                "observation_id": observation_id,
                "trace_id": trace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observation_link_attributes_type_0 import ObservationLinkAttributesType0

        d = dict(src_dict)

        def _parse_attributes(data: object) -> ObservationLinkAttributesType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                attributes_type_0 = ObservationLinkAttributesType0.from_dict(data)

                return attributes_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ObservationLinkAttributesType0 | None, data)

        attributes = _parse_attributes(d.pop("attributes"))

        observation_id = d.pop("observation_id")

        trace_id = d.pop("trace_id")

        observation_link = cls(
            attributes=attributes,
            observation_id=observation_id,
            trace_id=trace_id,
        )

        return observation_link
