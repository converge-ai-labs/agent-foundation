from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.connector_setup_launch_status import ConnectorSetupLaunchStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connector_connection import ConnectorConnection


T = TypeVar("T", bound="ConnectorSetupLaunch")


@_attrs_define(repr=False)
class ConnectorSetupLaunch:
    """
    Attributes:
        attempt_id (str):
        connection (ConnectorConnection):
        expires_at (datetime.datetime):
        status (ConnectorSetupLaunchStatus):
        redirect_url (None | str | Unset):
        requires_browser_callback (bool | Unset):
    """

    attempt_id: str
    connection: ConnectorConnection
    expires_at: datetime.datetime
    status: ConnectorSetupLaunchStatus
    redirect_url: str | Unset | None = UNSET
    requires_browser_callback: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        attempt_id = self.attempt_id

        connection = self.connection.to_dict()

        expires_at = self.expires_at.isoformat()

        status = self.status.value

        redirect_url: str | Unset | None
        if isinstance(self.redirect_url, Unset):
            redirect_url = UNSET
        else:
            redirect_url = self.redirect_url

        requires_browser_callback = self.requires_browser_callback

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "attempt_id": attempt_id,
                "connection": connection,
                "expires_at": expires_at,
                "status": status,
            }
        )
        if redirect_url is not UNSET:
            field_dict["redirect_url"] = redirect_url
        if requires_browser_callback is not UNSET:
            field_dict["requires_browser_callback"] = requires_browser_callback

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_connection import ConnectorConnection

        d = dict(src_dict)
        attempt_id = d.pop("attempt_id")

        connection = ConnectorConnection.from_dict(d.pop("connection"))

        expires_at = datetime.datetime.fromisoformat(d.pop("expires_at"))

        status = ConnectorSetupLaunchStatus(d.pop("status"))

        def _parse_redirect_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        redirect_url = _parse_redirect_url(d.pop("redirect_url", UNSET))

        requires_browser_callback = d.pop("requires_browser_callback", UNSET)

        connector_setup_launch = cls(
            attempt_id=attempt_id,
            connection=connection,
            expires_at=expires_at,
            status=status,
            redirect_url=redirect_url,
            requires_browser_callback=requires_browser_callback,
        )

        return connector_setup_launch
