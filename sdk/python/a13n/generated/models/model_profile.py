from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.model_profile_input_modalities_type_0_item import ModelProfileInputModalitiesType0Item
from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelProfile")


@_attrs_define(repr=False)
class ModelProfile:
    """Read-only Provider capability information returned by discovery.

    Attributes:
        input_modalities (list[ModelProfileInputModalitiesType0Item] | None | Unset):
        supports_audio_input (bool | None | Unset):
        supports_image_output (bool | None | Unset):
        supports_json_object_output (bool | None | Unset):
        supports_json_schema_output (bool | None | Unset):
        supports_thinking (bool | None | Unset):
        supports_tools (bool | None | Unset):
        thinking_always_enabled (bool | None | Unset):
    """

    input_modalities: list[ModelProfileInputModalitiesType0Item] | Unset | None = UNSET
    supports_audio_input: bool | Unset | None = UNSET
    supports_image_output: bool | Unset | None = UNSET
    supports_json_object_output: bool | Unset | None = UNSET
    supports_json_schema_output: bool | Unset | None = UNSET
    supports_thinking: bool | Unset | None = UNSET
    supports_tools: bool | Unset | None = UNSET
    thinking_always_enabled: bool | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        input_modalities: list[str] | Unset | None
        if isinstance(self.input_modalities, Unset):
            input_modalities = UNSET
        elif isinstance(self.input_modalities, list):
            input_modalities = []
            for input_modalities_type_0_item_data in self.input_modalities:
                input_modalities_type_0_item = input_modalities_type_0_item_data.value
                input_modalities.append(input_modalities_type_0_item)

        else:
            input_modalities = self.input_modalities

        supports_audio_input: bool | Unset | None
        if isinstance(self.supports_audio_input, Unset):
            supports_audio_input = UNSET
        else:
            supports_audio_input = self.supports_audio_input

        supports_image_output: bool | Unset | None
        if isinstance(self.supports_image_output, Unset):
            supports_image_output = UNSET
        else:
            supports_image_output = self.supports_image_output

        supports_json_object_output: bool | Unset | None
        if isinstance(self.supports_json_object_output, Unset):
            supports_json_object_output = UNSET
        else:
            supports_json_object_output = self.supports_json_object_output

        supports_json_schema_output: bool | Unset | None
        if isinstance(self.supports_json_schema_output, Unset):
            supports_json_schema_output = UNSET
        else:
            supports_json_schema_output = self.supports_json_schema_output

        supports_thinking: bool | Unset | None
        if isinstance(self.supports_thinking, Unset):
            supports_thinking = UNSET
        else:
            supports_thinking = self.supports_thinking

        supports_tools: bool | Unset | None
        if isinstance(self.supports_tools, Unset):
            supports_tools = UNSET
        else:
            supports_tools = self.supports_tools

        thinking_always_enabled: bool | Unset | None
        if isinstance(self.thinking_always_enabled, Unset):
            thinking_always_enabled = UNSET
        else:
            thinking_always_enabled = self.thinking_always_enabled

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if input_modalities is not UNSET:
            field_dict["input_modalities"] = input_modalities
        if supports_audio_input is not UNSET:
            field_dict["supports_audio_input"] = supports_audio_input
        if supports_image_output is not UNSET:
            field_dict["supports_image_output"] = supports_image_output
        if supports_json_object_output is not UNSET:
            field_dict["supports_json_object_output"] = supports_json_object_output
        if supports_json_schema_output is not UNSET:
            field_dict["supports_json_schema_output"] = supports_json_schema_output
        if supports_thinking is not UNSET:
            field_dict["supports_thinking"] = supports_thinking
        if supports_tools is not UNSET:
            field_dict["supports_tools"] = supports_tools
        if thinking_always_enabled is not UNSET:
            field_dict["thinking_always_enabled"] = thinking_always_enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_input_modalities(data: object) -> list[ModelProfileInputModalitiesType0Item] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                input_modalities_type_0 = []
                _input_modalities_type_0 = data
                for input_modalities_type_0_item_data in _input_modalities_type_0:
                    input_modalities_type_0_item = ModelProfileInputModalitiesType0Item(
                        input_modalities_type_0_item_data
                    )

                    input_modalities_type_0.append(input_modalities_type_0_item)

                return input_modalities_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ModelProfileInputModalitiesType0Item] | Unset | None, data)

        input_modalities = _parse_input_modalities(d.pop("input_modalities", UNSET))

        def _parse_supports_audio_input(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        supports_audio_input = _parse_supports_audio_input(d.pop("supports_audio_input", UNSET))

        def _parse_supports_image_output(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        supports_image_output = _parse_supports_image_output(d.pop("supports_image_output", UNSET))

        def _parse_supports_json_object_output(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        supports_json_object_output = _parse_supports_json_object_output(d.pop("supports_json_object_output", UNSET))

        def _parse_supports_json_schema_output(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        supports_json_schema_output = _parse_supports_json_schema_output(d.pop("supports_json_schema_output", UNSET))

        def _parse_supports_thinking(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        supports_thinking = _parse_supports_thinking(d.pop("supports_thinking", UNSET))

        def _parse_supports_tools(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        supports_tools = _parse_supports_tools(d.pop("supports_tools", UNSET))

        def _parse_thinking_always_enabled(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        thinking_always_enabled = _parse_thinking_always_enabled(d.pop("thinking_always_enabled", UNSET))

        model_profile = cls(
            input_modalities=input_modalities,
            supports_audio_input=supports_audio_input,
            supports_image_output=supports_image_output,
            supports_json_object_output=supports_json_object_output,
            supports_json_schema_output=supports_json_schema_output,
            supports_thinking=supports_thinking,
            supports_tools=supports_tools,
            thinking_always_enabled=thinking_always_enabled,
        )

        return model_profile
