from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.bot_reply_observation import BotReplyObservation


T = TypeVar("T", bound="BotTest")


@_attrs_define(repr=False)
class BotTest:
    """
    Attributes:
        accepted_at (datetime.datetime | None):
        account_id (str):
        account_version (int):
        admission_id (None | str):
        created_at (datetime.datetime):
        credential_generation (int):
        event_received_at (datetime.datetime | None):
        expires_at (datetime.datetime):
        external_target_id (str):
        id (str):
        rejection_code (None | str):
        run_id (None | str):
        stale (bool):
        steer_id (None | str):
        target_id (str):
        target_version (int):
        reply (BotReplyObservation | None | Unset):
        session_id (None | str | Unset):
        thread_id (None | str | Unset):
    """

    accepted_at: datetime.datetime | None
    account_id: str
    account_version: int
    admission_id: str | None
    created_at: datetime.datetime
    credential_generation: int
    event_received_at: datetime.datetime | None
    expires_at: datetime.datetime
    external_target_id: str
    id: str
    rejection_code: str | None
    run_id: str | None
    stale: bool
    steer_id: str | None
    target_id: str
    target_version: int
    reply: BotReplyObservation | Unset | None = UNSET
    session_id: str | Unset | None = UNSET
    thread_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.bot_reply_observation import BotReplyObservation

        accepted_at: str | None
        if isinstance(self.accepted_at, datetime.datetime):
            accepted_at = self.accepted_at.isoformat()
        else:
            accepted_at = self.accepted_at

        account_id = self.account_id

        account_version = self.account_version

        admission_id: str | None
        admission_id = self.admission_id

        created_at = self.created_at.isoformat()

        credential_generation = self.credential_generation

        event_received_at: str | None
        if isinstance(self.event_received_at, datetime.datetime):
            event_received_at = self.event_received_at.isoformat()
        else:
            event_received_at = self.event_received_at

        expires_at = self.expires_at.isoformat()

        external_target_id = self.external_target_id

        id = self.id

        rejection_code: str | None
        rejection_code = self.rejection_code

        run_id: str | None
        run_id = self.run_id

        stale = self.stale

        steer_id: str | None
        steer_id = self.steer_id

        target_id = self.target_id

        target_version = self.target_version

        reply: dict[str, Any] | Unset | None
        if isinstance(self.reply, Unset):
            reply = UNSET
        elif isinstance(self.reply, BotReplyObservation):
            reply = self.reply.to_dict()
        else:
            reply = self.reply

        session_id: str | Unset | None
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        thread_id: str | Unset | None
        if isinstance(self.thread_id, Unset):
            thread_id = UNSET
        else:
            thread_id = self.thread_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "accepted_at": accepted_at,
                "account_id": account_id,
                "account_version": account_version,
                "admission_id": admission_id,
                "created_at": created_at,
                "credential_generation": credential_generation,
                "event_received_at": event_received_at,
                "expires_at": expires_at,
                "external_target_id": external_target_id,
                "id": id,
                "rejection_code": rejection_code,
                "run_id": run_id,
                "stale": stale,
                "steer_id": steer_id,
                "target_id": target_id,
                "target_version": target_version,
            }
        )
        if reply is not UNSET:
            field_dict["reply"] = reply
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if thread_id is not UNSET:
            field_dict["thread_id"] = thread_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.bot_reply_observation import BotReplyObservation

        d = dict(src_dict)

        def _parse_accepted_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                accepted_at_type_0 = datetime.datetime.fromisoformat(data)

                return accepted_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        accepted_at = _parse_accepted_at(d.pop("accepted_at"))

        account_id = d.pop("account_id")

        account_version = d.pop("account_version")

        def _parse_admission_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        admission_id = _parse_admission_id(d.pop("admission_id"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        credential_generation = d.pop("credential_generation")

        def _parse_event_received_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                event_received_at_type_0 = datetime.datetime.fromisoformat(data)

                return event_received_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        event_received_at = _parse_event_received_at(d.pop("event_received_at"))

        expires_at = datetime.datetime.fromisoformat(d.pop("expires_at"))

        external_target_id = d.pop("external_target_id")

        id = d.pop("id")

        def _parse_rejection_code(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        rejection_code = _parse_rejection_code(d.pop("rejection_code"))

        def _parse_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        run_id = _parse_run_id(d.pop("run_id"))

        stale = d.pop("stale")

        def _parse_steer_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        steer_id = _parse_steer_id(d.pop("steer_id"))

        target_id = d.pop("target_id")

        target_version = d.pop("target_version")

        def _parse_reply(data: object) -> BotReplyObservation | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                reply_type_0 = BotReplyObservation.from_dict(data)

                return reply_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BotReplyObservation | Unset | None, data)

        reply = _parse_reply(d.pop("reply", UNSET))

        def _parse_session_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        def _parse_thread_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        thread_id = _parse_thread_id(d.pop("thread_id", UNSET))

        bot_test = cls(
            accepted_at=accepted_at,
            account_id=account_id,
            account_version=account_version,
            admission_id=admission_id,
            created_at=created_at,
            credential_generation=credential_generation,
            event_received_at=event_received_at,
            expires_at=expires_at,
            external_target_id=external_target_id,
            id=id,
            rejection_code=rejection_code,
            run_id=run_id,
            stale=stale,
            steer_id=steer_id,
            target_id=target_id,
            target_version=target_version,
            reply=reply,
            session_id=session_id,
            thread_id=thread_id,
        )

        return bot_test
