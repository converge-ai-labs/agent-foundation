from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.environment_command_action import EnvironmentCommandAction
from ..models.environment_command_status import EnvironmentCommandStatus

T = TypeVar("T", bound="EnvironmentCommand")


@_attrs_define(repr=False)
class EnvironmentCommand:
    """
    Attributes:
        action (EnvironmentCommandAction):
        completed_at (datetime.datetime | None):
        created_at (datetime.datetime):
        environment_id (str):
        id (str):
        status (EnvironmentCommandStatus):
    """

    action: EnvironmentCommandAction
    completed_at: datetime.datetime | None
    created_at: datetime.datetime
    environment_id: str
    id: str
    status: EnvironmentCommandStatus

    def to_dict(self) -> dict[str, Any]:
        action = self.action.value

        completed_at: str | None
        if isinstance(self.completed_at, datetime.datetime):
            completed_at = self.completed_at.isoformat()
        else:
            completed_at = self.completed_at

        created_at = self.created_at.isoformat()

        environment_id = self.environment_id

        id = self.id

        status = self.status.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "action": action,
                "completed_at": completed_at,
                "created_at": created_at,
                "environment_id": environment_id,
                "id": id,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        action = EnvironmentCommandAction(d.pop("action"))

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

        environment_id = d.pop("environment_id")

        id = d.pop("id")

        status = EnvironmentCommandStatus(d.pop("status"))

        environment_command = cls(
            action=action,
            completed_at=completed_at,
            created_at=created_at,
            environment_id=environment_id,
            id=id,
            status=status,
        )

        return environment_command
