from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.function_call import FunctionCall


T = TypeVar("T", bound="ToolCall")


@_attrs_define(repr=False)
class ToolCall:
    """A tool call, modelled after OpenAI tool calls.

    Attributes:
        function (FunctionCall): Name and arguments of a function call.
        id (str):
        encrypted_value (None | str | Unset):
        type_ (Literal['function'] | Unset):
    """

    function: FunctionCall
    id: str
    encrypted_value: str | Unset | None = UNSET
    type_: Literal["function"] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        function = self.function.to_dict()

        id = self.id

        encrypted_value: str | Unset | None
        if isinstance(self.encrypted_value, Unset):
            encrypted_value = UNSET
        else:
            encrypted_value = self.encrypted_value

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "function": function,
                "id": id,
            }
        )
        if encrypted_value is not UNSET:
            field_dict["encryptedValue"] = encrypted_value
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.function_call import FunctionCall

        d = dict(src_dict)
        function = FunctionCall.from_dict(d.pop("function"))

        id = d.pop("id")

        def _parse_encrypted_value(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        encrypted_value = _parse_encrypted_value(d.pop("encryptedValue", UNSET))

        type_ = cast(Literal["function"] | Unset, d.pop("type", UNSET))
        if type_ != "function" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'function', got '{type_}'")

        tool_call = cls(
            function=function,
            id=id,
            encrypted_value=encrypted_value,
            type_=type_,
        )

        tool_call.additional_properties = d
        return tool_call

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
