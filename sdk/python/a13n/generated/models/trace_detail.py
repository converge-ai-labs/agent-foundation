from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.observation import Observation
    from ..models.trace_summary import TraceSummary


T = TypeVar("T", bound="TraceDetail")


@_attrs_define(repr=False)
class TraceDetail:
    """
    Attributes:
        observations (list[Observation]):
        trace (TraceSummary):
    """

    observations: list[Observation]
    trace: TraceSummary

    def to_dict(self) -> dict[str, Any]:
        observations = []
        for observations_item_data in self.observations:
            observations_item = observations_item_data.to_dict()
            observations.append(observations_item)

        trace = self.trace.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "observations": observations,
                "trace": trace,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observation import Observation
        from ..models.trace_summary import TraceSummary

        d = dict(src_dict)
        observations = []
        _observations = d.pop("observations")
        for observations_item_data in _observations:
            observations_item = Observation.from_dict(observations_item_data)

            observations.append(observations_item)

        trace = TraceSummary.from_dict(d.pop("trace"))

        trace_detail = cls(
            observations=observations,
            trace=trace,
        )

        return trace_detail
