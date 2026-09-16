from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.memory_settings import MemorySettings


T = TypeVar("T", bound="AccountMemorySettings")


@_attrs_define(repr=False)
class AccountMemorySettings:
    """
    Attributes:
        account_id (str):
        memory (MemorySettings | None):
        version (int):
    """

    account_id: str
    memory: MemorySettings | None
    version: int

    def to_dict(self) -> dict[str, Any]:
        from ..models.memory_settings import MemorySettings

        account_id = self.account_id

        memory: dict[str, Any] | None
        if isinstance(self.memory, MemorySettings):
            memory = self.memory.to_dict()
        else:
            memory = self.memory

        version = self.version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "account_id": account_id,
                "memory": memory,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.memory_settings import MemorySettings

        d = dict(src_dict)
        account_id = d.pop("account_id")

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

        version = d.pop("version")

        account_memory_settings = cls(
            account_id=account_id,
            memory=memory,
            version=version,
        )

        return account_memory_settings
