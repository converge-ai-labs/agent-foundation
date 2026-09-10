from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="WaitingResolutionDefaults")


@_attrs_define(repr=False)
class WaitingResolutionDefaults:
    """
    Attributes:
        sealed_state_digest_sha256 (str):
        mode (Literal['defaults'] | Unset):
    """

    sealed_state_digest_sha256: str
    mode: Literal["defaults"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        sealed_state_digest_sha256 = self.sealed_state_digest_sha256

        mode = self.mode

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "sealed_state_digest_sha256": sealed_state_digest_sha256,
            }
        )
        if mode is not UNSET:
            field_dict["mode"] = mode

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        sealed_state_digest_sha256 = d.pop("sealed_state_digest_sha256")

        mode = cast(Literal["defaults"] | Unset, d.pop("mode", UNSET))
        if mode != "defaults" and not isinstance(mode, Unset):
            raise ValueError(f"mode must match const 'defaults', got '{mode}'")

        waiting_resolution_defaults = cls(
            sealed_state_digest_sha256=sealed_state_digest_sha256,
            mode=mode,
        )

        return waiting_resolution_defaults
