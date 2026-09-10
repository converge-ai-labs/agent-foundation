from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.skill_publication_receipt_outcome import SkillPublicationReceiptOutcome

if TYPE_CHECKING:
    from ..models.skill import Skill
    from ..models.skill_revision import SkillRevision


T = TypeVar("T", bound="SkillPublicationReceipt")


@_attrs_define(repr=False)
class SkillPublicationReceipt:
    """
    Attributes:
        outcome (SkillPublicationReceiptOutcome):
        revision (SkillRevision):
        skill (Skill):
    """

    outcome: SkillPublicationReceiptOutcome
    revision: SkillRevision
    skill: Skill

    def to_dict(self) -> dict[str, Any]:
        outcome = self.outcome.value

        revision = self.revision.to_dict()

        skill = self.skill.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "outcome": outcome,
                "revision": revision,
                "skill": skill,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.skill import Skill
        from ..models.skill_revision import SkillRevision

        d = dict(src_dict)
        outcome = SkillPublicationReceiptOutcome(d.pop("outcome"))

        revision = SkillRevision.from_dict(d.pop("revision"))

        skill = Skill.from_dict(d.pop("skill"))

        skill_publication_receipt = cls(
            outcome=outcome,
            revision=revision,
            skill=skill,
        )

        return skill_publication_receipt
