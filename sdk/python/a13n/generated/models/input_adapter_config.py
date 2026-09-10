from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.input_adapter_config_config import InputAdapterConfigConfig


T = TypeVar("T", bound="InputAdapterConfig")


@_attrs_define(repr=False)
class InputAdapterConfig:
    """
    Attributes:
        adapter_key (str):
        config (InputAdapterConfigConfig | Unset):
    """

    adapter_key: str
    config: InputAdapterConfigConfig | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        adapter_key = self.adapter_key

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "adapter_key": adapter_key,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.input_adapter_config_config import InputAdapterConfigConfig

        d = dict(src_dict)
        adapter_key = d.pop("adapter_key")

        _config = d.pop("config", UNSET)
        config: InputAdapterConfigConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = InputAdapterConfigConfig.from_dict(_config)

        input_adapter_config = cls(
            adapter_key=adapter_key,
            config=config,
        )

        return input_adapter_config
