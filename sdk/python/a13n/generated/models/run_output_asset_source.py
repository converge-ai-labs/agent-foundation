from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RunOutputAssetSource")


@_attrs_define(repr=False)
class RunOutputAssetSource:
    """
    Attributes:
        kind (Literal['run_output'] | Unset):
        run_id (None | str | Unset):
    """

    kind: Literal["run_output"] | Unset = UNSET
    run_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind

        run_id: str | Unset | None
        if isinstance(self.run_id, Unset):
            run_id = UNSET
        else:
            run_id = self.run_id

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if kind is not UNSET:
            field_dict["kind"] = kind
        if run_id is not UNSET:
            field_dict["run_id"] = run_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = cast(Literal["run_output"] | Unset, d.pop("kind", UNSET))
        if kind != "run_output" and not isinstance(kind, Unset):
            raise ValueError(f"kind must match const 'run_output', got '{kind}'")

        def _parse_run_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        run_id = _parse_run_id(d.pop("run_id", UNSET))

        run_output_asset_source = cls(
            kind=kind,
            run_id=run_id,
        )

        return run_output_asset_source
