from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.observation_status import ObservationStatus
from ..models.observation_type import ObservationType

if TYPE_CHECKING:
    from ..models.observation_metadata import ObservationMetadata
    from ..models.observation_usage_type_0 import ObservationUsageType0


T = TypeVar("T", bound="Observation")


@_attrs_define(repr=False)
class Observation:
    """
    Attributes:
        cost_usd (None | str):
        duration_ms (int | None):
        ended_at (datetime.datetime | None):
        id (str):
        input_ (Any | None):
        metadata (ObservationMetadata):
        model (None | str):
        name (str):
        output (Any | None):
        parent_id (None | str):
        started_at (datetime.datetime):
        status (ObservationStatus):
        type_ (ObservationType):
        usage (None | ObservationUsageType0):
    """

    cost_usd: str | None
    duration_ms: int | None
    ended_at: datetime.datetime | None
    id: str
    input_: Any | None
    metadata: ObservationMetadata
    model: str | None
    name: str
    output: Any | None
    parent_id: str | None
    started_at: datetime.datetime
    status: ObservationStatus
    type_: ObservationType
    usage: ObservationUsageType0 | None

    def to_dict(self) -> dict[str, Any]:
        from ..models.observation_usage_type_0 import ObservationUsageType0

        cost_usd: str | None
        cost_usd = self.cost_usd

        duration_ms: int | None
        duration_ms = self.duration_ms

        ended_at: str | None
        if isinstance(self.ended_at, datetime.datetime):
            ended_at = self.ended_at.isoformat()
        else:
            ended_at = self.ended_at

        id = self.id

        input_: Any | None
        input_ = self.input_

        metadata = self.metadata.to_dict()

        model: str | None
        model = self.model

        name = self.name

        output: Any | None
        output = self.output

        parent_id: str | None
        parent_id = self.parent_id

        started_at = self.started_at.isoformat()

        status = self.status.value

        type_ = self.type_.value

        usage: dict[str, Any] | None
        if isinstance(self.usage, ObservationUsageType0):
            usage = self.usage.to_dict()
        else:
            usage = self.usage

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "cost_usd": cost_usd,
                "duration_ms": duration_ms,
                "ended_at": ended_at,
                "id": id,
                "input": input_,
                "metadata": metadata,
                "model": model,
                "name": name,
                "output": output,
                "parent_id": parent_id,
                "started_at": started_at,
                "status": status,
                "type": type_,
                "usage": usage,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observation_metadata import ObservationMetadata
        from ..models.observation_usage_type_0 import ObservationUsageType0

        d = dict(src_dict)

        def _parse_cost_usd(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        cost_usd = _parse_cost_usd(d.pop("cost_usd"))

        def _parse_duration_ms(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        duration_ms = _parse_duration_ms(d.pop("duration_ms"))

        def _parse_ended_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                ended_at_type_0 = datetime.datetime.fromisoformat(data)

                return ended_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        ended_at = _parse_ended_at(d.pop("ended_at"))

        id = d.pop("id")

        def _parse_input_(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        input_ = _parse_input_(d.pop("input"))

        metadata = ObservationMetadata.from_dict(d.pop("metadata"))

        def _parse_model(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        model = _parse_model(d.pop("model"))

        name = d.pop("name")

        def _parse_output(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        output = _parse_output(d.pop("output"))

        def _parse_parent_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        parent_id = _parse_parent_id(d.pop("parent_id"))

        started_at = datetime.datetime.fromisoformat(d.pop("started_at"))

        status = ObservationStatus(d.pop("status"))

        type_ = ObservationType(d.pop("type"))

        def _parse_usage(data: object) -> ObservationUsageType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                usage_type_0 = ObservationUsageType0.from_dict(data)

                return usage_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ObservationUsageType0 | None, data)

        usage = _parse_usage(d.pop("usage"))

        observation = cls(
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            ended_at=ended_at,
            id=id,
            input_=input_,
            metadata=metadata,
            model=model,
            name=name,
            output=output,
            parent_id=parent_id,
            started_at=started_at,
            status=status,
            type_=type_,
            usage=usage,
        )

        return observation
