from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.trace_summary_run_attempt_outcome_type_0 import TraceSummaryRunAttemptOutcomeType0
from ..models.trace_summary_trace_status import TraceSummaryTraceStatus

if TYPE_CHECKING:
    from ..models.trace_summary_usage_type_0 import TraceSummaryUsageType0


T = TypeVar("T", bound="TraceSummary")


@_attrs_define(repr=False)
class TraceSummary:
    """
    Attributes:
        duration_ms (int | None):
        ended_at (datetime.datetime | None):
        id (str):
        input_ (Any | None):
        models (list[str]):
        name (str):
        observation_count (int | None):
        output (Any | None):
        run_attempt_id (str):
        run_attempt_number (int):
        run_attempt_outcome (None | TraceSummaryRunAttemptOutcomeType0):
        run_id (str):
        session_id (str):
        source_url (None | str):
        started_at (datetime.datetime):
        thread_id (str):
        total_cost_usd (None | str):
        trace_status (TraceSummaryTraceStatus):
        usage (None | TraceSummaryUsageType0):
    """

    duration_ms: int | None
    ended_at: datetime.datetime | None
    id: str
    input_: Any | None
    models: list[str]
    name: str
    observation_count: int | None
    output: Any | None
    run_attempt_id: str
    run_attempt_number: int
    run_attempt_outcome: TraceSummaryRunAttemptOutcomeType0 | None
    run_id: str
    session_id: str
    source_url: str | None
    started_at: datetime.datetime
    thread_id: str
    total_cost_usd: str | None
    trace_status: TraceSummaryTraceStatus
    usage: TraceSummaryUsageType0 | None

    def to_dict(self) -> dict[str, Any]:
        from ..models.trace_summary_usage_type_0 import TraceSummaryUsageType0

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

        models = self.models

        name = self.name

        observation_count: int | None
        observation_count = self.observation_count

        output: Any | None
        output = self.output

        run_attempt_id = self.run_attempt_id

        run_attempt_number = self.run_attempt_number

        run_attempt_outcome: str | None
        if isinstance(self.run_attempt_outcome, TraceSummaryRunAttemptOutcomeType0):
            run_attempt_outcome = self.run_attempt_outcome.value
        else:
            run_attempt_outcome = self.run_attempt_outcome

        run_id = self.run_id

        session_id = self.session_id

        source_url: str | None
        source_url = self.source_url

        started_at = self.started_at.isoformat()

        thread_id = self.thread_id

        total_cost_usd: str | None
        total_cost_usd = self.total_cost_usd

        trace_status = self.trace_status.value

        usage: dict[str, Any] | None
        if isinstance(self.usage, TraceSummaryUsageType0):
            usage = self.usage.to_dict()
        else:
            usage = self.usage

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "duration_ms": duration_ms,
                "ended_at": ended_at,
                "id": id,
                "input": input_,
                "models": models,
                "name": name,
                "observation_count": observation_count,
                "output": output,
                "run_attempt_id": run_attempt_id,
                "run_attempt_number": run_attempt_number,
                "run_attempt_outcome": run_attempt_outcome,
                "run_id": run_id,
                "session_id": session_id,
                "source_url": source_url,
                "started_at": started_at,
                "thread_id": thread_id,
                "total_cost_usd": total_cost_usd,
                "trace_status": trace_status,
                "usage": usage,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.trace_summary_usage_type_0 import TraceSummaryUsageType0

        d = dict(src_dict)

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

        models = cast(list[str], d.pop("models"))

        name = d.pop("name")

        def _parse_observation_count(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        observation_count = _parse_observation_count(d.pop("observation_count"))

        def _parse_output(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        output = _parse_output(d.pop("output"))

        run_attempt_id = d.pop("run_attempt_id")

        run_attempt_number = d.pop("run_attempt_number")

        def _parse_run_attempt_outcome(data: object) -> TraceSummaryRunAttemptOutcomeType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                run_attempt_outcome_type_0 = TraceSummaryRunAttemptOutcomeType0(data)

                return run_attempt_outcome_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(TraceSummaryRunAttemptOutcomeType0 | None, data)

        run_attempt_outcome = _parse_run_attempt_outcome(d.pop("run_attempt_outcome"))

        run_id = d.pop("run_id")

        session_id = d.pop("session_id")

        def _parse_source_url(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        source_url = _parse_source_url(d.pop("source_url"))

        started_at = datetime.datetime.fromisoformat(d.pop("started_at"))

        thread_id = d.pop("thread_id")

        def _parse_total_cost_usd(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        total_cost_usd = _parse_total_cost_usd(d.pop("total_cost_usd"))

        trace_status = TraceSummaryTraceStatus(d.pop("trace_status"))

        def _parse_usage(data: object) -> TraceSummaryUsageType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                usage_type_0 = TraceSummaryUsageType0.from_dict(data)

                return usage_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(TraceSummaryUsageType0 | None, data)

        usage = _parse_usage(d.pop("usage"))

        trace_summary = cls(
            duration_ms=duration_ms,
            ended_at=ended_at,
            id=id,
            input_=input_,
            models=models,
            name=name,
            observation_count=observation_count,
            output=output,
            run_attempt_id=run_attempt_id,
            run_attempt_number=run_attempt_number,
            run_attempt_outcome=run_attempt_outcome,
            run_id=run_id,
            session_id=session_id,
            source_url=source_url,
            started_at=started_at,
            thread_id=thread_id,
            total_cost_usd=total_cost_usd,
            trace_status=trace_status,
            usage=usage,
        )

        return trace_summary
