from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.tool_setup_destination_kind import ToolSetupDestinationKind
from ..models.tool_setup_destination_operation_type_0 import ToolSetupDestinationOperationType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="ToolSetupDestination")


@_attrs_define(repr=False)
class ToolSetupDestination:
    """
    Attributes:
        kind (ToolSetupDestinationKind):
        operation (None | ToolSetupDestinationOperationType0 | Unset):
    """

    kind: ToolSetupDestinationKind
    operation: ToolSetupDestinationOperationType0 | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        operation: str | Unset | None
        if isinstance(self.operation, Unset):
            operation = UNSET
        elif isinstance(self.operation, ToolSetupDestinationOperationType0):
            operation = self.operation.value
        else:
            operation = self.operation

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "kind": kind,
            }
        )
        if operation is not UNSET:
            field_dict["operation"] = operation

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = ToolSetupDestinationKind(d.pop("kind"))

        def _parse_operation(data: object) -> ToolSetupDestinationOperationType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                operation_type_0 = ToolSetupDestinationOperationType0(data)

                return operation_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ToolSetupDestinationOperationType0 | Unset | None, data)

        operation = _parse_operation(d.pop("operation", UNSET))

        tool_setup_destination = cls(
            kind=kind,
            operation=operation,
        )

        return tool_setup_destination
