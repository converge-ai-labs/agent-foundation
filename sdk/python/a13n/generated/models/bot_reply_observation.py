from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.bot_reply_observation_provider_key import BotReplyObservationProviderKey
from ..models.bot_reply_observation_status import BotReplyObservationStatus

if TYPE_CHECKING:
    from ..models.lark_reply_receipt import LarkReplyReceipt
    from ..models.slack_reply_receipt import SlackReplyReceipt


T = TypeVar("T", bound="BotReplyObservation")


@_attrs_define(repr=False)
class BotReplyObservation:
    """
    Attributes:
        account_version (int):
        credential_generation (int):
        error_code (None | str):
        finished_at (datetime.datetime | None):
        id (str):
        provider_key (BotReplyObservationProviderKey):
        receipt (LarkReplyReceipt | None | SlackReplyReceipt):
        run_attempt_id (str):
        run_id (str):
        started_at (datetime.datetime):
        status (BotReplyObservationStatus):
        target_id (None | str):
        test_id (None | str):
    """

    account_version: int
    credential_generation: int
    error_code: str | None
    finished_at: datetime.datetime | None
    id: str
    provider_key: BotReplyObservationProviderKey
    receipt: LarkReplyReceipt | SlackReplyReceipt | None
    run_attempt_id: str
    run_id: str
    started_at: datetime.datetime
    status: BotReplyObservationStatus
    target_id: str | None
    test_id: str | None

    def to_dict(self) -> dict[str, Any]:
        from ..models.lark_reply_receipt import LarkReplyReceipt
        from ..models.slack_reply_receipt import SlackReplyReceipt

        account_version = self.account_version

        credential_generation = self.credential_generation

        error_code: str | None
        error_code = self.error_code

        finished_at: str | None
        if isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        id = self.id

        provider_key = self.provider_key.value

        receipt: dict[str, Any] | None
        if isinstance(self.receipt, SlackReplyReceipt):
            receipt = self.receipt.to_dict()
        elif isinstance(self.receipt, LarkReplyReceipt):
            receipt = self.receipt.to_dict()
        else:
            receipt = self.receipt

        run_attempt_id = self.run_attempt_id

        run_id = self.run_id

        started_at = self.started_at.isoformat()

        status = self.status.value

        target_id: str | None
        target_id = self.target_id

        test_id: str | None
        test_id = self.test_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "account_version": account_version,
                "credential_generation": credential_generation,
                "error_code": error_code,
                "finished_at": finished_at,
                "id": id,
                "provider_key": provider_key,
                "receipt": receipt,
                "run_attempt_id": run_attempt_id,
                "run_id": run_id,
                "started_at": started_at,
                "status": status,
                "target_id": target_id,
                "test_id": test_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.lark_reply_receipt import LarkReplyReceipt
        from ..models.slack_reply_receipt import SlackReplyReceipt

        d = dict(src_dict)
        account_version = d.pop("account_version")

        credential_generation = d.pop("credential_generation")

        def _parse_error_code(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        error_code = _parse_error_code(d.pop("error_code"))

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

        id = d.pop("id")

        provider_key = BotReplyObservationProviderKey(d.pop("provider_key"))

        def _parse_receipt(data: object) -> LarkReplyReceipt | SlackReplyReceipt | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                receipt_type_0 = SlackReplyReceipt.from_dict(data)

                return receipt_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                receipt_type_1 = LarkReplyReceipt.from_dict(data)

                return receipt_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(LarkReplyReceipt | SlackReplyReceipt | None, data)

        receipt = _parse_receipt(d.pop("receipt"))

        run_attempt_id = d.pop("run_attempt_id")

        run_id = d.pop("run_id")

        started_at = datetime.datetime.fromisoformat(d.pop("started_at"))

        status = BotReplyObservationStatus(d.pop("status"))

        def _parse_target_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        target_id = _parse_target_id(d.pop("target_id"))

        def _parse_test_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        test_id = _parse_test_id(d.pop("test_id"))

        bot_reply_observation = cls(
            account_version=account_version,
            credential_generation=credential_generation,
            error_code=error_code,
            finished_at=finished_at,
            id=id,
            provider_key=provider_key,
            receipt=receipt,
            run_attempt_id=run_attempt_id,
            run_id=run_id,
            started_at=started_at,
            status=status,
            target_id=target_id,
            test_id=test_id,
        )

        return bot_reply_observation
