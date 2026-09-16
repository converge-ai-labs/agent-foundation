from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.verification_acknowledgement import VerificationAcknowledgement


T = TypeVar("T", bound="ApplyDraftRequest")


@_attrs_define(repr=False)
class ApplyDraftRequest:
    """
    Attributes:
        content_digest (str):
        dependency_digest (str):
        expected_version (int):
        expected_target_version (int | None | Unset):
        verification_acknowledgement (None | Unset | VerificationAcknowledgement):
        verification_run_ids (list[str] | Unset):
    """

    content_digest: str
    dependency_digest: str
    expected_version: int
    expected_target_version: int | Unset | None = UNSET
    verification_acknowledgement: Unset | VerificationAcknowledgement | None = UNSET
    verification_run_ids: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.verification_acknowledgement import VerificationAcknowledgement

        content_digest = self.content_digest

        dependency_digest = self.dependency_digest

        expected_version = self.expected_version

        expected_target_version: int | Unset | None
        if isinstance(self.expected_target_version, Unset):
            expected_target_version = UNSET
        else:
            expected_target_version = self.expected_target_version

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
                "content_digest": content_digest,
                "dependency_digest": dependency_digest,
                "expected_version": expected_version,
            }
        )
        if expected_target_version is not UNSET:
            field_dict["expected_target_version"] = expected_target_version
        if verification_acknowledgement is not UNSET:
            field_dict["verification_acknowledgement"] = verification_acknowledgement
        if verification_run_ids is not UNSET:
            field_dict["verification_run_ids"] = verification_run_ids

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.verification_acknowledgement import VerificationAcknowledgement

        d = dict(src_dict)
        content_digest = d.pop("content_digest")

        dependency_digest = d.pop("dependency_digest")

        expected_version = d.pop("expected_version")

        def _parse_expected_target_version(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        expected_target_version = _parse_expected_target_version(d.pop("expected_target_version", UNSET))

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

        apply_draft_request = cls(
            content_digest=content_digest,
            dependency_digest=dependency_digest,
            expected_version=expected_version,
            expected_target_version=expected_target_version,
            verification_acknowledgement=verification_acknowledgement,
            verification_run_ids=verification_run_ids,
        )

        return apply_draft_request
