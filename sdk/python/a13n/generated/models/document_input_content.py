from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.input_content_data_source import InputContentDataSource
    from ..models.input_content_url_source import InputContentUrlSource


T = TypeVar("T", bound="DocumentInputContent")


@_attrs_define(repr=False)
class DocumentInputContent:
    """A document input content fragment.

    Attributes:
        source (InputContentDataSource | InputContentUrlSource):
        metadata (Any | None | Unset):
        type_ (Literal['document'] | Unset):
    """

    source: InputContentDataSource | InputContentUrlSource
    metadata: Any | Unset | None = UNSET
    type_: Literal["document"] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.input_content_data_source import InputContentDataSource

        source: dict[str, Any]
        if isinstance(self.source, InputContentDataSource):
            source = self.source.to_dict()
        else:
            source = self.source.to_dict()

        metadata: Any | Unset | None
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        else:
            metadata = self.metadata

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "source": source,
            }
        )
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.input_content_data_source import InputContentDataSource
        from ..models.input_content_url_source import InputContentUrlSource

        d = dict(src_dict)

        def _parse_source(data: object) -> InputContentDataSource | InputContentUrlSource:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_type_0 = InputContentDataSource.from_dict(data)

                return source_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            source_type_1 = InputContentUrlSource.from_dict(data)

            return source_type_1

        source = _parse_source(d.pop("source"))

        def _parse_metadata(data: object) -> Any | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | Unset | None, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        type_ = cast(Literal["document"] | Unset, d.pop("type", UNSET))
        if type_ != "document" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'document', got '{type_}'")

        document_input_content = cls(
            source=source,
            metadata=metadata,
            type_=type_,
        )

        document_input_content.additional_properties = d
        return document_input_content

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
