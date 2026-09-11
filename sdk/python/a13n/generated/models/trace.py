from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.observation import Observation
    from ..models.trace_correlation import TraceCorrelation


T = TypeVar("T", bound="Trace")


@_attrs_define(repr=False)
class Trace:
    """
    Attributes:
        correlation (TraceCorrelation):
        id (str):
        provider (str):
        root (Observation):
        source_url (None | str):
    """

    correlation: TraceCorrelation
    id: str
    provider: str
    root: Observation
    source_url: str | None

    def to_dict(self) -> dict[str, Any]:
        correlation = self.correlation.to_dict()

        id = self.id

        provider = self.provider

        root = self.root.to_dict()

        source_url: str | None
        source_url = self.source_url

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "correlation": correlation,
                "id": id,
                "provider": provider,
                "root": root,
                "source_url": source_url,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observation import Observation
        from ..models.trace_correlation import TraceCorrelation

        d = dict(src_dict)
        correlation = TraceCorrelation.from_dict(d.pop("correlation"))

        id = d.pop("id")

        provider = d.pop("provider")

        root = Observation.from_dict(d.pop("root"))

        def _parse_source_url(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        source_url = _parse_source_url(d.pop("source_url"))

        trace = cls(
            correlation=correlation,
            id=id,
            provider=provider,
            root=root,
            source_url=source_url,
        )

        return trace
