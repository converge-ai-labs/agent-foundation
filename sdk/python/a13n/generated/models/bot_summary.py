from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.bot_summary_setup_condition import BotSummarySetupCondition
from ..models.bot_summary_test_stage_type_0 import BotSummaryTestStageType0

if TYPE_CHECKING:
    from ..models.account import Account
    from ..models.account_memory_settings import AccountMemorySettings


T = TypeVar("T", bound="BotSummary")


@_attrs_define(repr=False)
class BotSummary:
    """
    Attributes:
        account (Account):
        checked_at (datetime.datetime | None):
        configured_target_count (int):
        external_organization_id (None | str):
        external_organization_name (None | str):
        memory_settings (AccountMemorySettings):
        setup_condition (BotSummarySetupCondition):
        test_observed_at (datetime.datetime | None):
        test_stage (BotSummaryTestStageType0 | None):
    """

    account: Account
    checked_at: datetime.datetime | None
    configured_target_count: int
    external_organization_id: str | None
    external_organization_name: str | None
    memory_settings: AccountMemorySettings
    setup_condition: BotSummarySetupCondition
    test_observed_at: datetime.datetime | None
    test_stage: BotSummaryTestStageType0 | None

    def to_dict(self) -> dict[str, Any]:
        account = self.account.to_dict()

        checked_at: str | None
        if isinstance(self.checked_at, datetime.datetime):
            checked_at = self.checked_at.isoformat()
        else:
            checked_at = self.checked_at

        configured_target_count = self.configured_target_count

        external_organization_id: str | None
        external_organization_id = self.external_organization_id

        external_organization_name: str | None
        external_organization_name = self.external_organization_name

        memory_settings = self.memory_settings.to_dict()

        setup_condition = self.setup_condition.value

        test_observed_at: str | None
        if isinstance(self.test_observed_at, datetime.datetime):
            test_observed_at = self.test_observed_at.isoformat()
        else:
            test_observed_at = self.test_observed_at

        test_stage: str | None
        if isinstance(self.test_stage, BotSummaryTestStageType0):
            test_stage = self.test_stage.value
        else:
            test_stage = self.test_stage

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "account": account,
                "checked_at": checked_at,
                "configured_target_count": configured_target_count,
                "external_organization_id": external_organization_id,
                "external_organization_name": external_organization_name,
                "memory_settings": memory_settings,
                "setup_condition": setup_condition,
                "test_observed_at": test_observed_at,
                "test_stage": test_stage,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.account import Account
        from ..models.account_memory_settings import AccountMemorySettings

        d = dict(src_dict)
        account = Account.from_dict(d.pop("account"))

        def _parse_checked_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                checked_at_type_0 = datetime.datetime.fromisoformat(data)

                return checked_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        checked_at = _parse_checked_at(d.pop("checked_at"))

        configured_target_count = d.pop("configured_target_count")

        def _parse_external_organization_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        external_organization_id = _parse_external_organization_id(d.pop("external_organization_id"))

        def _parse_external_organization_name(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        external_organization_name = _parse_external_organization_name(d.pop("external_organization_name"))

        memory_settings = AccountMemorySettings.from_dict(d.pop("memory_settings"))

        setup_condition = BotSummarySetupCondition(d.pop("setup_condition"))

        def _parse_test_observed_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                test_observed_at_type_0 = datetime.datetime.fromisoformat(data)

                return test_observed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        test_observed_at = _parse_test_observed_at(d.pop("test_observed_at"))

        def _parse_test_stage(data: object) -> BotSummaryTestStageType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                test_stage_type_0 = BotSummaryTestStageType0(data)

                return test_stage_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BotSummaryTestStageType0 | None, data)

        test_stage = _parse_test_stage(d.pop("test_stage"))

        bot_summary = cls(
            account=account,
            checked_at=checked_at,
            configured_target_count=configured_target_count,
            external_organization_id=external_organization_id,
            external_organization_name=external_organization_name,
            memory_settings=memory_settings,
            setup_condition=setup_condition,
            test_observed_at=test_observed_at,
            test_stage=test_stage,
        )

        return bot_summary
