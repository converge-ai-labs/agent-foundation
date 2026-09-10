from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ReasoningMessage")


@_attrs_define(repr=False)
class ReasoningMessage:
    """A reasoning message containing the agent's internal reasoning process.

    Attributes:
        content (str):
        id (str):
        encrypted_value (None | str | Unset):
        role (Literal['reasoning'] | Unset):
    """

    content: str
    id: str
    encrypted_value: str | Unset | None = UNSET
    role: Literal["reasoning"] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        content = self.content

        id = self.id

        encrypted_value: str | Unset | None
        if isinstance(self.encrypted_value, Unset):
            encrypted_value = UNSET
        else:
            encrypted_value = self.encrypted_value

        role = self.role

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "content": content,
                "id": id,
            }
        )
        if encrypted_value is not UNSET:
            field_dict["encryptedValue"] = encrypted_value
        if role is not UNSET:
            field_dict["role"] = role

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        content = d.pop("content")

        id = d.pop("id")

        def _parse_encrypted_value(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        encrypted_value = _parse_encrypted_value(d.pop("encryptedValue", UNSET))

        role = cast(Literal["reasoning"] | Unset, d.pop("role", UNSET))
        if role != "reasoning" and not isinstance(role, Unset):
            raise ValueError(f"role must match const 'reasoning', got '{role}'")

        reasoning_message = cls(
            content=content,
            id=id,
            encrypted_value=encrypted_value,
            role=role,
        )

        reasoning_message.additional_properties = d
        return reasoning_message

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
