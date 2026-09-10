from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.audio_input_content import AudioInputContent
    from ..models.binary_input_content import BinaryInputContent
    from ..models.document_input_content import DocumentInputContent
    from ..models.image_input_content import ImageInputContent
    from ..models.text_input_content import TextInputContent
    from ..models.video_input_content import VideoInputContent


T = TypeVar("T", bound="UserMessage")


@_attrs_define(repr=False)
class UserMessage:
    """A user message supporting text or multimodal content.

    Attributes:
        content (list[AudioInputContent | BinaryInputContent | DocumentInputContent | ImageInputContent |
            TextInputContent | VideoInputContent] | str):
        id (str):
        encrypted_value (None | str | Unset):
        name (None | str | Unset):
        role (Literal['user'] | Unset):
    """

    content: (
        list[
            AudioInputContent
            | BinaryInputContent
            | DocumentInputContent
            | ImageInputContent
            | TextInputContent
            | VideoInputContent
        ]
        | str
    )
    id: str
    encrypted_value: str | Unset | None = UNSET
    name: str | Unset | None = UNSET
    role: Literal["user"] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.audio_input_content import AudioInputContent
        from ..models.document_input_content import DocumentInputContent
        from ..models.image_input_content import ImageInputContent
        from ..models.text_input_content import TextInputContent
        from ..models.video_input_content import VideoInputContent

        content: list[dict[str, Any]] | str
        if isinstance(self.content, list):
            content = []
            for content_type_1_item_data in self.content:
                content_type_1_item: dict[str, Any]
                if isinstance(content_type_1_item_data, TextInputContent):
                    content_type_1_item = content_type_1_item_data.to_dict()
                elif isinstance(content_type_1_item_data, ImageInputContent):
                    content_type_1_item = content_type_1_item_data.to_dict()
                elif isinstance(content_type_1_item_data, AudioInputContent):
                    content_type_1_item = content_type_1_item_data.to_dict()
                elif isinstance(content_type_1_item_data, VideoInputContent):
                    content_type_1_item = content_type_1_item_data.to_dict()
                elif isinstance(content_type_1_item_data, DocumentInputContent):
                    content_type_1_item = content_type_1_item_data.to_dict()
                else:
                    content_type_1_item = content_type_1_item_data.to_dict()

                content.append(content_type_1_item)

        else:
            content = self.content

        id = self.id

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
        if name is not UNSET:
            field_dict["name"] = name
        if role is not UNSET:
            field_dict["role"] = role

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.audio_input_content import AudioInputContent
        from ..models.binary_input_content import BinaryInputContent
        from ..models.document_input_content import DocumentInputContent
        from ..models.image_input_content import ImageInputContent
        from ..models.text_input_content import TextInputContent
        from ..models.video_input_content import VideoInputContent

        d = dict(src_dict)

        def _parse_content(
            data: object,
        ) -> (
            list[
                AudioInputContent
                | BinaryInputContent
                | DocumentInputContent
                | ImageInputContent
                | TextInputContent
                | VideoInputContent
            ]
            | str
        ):
            try:
                if not isinstance(data, list):
                    raise TypeError()
                content_type_1 = []
                _content_type_1 = data
                for content_type_1_item_data in _content_type_1:

                    def _parse_content_type_1_item(
                        data: object,
                    ) -> (
                        AudioInputContent
                        | BinaryInputContent
                        | DocumentInputContent
                        | ImageInputContent
                        | TextInputContent
                        | VideoInputContent
                    ):
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            content_type_1_item_type_0 = TextInputContent.from_dict(data)

                            return content_type_1_item_type_0
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            content_type_1_item_type_1 = ImageInputContent.from_dict(data)

                            return content_type_1_item_type_1
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            content_type_1_item_type_2 = AudioInputContent.from_dict(data)

                            return content_type_1_item_type_2
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            content_type_1_item_type_3 = VideoInputContent.from_dict(data)

                            return content_type_1_item_type_3
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            content_type_1_item_type_4 = DocumentInputContent.from_dict(data)

                            return content_type_1_item_type_4
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        if not isinstance(data, dict):
                            raise TypeError()
                        content_type_1_item_type_5 = BinaryInputContent.from_dict(data)

                        return content_type_1_item_type_5

                    content_type_1_item = _parse_content_type_1_item(content_type_1_item_data)

                    content_type_1.append(content_type_1_item)

                return content_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(
                list[
                    AudioInputContent
                    | BinaryInputContent
                    | DocumentInputContent
                    | ImageInputContent
                    | TextInputContent
                    | VideoInputContent
                ]
                | str,
                data,
            )

        content = _parse_content(d.pop("content"))

        id = d.pop("id")

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

        role = cast(Literal["user"] | Unset, d.pop("role", UNSET))
        if role != "user" and not isinstance(role, Unset):
            raise ValueError(f"role must match const 'user', got '{role}'")

        user_message = cls(
            content=content,
            id=id,
            encrypted_value=encrypted_value,
            name=name,
            role=role,
        )

        user_message.additional_properties = d
        return user_message

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
