from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelPricing")


@_attrs_define(repr=False)
class ModelPricing:
    """Editable USD prices per million tokens.

    Attributes:
        cache_read (float | None | Unset):
        cache_write (float | None | Unset):
        input_ (float | None | Unset):
        output (float | None | Unset):
    """

    cache_read: float | Unset | None = UNSET
    cache_write: float | Unset | None = UNSET
    input_: float | Unset | None = UNSET
    output: float | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        cache_read: float | Unset | None
        if isinstance(self.cache_read, Unset):
            cache_read = UNSET
        else:
            cache_read = self.cache_read

        cache_write: float | Unset | None
        if isinstance(self.cache_write, Unset):
            cache_write = UNSET
        else:
            cache_write = self.cache_write

        input_: float | Unset | None
        if isinstance(self.input_, Unset):
            input_ = UNSET
        else:
            input_ = self.input_

        output: float | Unset | None
        if isinstance(self.output, Unset):
            output = UNSET
        else:
            output = self.output

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if cache_read is not UNSET:
            field_dict["cache_read"] = cache_read
        if cache_write is not UNSET:
            field_dict["cache_write"] = cache_write
        if input_ is not UNSET:
            field_dict["input"] = input_
        if output is not UNSET:
            field_dict["output"] = output

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_cache_read(data: object) -> float | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | Unset | None, data)

        cache_read = _parse_cache_read(d.pop("cache_read", UNSET))

        def _parse_cache_write(data: object) -> float | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | Unset | None, data)

        cache_write = _parse_cache_write(d.pop("cache_write", UNSET))

        def _parse_input_(data: object) -> float | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | Unset | None, data)

        input_ = _parse_input_(d.pop("input", UNSET))

        def _parse_output(data: object) -> float | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | Unset | None, data)

        output = _parse_output(d.pop("output", UNSET))

        model_pricing = cls(
            cache_read=cache_read,
            cache_write=cache_write,
            input_=input_,
            output=output,
        )

        return model_pricing
