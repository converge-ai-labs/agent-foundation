from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="RunAttemptResource")


@_attrs_define(repr=False)
class RunAttemptResource:
    """
    Attributes:
        attempt_number (int):
        created_at (datetime.datetime):
        failure (Any | None):
        finished_at (datetime.datetime | None):
        harness_run_id (None | str):
        id (str):
        replaces_run_attempt_id (None | str):
        run_id (str):
        start_reason (None | str):
        started_at (datetime.datetime | None):
        status (str):
        updated_at (datetime.datetime):
        version (int):
        worker_build_id (str):
        yield_reason (None | str):
    """

    attempt_number: int
    created_at: datetime.datetime
    failure: Any | None
    finished_at: datetime.datetime | None
    harness_run_id: str | None
    id: str
    replaces_run_attempt_id: str | None
    run_id: str
    start_reason: str | None
    started_at: datetime.datetime | None
    status: str
    updated_at: datetime.datetime
    version: int
    worker_build_id: str
    yield_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        attempt_number = self.attempt_number

        created_at = self.created_at.isoformat()

        failure: Any | None
        failure = self.failure

        finished_at: str | None
        if isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        harness_run_id: str | None
        harness_run_id = self.harness_run_id

        id = self.id

        replaces_run_attempt_id: str | None
        replaces_run_attempt_id = self.replaces_run_attempt_id

        run_id = self.run_id

        start_reason: str | None
        start_reason = self.start_reason

        started_at: str | None
        if isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        status = self.status

        updated_at = self.updated_at.isoformat()

        version = self.version

        worker_build_id = self.worker_build_id

        yield_reason: str | None
        yield_reason = self.yield_reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "attempt_number": attempt_number,
                "created_at": created_at,
                "failure": failure,
                "finished_at": finished_at,
                "harness_run_id": harness_run_id,
                "id": id,
                "replaces_run_attempt_id": replaces_run_attempt_id,
                "run_id": run_id,
                "start_reason": start_reason,
                "started_at": started_at,
                "status": status,
                "updated_at": updated_at,
                "version": version,
                "worker_build_id": worker_build_id,
                "yield_reason": yield_reason,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        attempt_number = d.pop("attempt_number")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_failure(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        failure = _parse_failure(d.pop("failure"))

        def _parse_finished_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finished_at_type_0 = datetime.datetime.fromisoformat(data)

                return finished_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        finished_at = _parse_finished_at(d.pop("finished_at"))

        def _parse_harness_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        harness_run_id = _parse_harness_run_id(d.pop("harness_run_id"))

        id = d.pop("id")

        def _parse_replaces_run_attempt_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        replaces_run_attempt_id = _parse_replaces_run_attempt_id(d.pop("replaces_run_attempt_id"))

        run_id = d.pop("run_id")

        def _parse_start_reason(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        start_reason = _parse_start_reason(d.pop("start_reason"))

        def _parse_started_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                started_at_type_0 = datetime.datetime.fromisoformat(data)

                return started_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        started_at = _parse_started_at(d.pop("started_at"))

        status = d.pop("status")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        worker_build_id = d.pop("worker_build_id")

        def _parse_yield_reason(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        yield_reason = _parse_yield_reason(d.pop("yield_reason"))

        run_attempt_resource = cls(
            attempt_number=attempt_number,
            created_at=created_at,
            failure=failure,
            finished_at=finished_at,
            harness_run_id=harness_run_id,
            id=id,
            replaces_run_attempt_id=replaces_run_attempt_id,
            run_id=run_id,
            start_reason=start_reason,
            started_at=started_at,
            status=status,
            updated_at=updated_at,
            version=version,
            worker_build_id=worker_build_id,
            yield_reason=yield_reason,
        )

        return run_attempt_resource
