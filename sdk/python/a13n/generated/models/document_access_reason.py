from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.document_access_reason_kind import DocumentAccessReasonKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="DocumentAccessReason")


@_attrs_define(repr=False)
class DocumentAccessReason:
    """
    Attributes:
        kind (DocumentAccessReasonKind):
        policy_id (None | str | Unset):
        policy_name (None | str | Unset):
    """

    kind: DocumentAccessReasonKind
    policy_id: str | Unset | None = UNSET
    policy_name: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        policy_id: str | Unset | None
        if isinstance(self.policy_id, Unset):
            policy_id = UNSET
        else:
            policy_id = self.policy_id

        policy_name: str | Unset | None
        if isinstance(self.policy_name, Unset):
            policy_name = UNSET
        else:
            policy_name = self.policy_name

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "kind": kind,
            }
        )
        if policy_id is not UNSET:
            field_dict["policy_id"] = policy_id
        if policy_name is not UNSET:
            field_dict["policy_name"] = policy_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = DocumentAccessReasonKind(d.pop("kind"))

        def _parse_policy_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        policy_id = _parse_policy_id(d.pop("policy_id", UNSET))

        def _parse_policy_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        policy_name = _parse_policy_name(d.pop("policy_name", UNSET))

        document_access_reason = cls(
            kind=kind,
            policy_id=policy_id,
            policy_name=policy_name,
        )

        return document_access_reason
