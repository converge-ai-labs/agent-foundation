from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.connection_check_scope import ConnectionCheckScope
from ..models.connection_check_status import ConnectionCheckStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="ConnectionCheck")


@_attrs_define(repr=False)
class ConnectionCheck:
    """
    Attributes:
        checked_at (datetime.datetime):
        scope (ConnectionCheckScope):
        status (ConnectionCheckStatus):
        error_code (None | str | Unset):
    """

    checked_at: datetime.datetime
    scope: ConnectionCheckScope
    status: ConnectionCheckStatus
    error_code: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        checked_at = self.checked_at.isoformat()

        scope = self.scope.value

        status = self.status.value

        error_code: str | Unset | None
        if isinstance(self.error_code, Unset):
            error_code = UNSET
        else:
            error_code = self.error_code

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "checked_at": checked_at,
                "scope": scope,
                "status": status,
            }
        )
        if error_code is not UNSET:
            field_dict["error_code"] = error_code

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        checked_at = datetime.datetime.fromisoformat(d.pop("checked_at"))

        scope = ConnectionCheckScope(d.pop("scope"))

        status = ConnectionCheckStatus(d.pop("status"))

        def _parse_error_code(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        error_code = _parse_error_code(d.pop("error_code", UNSET))

        connection_check = cls(
            checked_at=checked_at,
            scope=scope,
            status=status,
            error_code=error_code,
        )

        return connection_check
