from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.model_capability import ModelCapability
from ..models.model_declarations_thinking_efforts_item import ModelDeclarationsThinkingEffortsItem
from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelDeclarations")


@_attrs_define(repr=False)
class ModelDeclarations:
    """Harness-facing facts and authoring choices declared for one saved Model.

    Attributes:
        capabilities (list[ModelCapability] | Unset):
        context_window (int | None | Unset):
        thinking_efforts (list[ModelDeclarationsThinkingEffortsItem] | Unset):
    """

    capabilities: list[ModelCapability] | Unset = UNSET
    context_window: int | Unset | None = UNSET
    thinking_efforts: list[ModelDeclarationsThinkingEffortsItem] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        capabilities: list[str] | Unset = UNSET
        if not isinstance(self.capabilities, Unset):
            capabilities = []
            for capabilities_item_data in self.capabilities:
                capabilities_item = capabilities_item_data.value
                capabilities.append(capabilities_item)

        context_window: int | Unset | None
        if isinstance(self.context_window, Unset):
            context_window = UNSET
        else:
            context_window = self.context_window

        thinking_efforts: list[str] | Unset = UNSET
        if not isinstance(self.thinking_efforts, Unset):
            thinking_efforts = []
            for thinking_efforts_item_data in self.thinking_efforts:
                thinking_efforts_item = thinking_efforts_item_data.value
                thinking_efforts.append(thinking_efforts_item)

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if capabilities is not UNSET:
            field_dict["capabilities"] = capabilities
        if context_window is not UNSET:
            field_dict["context_window"] = context_window
        if thinking_efforts is not UNSET:
            field_dict["thinking_efforts"] = thinking_efforts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        _capabilities = d.pop("capabilities", UNSET)
        capabilities: list[ModelCapability] | Unset = UNSET
        if _capabilities is not UNSET:
            capabilities = []
            for capabilities_item_data in _capabilities:
                capabilities_item = ModelCapability(capabilities_item_data)

                capabilities.append(capabilities_item)

        def _parse_context_window(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        context_window = _parse_context_window(d.pop("context_window", UNSET))

        _thinking_efforts = d.pop("thinking_efforts", UNSET)
        thinking_efforts: list[ModelDeclarationsThinkingEffortsItem] | Unset = UNSET
        if _thinking_efforts is not UNSET:
            thinking_efforts = []
            for thinking_efforts_item_data in _thinking_efforts:
                thinking_efforts_item = ModelDeclarationsThinkingEffortsItem(thinking_efforts_item_data)

                thinking_efforts.append(thinking_efforts_item)

        model_declarations = cls(
            capabilities=capabilities,
            context_window=context_window,
            thinking_efforts=thinking_efforts,
        )

        return model_declarations
