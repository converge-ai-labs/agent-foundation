from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..models.tool_resource_selector_operation import ToolResourceSelectorOperation

T = TypeVar("T", bound="ToolResourceSelector")


@_attrs_define(repr=False)
class ToolResourceSelector:
    """
    Attributes:
        kind (Literal['web_provider']):
        operation (ToolResourceSelectorOperation):
    """

    kind: Literal["web_provider"]
    operation: ToolResourceSelectorOperation

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind

        operation = self.operation.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "kind": kind,
                "operation": operation,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = cast(Literal["web_provider"], d.pop("kind"))
        if kind != "web_provider":
            raise ValueError(f"kind must match const 'web_provider', got '{kind}'")

        operation = ToolResourceSelectorOperation(d.pop("operation"))

        tool_resource_selector = cls(
            kind=kind,
            operation=operation,
        )

        return tool_resource_selector
