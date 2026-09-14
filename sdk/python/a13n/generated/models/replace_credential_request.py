from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.replace_credential_request_credential_type_0 import ReplaceCredentialRequestCredentialType0


T = TypeVar("T", bound="ReplaceCredentialRequest")


@_attrs_define(repr=False)
class ReplaceCredentialRequest:
    """
    Attributes:
        credential (None | ReplaceCredentialRequestCredentialType0):
    """

    credential: ReplaceCredentialRequestCredentialType0 | None

    def to_dict(self) -> dict[str, Any]:
        from ..models.replace_credential_request_credential_type_0 import (
            ReplaceCredentialRequestCredentialType0,
        )

        credential: dict[str, Any] | None
        if isinstance(self.credential, ReplaceCredentialRequestCredentialType0):
            credential = self.credential.to_dict()
        else:
            credential = self.credential

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "credential": credential,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.replace_credential_request_credential_type_0 import (
            ReplaceCredentialRequestCredentialType0,
        )

        d = dict(src_dict)

        def _parse_credential(data: object) -> ReplaceCredentialRequestCredentialType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credential_type_0 = ReplaceCredentialRequestCredentialType0.from_dict(data)

                return credential_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ReplaceCredentialRequestCredentialType0 | None, data)

        credential = _parse_credential(d.pop("credential"))

        replace_credential_request = cls(
            credential=credential,
        )

        return replace_credential_request
