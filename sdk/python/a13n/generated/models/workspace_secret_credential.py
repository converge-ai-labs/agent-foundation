from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="WorkspaceSecretCredential")


@_attrs_define(repr=False)
class WorkspaceSecretCredential:
    """
    Attributes:
        secret_id (str):
        source (Literal['workspace_secret'] | Unset):
    """

    secret_id: str
    source: Literal["workspace_secret"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        secret_id = self.secret_id

        source = self.source

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "secret_id": secret_id,
            }
        )
        if source is not UNSET:
            field_dict["source"] = source

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        secret_id = d.pop("secret_id")

        source = cast(Literal["workspace_secret"] | Unset, d.pop("source", UNSET))
        if source != "workspace_secret" and not isinstance(source, Unset):
            raise ValueError(f"source must match const 'workspace_secret', got '{source}'")

        workspace_secret_credential = cls(
            secret_id=secret_id,
            source=source,
        )

        return workspace_secret_credential
