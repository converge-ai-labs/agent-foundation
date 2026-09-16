from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.conversation_info import ConversationInfo
    from ..models.installation_info import InstallationInfo


T = TypeVar("T", bound="BotCheck")


@_attrs_define(repr=False)
class BotCheck:
    """
    Attributes:
        account_id (str):
        checked_at (datetime.datetime):
        credential_generation (int):
        conversation (ConversationInfo | None | Unset):
        conversation_id (None | str | Unset):
        error_code (None | str | Unset):
        installation (InstallationInfo | None | Unset):
    """

    account_id: str
    checked_at: datetime.datetime
    credential_generation: int
    conversation: ConversationInfo | Unset | None = UNSET
    conversation_id: str | Unset | None = UNSET
    error_code: str | Unset | None = UNSET
    installation: InstallationInfo | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.conversation_info import ConversationInfo
        from ..models.installation_info import InstallationInfo

        account_id = self.account_id

        checked_at = self.checked_at.isoformat()

        credential_generation = self.credential_generation

        conversation: dict[str, Any] | Unset | None
        if isinstance(self.conversation, Unset):
            conversation = UNSET
        elif isinstance(self.conversation, ConversationInfo):
            conversation = self.conversation.to_dict()
        else:
            conversation = self.conversation

        conversation_id: str | Unset | None
        if isinstance(self.conversation_id, Unset):
            conversation_id = UNSET
        else:
            conversation_id = self.conversation_id

        error_code: str | Unset | None
        if isinstance(self.error_code, Unset):
            error_code = UNSET
        else:
            error_code = self.error_code

        installation: dict[str, Any] | Unset | None
        if isinstance(self.installation, Unset):
            installation = UNSET
        elif isinstance(self.installation, InstallationInfo):
            installation = self.installation.to_dict()
        else:
            installation = self.installation

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "account_id": account_id,
                "checked_at": checked_at,
                "credential_generation": credential_generation,
            }
        )
        if conversation is not UNSET:
            field_dict["conversation"] = conversation
        if conversation_id is not UNSET:
            field_dict["conversation_id"] = conversation_id
        if error_code is not UNSET:
            field_dict["error_code"] = error_code
        if installation is not UNSET:
            field_dict["installation"] = installation

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.conversation_info import ConversationInfo
        from ..models.installation_info import InstallationInfo

        d = dict(src_dict)
        account_id = d.pop("account_id")

        checked_at = datetime.datetime.fromisoformat(d.pop("checked_at"))

        credential_generation = d.pop("credential_generation")

        def _parse_conversation(data: object) -> ConversationInfo | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                conversation_type_0 = ConversationInfo.from_dict(data)

                return conversation_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConversationInfo | Unset | None, data)

        conversation = _parse_conversation(d.pop("conversation", UNSET))

        def _parse_conversation_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        conversation_id = _parse_conversation_id(d.pop("conversation_id", UNSET))

        def _parse_error_code(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        error_code = _parse_error_code(d.pop("error_code", UNSET))

        def _parse_installation(data: object) -> InstallationInfo | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                installation_type_0 = InstallationInfo.from_dict(data)

                return installation_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InstallationInfo | Unset | None, data)

        installation = _parse_installation(d.pop("installation", UNSET))

        bot_check = cls(
            account_id=account_id,
            checked_at=checked_at,
            credential_generation=credential_generation,
            conversation=conversation,
            conversation_id=conversation_id,
            error_code=error_code,
            installation=installation,
        )

        return bot_check
