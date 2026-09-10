from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.principal_ref import PrincipalRef


T = TypeVar("T", bound="UploadedAssetSource")


@_attrs_define(repr=False)
class UploadedAssetSource:
    """
    Attributes:
        principal (PrincipalRef):
        kind (Literal['upload'] | Unset):
    """

    principal: PrincipalRef
    kind: Literal["upload"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        principal = self.principal.to_dict()

        kind = self.kind

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "principal": principal,
            }
        )
        if kind is not UNSET:
            field_dict["kind"] = kind

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.principal_ref import PrincipalRef

        d = dict(src_dict)
        principal = PrincipalRef.from_dict(d.pop("principal"))

        kind = cast(Literal["upload"] | Unset, d.pop("kind", UNSET))
        if kind != "upload" and not isinstance(kind, Unset):
            raise ValueError(f"kind must match const 'upload', got '{kind}'")

        uploaded_asset_source = cls(
            principal=principal,
            kind=kind,
        )

        return uploaded_asset_source
