from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="InvokingUserSecretCredential")


@_attrs_define(repr=False)
class InvokingUserSecretCredential:
    """
    Attributes:
        secret_key (str):
        source (Literal['invoking_user_secret'] | Unset):
    """

    secret_key: str
    source: Literal["invoking_user_secret"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        secret_key = self.secret_key

        source = self.source

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "secret_key": secret_key,
            }
        )
        if source is not UNSET:
            field_dict["source"] = source

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        secret_key = d.pop("secret_key")

        source = cast(Literal["invoking_user_secret"] | Unset, d.pop("source", UNSET))
        if source != "invoking_user_secret" and not isinstance(source, Unset):
            raise ValueError(f"source must match const 'invoking_user_secret', got '{source}'")

        invoking_user_secret_credential = cls(
            secret_key=secret_key,
            source=source,
        )

        return invoking_user_secret_credential
