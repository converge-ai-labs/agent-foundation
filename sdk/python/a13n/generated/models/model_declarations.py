from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.model_capability import ModelCapability
from ..models.model_declarations_thinking_efforts_item import ModelDeclarationsThinkingEffortsItem
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_pricing import ModelPricing


T = TypeVar("T", bound="ModelDeclarations")


@_attrs_define(repr=False)
class ModelDeclarations:
    """Harness-facing facts and authoring choices declared for one saved Model.

    Attributes:
        capabilities (list[ModelCapability] | Unset):
        context_window_tokens (int | None | Unset):
        max_output_tokens (int | None | Unset):
        pricing (ModelPricing | None | Unset):
        structured_output (bool | None | Unset):
        supports_tools (bool | None | Unset):
        thinking_efforts (list[ModelDeclarationsThinkingEffortsItem] | Unset):
    """

    capabilities: list[ModelCapability] | Unset = UNSET
    context_window_tokens: int | Unset | None = UNSET
    max_output_tokens: int | Unset | None = UNSET
    pricing: ModelPricing | Unset | None = UNSET
    structured_output: bool | Unset | None = UNSET
    supports_tools: bool | Unset | None = UNSET
    thinking_efforts: list[ModelDeclarationsThinkingEffortsItem] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.model_pricing import ModelPricing

        capabilities: list[str] | Unset = UNSET
        if not isinstance(self.capabilities, Unset):
            capabilities = []
            for capabilities_item_data in self.capabilities:
                capabilities_item = capabilities_item_data.value
                capabilities.append(capabilities_item)

        context_window_tokens: int | Unset | None
        if isinstance(self.context_window_tokens, Unset):
            context_window_tokens = UNSET
        else:
            context_window_tokens = self.context_window_tokens

        max_output_tokens: int | Unset | None
        if isinstance(self.max_output_tokens, Unset):
            max_output_tokens = UNSET
        else:
            max_output_tokens = self.max_output_tokens

        pricing: dict[str, Any] | Unset | None
        if isinstance(self.pricing, Unset):
            pricing = UNSET
        elif isinstance(self.pricing, ModelPricing):
            pricing = self.pricing.to_dict()
        else:
            pricing = self.pricing

        structured_output: bool | Unset | None
        if isinstance(self.structured_output, Unset):
            structured_output = UNSET
        else:
            structured_output = self.structured_output

        supports_tools: bool | Unset | None
        if isinstance(self.supports_tools, Unset):
            supports_tools = UNSET
        else:
            supports_tools = self.supports_tools

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
        if context_window_tokens is not UNSET:
            field_dict["context_window_tokens"] = context_window_tokens
        if max_output_tokens is not UNSET:
            field_dict["max_output_tokens"] = max_output_tokens
        if pricing is not UNSET:
            field_dict["pricing"] = pricing
        if structured_output is not UNSET:
            field_dict["structured_output"] = structured_output
        if supports_tools is not UNSET:
            field_dict["supports_tools"] = supports_tools
        if thinking_efforts is not UNSET:
            field_dict["thinking_efforts"] = thinking_efforts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_pricing import ModelPricing

        d = dict(src_dict)
        _capabilities = d.pop("capabilities", UNSET)
        capabilities: list[ModelCapability] | Unset = UNSET
        if _capabilities is not UNSET:
            capabilities = []
            for capabilities_item_data in _capabilities:
                capabilities_item = ModelCapability(capabilities_item_data)

                capabilities.append(capabilities_item)

        def _parse_context_window_tokens(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        context_window_tokens = _parse_context_window_tokens(d.pop("context_window_tokens", UNSET))

        def _parse_max_output_tokens(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        max_output_tokens = _parse_max_output_tokens(d.pop("max_output_tokens", UNSET))

        def _parse_pricing(data: object) -> ModelPricing | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                pricing_type_0 = ModelPricing.from_dict(data)

                return pricing_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ModelPricing | Unset | None, data)

        pricing = _parse_pricing(d.pop("pricing", UNSET))

        def _parse_structured_output(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        structured_output = _parse_structured_output(d.pop("structured_output", UNSET))

        def _parse_supports_tools(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        supports_tools = _parse_supports_tools(d.pop("supports_tools", UNSET))

        _thinking_efforts = d.pop("thinking_efforts", UNSET)
        thinking_efforts: list[ModelDeclarationsThinkingEffortsItem] | Unset = UNSET
        if _thinking_efforts is not UNSET:
            thinking_efforts = []
            for thinking_efforts_item_data in _thinking_efforts:
                thinking_efforts_item = ModelDeclarationsThinkingEffortsItem(thinking_efforts_item_data)

                thinking_efforts.append(thinking_efforts_item)

        model_declarations = cls(
            capabilities=capabilities,
            context_window_tokens=context_window_tokens,
            max_output_tokens=max_output_tokens,
            pricing=pricing,
            structured_output=structured_output,
            supports_tools=supports_tools,
            thinking_efforts=thinking_efforts,
        )

        return model_declarations
