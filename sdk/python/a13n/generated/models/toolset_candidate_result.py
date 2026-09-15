from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.toolset_candidate_error import ToolsetCandidateError
    from ..models.toolset_candidate_result_toolsets import ToolsetCandidateResultToolsets


T = TypeVar("T", bound="ToolsetCandidateResult")


@_attrs_define(repr=False)
class ToolsetCandidateResult:
    """
    Attributes:
        errors (list[ToolsetCandidateError]):
        toolsets (ToolsetCandidateResultToolsets):
        valid (bool):
    """

    errors: list[ToolsetCandidateError]
    toolsets: ToolsetCandidateResultToolsets
    valid: bool

    def to_dict(self) -> dict[str, Any]:
        errors = []
        for errors_item_data in self.errors:
            errors_item = errors_item_data.to_dict()
            errors.append(errors_item)

        toolsets = self.toolsets.to_dict()

        valid = self.valid

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "errors": errors,
                "toolsets": toolsets,
                "valid": valid,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.toolset_candidate_error import ToolsetCandidateError
        from ..models.toolset_candidate_result_toolsets import ToolsetCandidateResultToolsets

        d = dict(src_dict)
        errors = []
        _errors = d.pop("errors")
        for errors_item_data in _errors:
            errors_item = ToolsetCandidateError.from_dict(errors_item_data)

            errors.append(errors_item)

        toolsets = ToolsetCandidateResultToolsets.from_dict(d.pop("toolsets"))

        valid = d.pop("valid")

        toolset_candidate_result = cls(
            errors=errors,
            toolsets=toolsets,
            valid=valid,
        )

        return toolset_candidate_result
