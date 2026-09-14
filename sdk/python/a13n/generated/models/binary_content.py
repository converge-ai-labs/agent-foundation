from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..models.binary_content_delivery import BinaryContentDelivery
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.asset_binary_source import AssetBinarySource
    from ..models.path_binary_source import PathBinarySource
    from ..models.url_binary_source import UrlBinarySource


T = TypeVar("T", bound="BinaryContent")


@_attrs_define(repr=False)
class BinaryContent:
    """
    Attributes:
        source (AssetBinarySource | PathBinarySource | UrlBinarySource):
        delivery (BinaryContentDelivery | Unset):
        filename (None | str | Unset):
        media_type (None | str | Unset):
        type_ (Literal['binary'] | Unset):
    """

    source: AssetBinarySource | PathBinarySource | UrlBinarySource
    delivery: BinaryContentDelivery | Unset = UNSET
    filename: str | Unset | None = UNSET
    media_type: str | Unset | None = UNSET
    type_: Literal["binary"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.path_binary_source import PathBinarySource
        from ..models.url_binary_source import UrlBinarySource

        source: dict[str, Any]
        if isinstance(self.source, UrlBinarySource):
            source = self.source.to_dict()
        elif isinstance(self.source, PathBinarySource):
            source = self.source.to_dict()
        else:
            source = self.source.to_dict()

        delivery: str | Unset = UNSET
        if not isinstance(self.delivery, Unset):
            delivery = self.delivery.value

        filename: str | Unset | None
        if isinstance(self.filename, Unset):
            filename = UNSET
        else:
            filename = self.filename

        media_type: str | Unset | None
        if isinstance(self.media_type, Unset):
            media_type = UNSET
        else:
            media_type = self.media_type

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "source": source,
            }
        )
        if delivery is not UNSET:
            field_dict["delivery"] = delivery
        if filename is not UNSET:
            field_dict["filename"] = filename
        if media_type is not UNSET:
            field_dict["media_type"] = media_type
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.asset_binary_source import AssetBinarySource
        from ..models.path_binary_source import PathBinarySource
        from ..models.url_binary_source import UrlBinarySource

        d = dict(src_dict)

        def _parse_source(data: object) -> AssetBinarySource | PathBinarySource | UrlBinarySource:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_type_0 = UrlBinarySource.from_dict(data)

                return source_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_type_1 = PathBinarySource.from_dict(data)

                return source_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            source_type_2 = AssetBinarySource.from_dict(data)

            return source_type_2

        source = _parse_source(d.pop("source"))

        _delivery = d.pop("delivery", UNSET)
        delivery: BinaryContentDelivery | Unset
        if isinstance(_delivery, Unset):
            delivery = UNSET
        else:
            delivery = BinaryContentDelivery(_delivery)

        def _parse_filename(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        filename = _parse_filename(d.pop("filename", UNSET))

        def _parse_media_type(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        media_type = _parse_media_type(d.pop("media_type", UNSET))

        type_ = cast(Literal["binary"] | Unset, d.pop("type", UNSET))
        if type_ != "binary" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'binary', got '{type_}'")

        binary_content = cls(
            source=source,
            delivery=delivery,
            filename=filename,
            media_type=media_type,
            type_=type_,
        )

        return binary_content
