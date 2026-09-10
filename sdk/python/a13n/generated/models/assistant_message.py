from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.tool_call import ToolCall


T = TypeVar("T", bound="AssistantMessage")


@_attrs_define(repr=False)
class AssistantMessage:
    """An assistant message.

    Attributes:
        id (str):
        content (None | str | Unset):
        encrypted_value (None | str | Unset):
        name (None | str | Unset):
        role (Literal['assistant'] | Unset):
        tool_calls (list[ToolCall] | None | Unset):
    """

    id: str
    content: str | Unset | None = UNSET
    encrypted_value: str | Unset | None = UNSET
    name: str | Unset | None = UNSET
    role: Literal["assistant"] | Unset = UNSET
    tool_calls: list[ToolCall] | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        content: str | Unset | None
        if isinstance(self.content, Unset):
            content = UNSET
        else:
            content = self.content

        encrypted_value: str | Unset | None
        if isinstance(self.encrypted_value, Unset):
            encrypted_value = UNSET
        else:
            encrypted_value = self.encrypted_value

        name: str | Unset | None
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        role = self.role

        tool_calls: list[dict[str, Any]] | Unset | None
        if isinstance(self.tool_calls, Unset):
            tool_calls = UNSET
        elif isinstance(self.tool_calls, list):
            tool_calls = []
            for tool_calls_type_0_item_data in self.tool_calls:
                tool_calls_type_0_item = tool_calls_type_0_item_data.to_dict()
                tool_calls.append(tool_calls_type_0_item)

        else:
            tool_calls = self.tool_calls

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
            }
        )
        if content is not UNSET:
            field_dict["content"] = content
        if encrypted_value is not UNSET:
            field_dict["encryptedValue"] = encrypted_value
        if name is not UNSET:
            field_dict["name"] = name
        if role is not UNSET:
            field_dict["role"] = role
        if tool_calls is not UNSET:
            field_dict["toolCalls"] = tool_calls

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.tool_call import ToolCall

        d = dict(src_dict)
        id = d.pop("id")

        def _parse_content(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        content = _parse_content(d.pop("content", UNSET))

        def _parse_encrypted_value(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        encrypted_value = _parse_encrypted_value(d.pop("encryptedValue", UNSET))

        def _parse_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        name = _parse_name(d.pop("name", UNSET))

        role = cast(Literal["assistant"] | Unset, d.pop("role", UNSET))
        if role != "assistant" and not isinstance(role, Unset):
            raise ValueError(f"role must match const 'assistant', got '{role}'")

        def _parse_tool_calls(data: object) -> list[ToolCall] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tool_calls_type_0 = []
                _tool_calls_type_0 = data
                for tool_calls_type_0_item_data in _tool_calls_type_0:
                    tool_calls_type_0_item = ToolCall.from_dict(tool_calls_type_0_item_data)

                    tool_calls_type_0.append(tool_calls_type_0_item)

                return tool_calls_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ToolCall] | Unset | None, data)

        tool_calls = _parse_tool_calls(d.pop("toolCalls", UNSET))

        assistant_message = cls(
            id=id,
            content=content,
            encrypted_value=encrypted_value,
            name=name,
            role=role,
            tool_calls=tool_calls,
        )

        assistant_message.additional_properties = d
        return assistant_message

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
