from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.instrumentation_scope_attributes_type_0 import InstrumentationScopeAttributesType0


T = TypeVar("T", bound="InstrumentationScope")


@_attrs_define(repr=False)
class InstrumentationScope:
    """
    Attributes:
        attributes (InstrumentationScopeAttributesType0 | None):
        name (None | str):
        version (None | str):
    """

    attributes: InstrumentationScopeAttributesType0 | None
    name: str | None
    version: str | None

    def to_dict(self) -> dict[str, Any]:
        from ..models.instrumentation_scope_attributes_type_0 import (
            InstrumentationScopeAttributesType0,
        )

        attributes: dict[str, Any] | None
        if isinstance(self.attributes, InstrumentationScopeAttributesType0):
            attributes = self.attributes.to_dict()
        else:
            attributes = self.attributes

        name: str | None
        name = self.name

        version: str | None
        version = self.version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "attributes": attributes,
                "name": name,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.instrumentation_scope_attributes_type_0 import (
            InstrumentationScopeAttributesType0,
        )

        d = dict(src_dict)

        def _parse_attributes(data: object) -> InstrumentationScopeAttributesType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                attributes_type_0 = InstrumentationScopeAttributesType0.from_dict(data)

                return attributes_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InstrumentationScopeAttributesType0 | None, data)

        attributes = _parse_attributes(d.pop("attributes"))

        def _parse_name(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        name = _parse_name(d.pop("name"))

        def _parse_version(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        version = _parse_version(d.pop("version"))

        instrumentation_scope = cls(
            attributes=attributes,
            name=name,
            version=version,
        )

        return instrumentation_scope
