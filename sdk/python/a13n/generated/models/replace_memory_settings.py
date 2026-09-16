from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.memory_settings import MemorySettings


T = TypeVar("T", bound="ReplaceMemorySettings")


@_attrs_define(repr=False)
class ReplaceMemorySettings:
    """
    Attributes:
        expected_version (int):
        memory (MemorySettings | None):
    """

    expected_version: int
    memory: MemorySettings | None

    def to_dict(self) -> dict[str, Any]:
        from ..models.memory_settings import MemorySettings

        expected_version = self.expected_version

        memory: dict[str, Any] | None
        if isinstance(self.memory, MemorySettings):
            memory = self.memory.to_dict()
        else:
            memory = self.memory

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "memory": memory,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.memory_settings import MemorySettings

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        def _parse_memory(data: object) -> MemorySettings | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                memory_type_0 = MemorySettings.from_dict(data)

                return memory_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MemorySettings | None, data)

        memory = _parse_memory(d.pop("memory"))

        replace_memory_settings = cls(
            expected_version=expected_version,
            memory=memory,
        )

        return replace_memory_settings
