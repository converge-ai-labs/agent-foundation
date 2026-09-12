from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.authorization_status import AuthorizationStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.authorization_action import AuthorizationAction


T = TypeVar("T", bound="Authorization")


@_attrs_define(repr=False)
class Authorization:
    """
    Attributes:
        connection_id (str):
        expires_at (datetime.datetime):
        id (str):
        status (AuthorizationStatus):
        updated_at (datetime.datetime):
        error_code (None | str | Unset):
        next_action (AuthorizationAction | None | Unset):
        outcome_unknown (bool | Unset):
    """

    connection_id: str
    expires_at: datetime.datetime
    id: str
    status: AuthorizationStatus
    updated_at: datetime.datetime
    error_code: str | Unset | None = UNSET
    next_action: AuthorizationAction | Unset | None = UNSET
    outcome_unknown: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.authorization_action import AuthorizationAction

        connection_id = self.connection_id

        expires_at = self.expires_at.isoformat()

        id = self.id

        status = self.status.value

        updated_at = self.updated_at.isoformat()

        error_code: str | Unset | None
        if isinstance(self.error_code, Unset):
            error_code = UNSET
        else:
            error_code = self.error_code

        next_action: dict[str, Any] | Unset | None
        if isinstance(self.next_action, Unset):
            next_action = UNSET
        elif isinstance(self.next_action, AuthorizationAction):
            next_action = self.next_action.to_dict()
        else:
            next_action = self.next_action

        outcome_unknown = self.outcome_unknown

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connection_id": connection_id,
                "expires_at": expires_at,
                "id": id,
                "status": status,
                "updated_at": updated_at,
            }
        )
        if error_code is not UNSET:
            field_dict["error_code"] = error_code
        if next_action is not UNSET:
            field_dict["next_action"] = next_action
        if outcome_unknown is not UNSET:
            field_dict["outcome_unknown"] = outcome_unknown

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.authorization_action import AuthorizationAction

        d = dict(src_dict)
        connection_id = d.pop("connection_id")

        expires_at = datetime.datetime.fromisoformat(d.pop("expires_at"))

        id = d.pop("id")

        status = AuthorizationStatus(d.pop("status"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        def _parse_error_code(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        error_code = _parse_error_code(d.pop("error_code", UNSET))

        def _parse_next_action(data: object) -> AuthorizationAction | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                next_action_type_0 = AuthorizationAction.from_dict(data)

                return next_action_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AuthorizationAction | Unset | None, data)

        next_action = _parse_next_action(d.pop("next_action", UNSET))

        outcome_unknown = d.pop("outcome_unknown", UNSET)

        authorization = cls(
            connection_id=connection_id,
            expires_at=expires_at,
            id=id,
            status=status,
            updated_at=updated_at,
            error_code=error_code,
            next_action=next_action,
            outcome_unknown=outcome_unknown,
        )

        return authorization
