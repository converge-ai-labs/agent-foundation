from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.run_lineage_kind import RunLineageKind
from ..models.run_status import RunStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.run_resource_labels import RunResourceLabels


T = TypeVar("T", bound="RunResource")


@_attrs_define(repr=False)
class RunResource:
    """
    Attributes:
        agent_id (str):
        agent_revision_id (str):
        completed_at (datetime.datetime | None):
        created_at (datetime.datetime):
        effective_agent_config_digest (str):
        environment_access (None | str):
        environment_id (None | str):
        failure (Any | None):
        id (str):
        input_ (Any | None):
        input_kind (str):
        input_text (None | str):
        labels (RunResourceLabels):
        lineage_kind (RunLineageKind):
        output (Any | None):
        output_text (None | str):
        parent_run_id (None | str):
        pending (Any | None):
        retry_of_run_id (None | str):
        sealed_at (datetime.datetime | None):
        sealed_state_digest_sha256 (None | str):
        session_id (str):
        started_at (datetime.datetime | None):
        status (RunStatus):
        thread_id (str):
        trigger_type (str):
        updated_at (datetime.datetime):
        version (int):
        wait_reason (None | str):
        waiting_at (datetime.datetime | None):
        configuration_draft_id (None | str | Unset):
    """

    agent_id: str
    agent_revision_id: str
    completed_at: datetime.datetime | None
    created_at: datetime.datetime
    effective_agent_config_digest: str
    environment_access: str | None
    environment_id: str | None
    failure: Any | None
    id: str
    input_: Any | None
    input_kind: str
    input_text: str | None
    labels: RunResourceLabels
    lineage_kind: RunLineageKind
    output: Any | None
    output_text: str | None
    parent_run_id: str | None
    pending: Any | None
    retry_of_run_id: str | None
    sealed_at: datetime.datetime | None
    sealed_state_digest_sha256: str | None
    session_id: str
    started_at: datetime.datetime | None
    status: RunStatus
    thread_id: str
    trigger_type: str
    updated_at: datetime.datetime
    version: int
    wait_reason: str | None
    waiting_at: datetime.datetime | None
    configuration_draft_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        agent_id = self.agent_id

        agent_revision_id = self.agent_revision_id

        completed_at: str | None
        if isinstance(self.completed_at, datetime.datetime):
            completed_at = self.completed_at.isoformat()
        else:
            completed_at = self.completed_at

        created_at = self.created_at.isoformat()

        effective_agent_config_digest = self.effective_agent_config_digest

        environment_access: str | None
        environment_access = self.environment_access

        environment_id: str | None
        environment_id = self.environment_id

        failure: Any | None
        failure = self.failure

        id = self.id

        input_: Any | None
        input_ = self.input_

        input_kind = self.input_kind

        input_text: str | None
        input_text = self.input_text

        labels = self.labels.to_dict()

        lineage_kind = self.lineage_kind.value

        output: Any | None
        output = self.output

        output_text: str | None
        output_text = self.output_text

        parent_run_id: str | None
        parent_run_id = self.parent_run_id

        pending: Any | None
        pending = self.pending

        retry_of_run_id: str | None
        retry_of_run_id = self.retry_of_run_id

        sealed_at: str | None
        if isinstance(self.sealed_at, datetime.datetime):
            sealed_at = self.sealed_at.isoformat()
        else:
            sealed_at = self.sealed_at

        sealed_state_digest_sha256: str | None
        sealed_state_digest_sha256 = self.sealed_state_digest_sha256

        session_id = self.session_id

        started_at: str | None
        if isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        status = self.status.value

        thread_id = self.thread_id

        trigger_type = self.trigger_type

        updated_at = self.updated_at.isoformat()

        version = self.version

        wait_reason: str | None
        wait_reason = self.wait_reason

        waiting_at: str | None
        if isinstance(self.waiting_at, datetime.datetime):
            waiting_at = self.waiting_at.isoformat()
        else:
            waiting_at = self.waiting_at

        configuration_draft_id: str | Unset | None
        if isinstance(self.configuration_draft_id, Unset):
            configuration_draft_id = UNSET
        else:
            configuration_draft_id = self.configuration_draft_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent_id": agent_id,
                "agent_revision_id": agent_revision_id,
                "completed_at": completed_at,
                "created_at": created_at,
                "effective_agent_config_digest": effective_agent_config_digest,
                "environment_access": environment_access,
                "environment_id": environment_id,
                "failure": failure,
                "id": id,
                "input": input_,
                "input_kind": input_kind,
                "input_text": input_text,
                "labels": labels,
                "lineage_kind": lineage_kind,
                "output": output,
                "output_text": output_text,
                "parent_run_id": parent_run_id,
                "pending": pending,
                "retry_of_run_id": retry_of_run_id,
                "sealed_at": sealed_at,
                "sealed_state_digest_sha256": sealed_state_digest_sha256,
                "session_id": session_id,
                "started_at": started_at,
                "status": status,
                "thread_id": thread_id,
                "trigger_type": trigger_type,
                "updated_at": updated_at,
                "version": version,
                "wait_reason": wait_reason,
                "waiting_at": waiting_at,
            }
        )
        if configuration_draft_id is not UNSET:
            field_dict["configuration_draft_id"] = configuration_draft_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.run_resource_labels import RunResourceLabels

        d = dict(src_dict)
        agent_id = d.pop("agent_id")

        agent_revision_id = d.pop("agent_revision_id")

        def _parse_completed_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                completed_at_type_0 = datetime.datetime.fromisoformat(data)

                return completed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        completed_at = _parse_completed_at(d.pop("completed_at"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        effective_agent_config_digest = d.pop("effective_agent_config_digest")

        def _parse_environment_access(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        environment_access = _parse_environment_access(d.pop("environment_access"))

        def _parse_environment_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        environment_id = _parse_environment_id(d.pop("environment_id"))

        def _parse_failure(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        failure = _parse_failure(d.pop("failure"))

        id = d.pop("id")

        def _parse_input_(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        input_ = _parse_input_(d.pop("input"))

        input_kind = d.pop("input_kind")

        def _parse_input_text(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        input_text = _parse_input_text(d.pop("input_text"))

        labels = RunResourceLabels.from_dict(d.pop("labels"))

        lineage_kind = RunLineageKind(d.pop("lineage_kind"))

        def _parse_output(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        output = _parse_output(d.pop("output"))

        def _parse_output_text(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        output_text = _parse_output_text(d.pop("output_text"))

        def _parse_parent_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        parent_run_id = _parse_parent_run_id(d.pop("parent_run_id"))

        def _parse_pending(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        pending = _parse_pending(d.pop("pending"))

        def _parse_retry_of_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        retry_of_run_id = _parse_retry_of_run_id(d.pop("retry_of_run_id"))

        def _parse_sealed_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                sealed_at_type_0 = datetime.datetime.fromisoformat(data)

                return sealed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        sealed_at = _parse_sealed_at(d.pop("sealed_at"))

        def _parse_sealed_state_digest_sha256(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        sealed_state_digest_sha256 = _parse_sealed_state_digest_sha256(d.pop("sealed_state_digest_sha256"))

        session_id = d.pop("session_id")

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

        status = RunStatus(d.pop("status"))

        thread_id = d.pop("thread_id")

        trigger_type = d.pop("trigger_type")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        def _parse_wait_reason(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        wait_reason = _parse_wait_reason(d.pop("wait_reason"))

        def _parse_waiting_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                waiting_at_type_0 = datetime.datetime.fromisoformat(data)

                return waiting_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        waiting_at = _parse_waiting_at(d.pop("waiting_at"))

        def _parse_configuration_draft_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        configuration_draft_id = _parse_configuration_draft_id(d.pop("configuration_draft_id", UNSET))

        run_resource = cls(
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            completed_at=completed_at,
            created_at=created_at,
            effective_agent_config_digest=effective_agent_config_digest,
            environment_access=environment_access,
            environment_id=environment_id,
            failure=failure,
            id=id,
            input_=input_,
            input_kind=input_kind,
            input_text=input_text,
            labels=labels,
            lineage_kind=lineage_kind,
            output=output,
            output_text=output_text,
            parent_run_id=parent_run_id,
            pending=pending,
            retry_of_run_id=retry_of_run_id,
            sealed_at=sealed_at,
            sealed_state_digest_sha256=sealed_state_digest_sha256,
            session_id=session_id,
            started_at=started_at,
            status=status,
            thread_id=thread_id,
            trigger_type=trigger_type,
            updated_at=updated_at,
            version=version,
            wait_reason=wait_reason,
            waiting_at=waiting_at,
            configuration_draft_id=configuration_draft_id,
        )

        return run_resource
