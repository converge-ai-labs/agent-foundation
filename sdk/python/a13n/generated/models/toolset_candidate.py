from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_reviewer import AgentReviewer
    from ..models.toolset_candidate_toolsets import ToolsetCandidateToolsets


T = TypeVar("T", bound="ToolsetCandidate")


@_attrs_define(repr=False)
class ToolsetCandidate:
    """
    Attributes:
        reviewer (AgentReviewer | None | Unset):
        toolsets (ToolsetCandidateToolsets | Unset):
    """

    reviewer: AgentReviewer | Unset | None = UNSET
    toolsets: ToolsetCandidateToolsets | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_reviewer import AgentReviewer

        reviewer: dict[str, Any] | Unset | None
        if isinstance(self.reviewer, Unset):
            reviewer = UNSET
        elif isinstance(self.reviewer, AgentReviewer):
            reviewer = self.reviewer.to_dict()
        else:
            reviewer = self.reviewer

        toolsets: dict[str, Any] | Unset = UNSET
        if not isinstance(self.toolsets, Unset):
            toolsets = self.toolsets.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if reviewer is not UNSET:
            field_dict["reviewer"] = reviewer
        if toolsets is not UNSET:
            field_dict["toolsets"] = toolsets

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_reviewer import AgentReviewer
        from ..models.toolset_candidate_toolsets import ToolsetCandidateToolsets

        d = dict(src_dict)

        def _parse_reviewer(data: object) -> AgentReviewer | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                reviewer_type_0 = AgentReviewer.from_dict(data)

                return reviewer_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentReviewer | Unset | None, data)

        reviewer = _parse_reviewer(d.pop("reviewer", UNSET))

        _toolsets = d.pop("toolsets", UNSET)
        toolsets: ToolsetCandidateToolsets | Unset
        if isinstance(_toolsets, Unset):
            toolsets = UNSET
        else:
            toolsets = ToolsetCandidateToolsets.from_dict(_toolsets)

        toolset_candidate = cls(
            reviewer=reviewer,
            toolsets=toolsets,
        )

        return toolset_candidate
