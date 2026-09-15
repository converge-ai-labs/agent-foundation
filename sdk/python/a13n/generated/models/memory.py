from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="Memory")


@_attrs_define(repr=False)
class Memory:
    """
    Attributes:
        id (str):
        memory (str):
        score (float | None | Unset):
    """

    id: str
    memory: str
    score: float | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        memory = self.memory

        score: float | Unset | None
        if isinstance(self.score, Unset):
            score = UNSET
        else:
            score = self.score

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "id": id,
                "memory": memory,
            }
        )
        if score is not UNSET:
            field_dict["score"] = score

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        memory = d.pop("memory")

        def _parse_score(data: object) -> float | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | Unset | None, data)

        score = _parse_score(d.pop("score", UNSET))

        memory = cls(
            id=id,
            memory=memory,
            score=score,
        )

        return memory
