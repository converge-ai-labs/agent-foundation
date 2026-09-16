from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.verification_acknowledgement import VerificationAcknowledgement


T = TypeVar("T", bound="ConfigurationApplicationReceipt")


@_attrs_define(repr=False)
class ConfigurationApplicationReceipt:
    """
    Attributes:
        agent_id (str):
        agent_revision_id (str):
        agent_version (int):
        applied_at (datetime.datetime):
        applied_by_user_id (str):
        draft_id (str):
        no_change (bool):
        reviewed_digest (str):
        reviewed_version (int):
        verification_acknowledgement (None | Unset | VerificationAcknowledgement):
        verification_run_ids (list[str] | Unset):
    """

    agent_id: str
    agent_revision_id: str
    agent_version: int
    applied_at: datetime.datetime
    applied_by_user_id: str
    draft_id: str
    no_change: bool
    reviewed_digest: str
    reviewed_version: int
    verification_acknowledgement: Unset | VerificationAcknowledgement | None = UNSET
    verification_run_ids: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.verification_acknowledgement import VerificationAcknowledgement

        agent_id = self.agent_id

        agent_revision_id = self.agent_revision_id

        agent_version = self.agent_version

        applied_at = self.applied_at.isoformat()

        applied_by_user_id = self.applied_by_user_id

        draft_id = self.draft_id

        no_change = self.no_change

        reviewed_digest = self.reviewed_digest

        reviewed_version = self.reviewed_version

        verification_acknowledgement: dict[str, Any] | Unset | None
        if isinstance(self.verification_acknowledgement, Unset):
            verification_acknowledgement = UNSET
        elif isinstance(self.verification_acknowledgement, VerificationAcknowledgement):
            verification_acknowledgement = self.verification_acknowledgement.to_dict()
        else:
            verification_acknowledgement = self.verification_acknowledgement

        verification_run_ids: list[str] | Unset = UNSET
        if not isinstance(self.verification_run_ids, Unset):
            verification_run_ids = self.verification_run_ids

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent_id": agent_id,
                "agent_revision_id": agent_revision_id,
                "agent_version": agent_version,
                "applied_at": applied_at,
                "applied_by_user_id": applied_by_user_id,
                "draft_id": draft_id,
                "no_change": no_change,
                "reviewed_digest": reviewed_digest,
                "reviewed_version": reviewed_version,
            }
        )
        if verification_acknowledgement is not UNSET:
            field_dict["verification_acknowledgement"] = verification_acknowledgement
        if verification_run_ids is not UNSET:
            field_dict["verification_run_ids"] = verification_run_ids

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.verification_acknowledgement import VerificationAcknowledgement

        d = dict(src_dict)
        agent_id = d.pop("agent_id")

        agent_revision_id = d.pop("agent_revision_id")

        agent_version = d.pop("agent_version")

        applied_at = datetime.datetime.fromisoformat(d.pop("applied_at"))

        applied_by_user_id = d.pop("applied_by_user_id")

        draft_id = d.pop("draft_id")

        no_change = d.pop("no_change")

        reviewed_digest = d.pop("reviewed_digest")

        reviewed_version = d.pop("reviewed_version")

        def _parse_verification_acknowledgement(data: object) -> Unset | VerificationAcknowledgement | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                verification_acknowledgement_type_0 = VerificationAcknowledgement.from_dict(data)

                return verification_acknowledgement_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Unset | VerificationAcknowledgement | None, data)

        verification_acknowledgement = _parse_verification_acknowledgement(d.pop("verification_acknowledgement", UNSET))

        verification_run_ids = cast(list[str], d.pop("verification_run_ids", UNSET))

        configuration_application_receipt = cls(
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            agent_version=agent_version,
            applied_at=applied_at,
            applied_by_user_id=applied_by_user_id,
            draft_id=draft_id,
            no_change=no_change,
            reviewed_digest=reviewed_digest,
            reviewed_version=reviewed_version,
            verification_acknowledgement=verification_acknowledgement,
            verification_run_ids=verification_run_ids,
        )

        return configuration_application_receipt
