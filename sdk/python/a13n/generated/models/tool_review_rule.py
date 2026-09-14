from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.tool_review_rule_on_flagged_type_0 import ToolReviewRuleOnFlaggedType0
from ..models.tool_risk_level import ToolRiskLevel
from ..types import UNSET, Unset

T = TypeVar("T", bound="ToolReviewRule")


@_attrs_define(repr=False)
class ToolReviewRule:
    """A matching rule overrides the supplied fields of the global policy.

    Attributes:
        on_flagged (None | ToolReviewRuleOnFlaggedType0 | Unset):
        risk_threshold (None | ToolRiskLevel | Unset):
    """

    on_flagged: ToolReviewRuleOnFlaggedType0 | Unset | None = UNSET
    risk_threshold: ToolRiskLevel | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        on_flagged: str | Unset | None
        if isinstance(self.on_flagged, Unset):
            on_flagged = UNSET
        elif isinstance(self.on_flagged, ToolReviewRuleOnFlaggedType0):
            on_flagged = self.on_flagged.value
        else:
            on_flagged = self.on_flagged

        risk_threshold: str | Unset | None
        if isinstance(self.risk_threshold, Unset):
            risk_threshold = UNSET
        elif isinstance(self.risk_threshold, ToolRiskLevel):
            risk_threshold = self.risk_threshold.value
        else:
            risk_threshold = self.risk_threshold

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if on_flagged is not UNSET:
            field_dict["on_flagged"] = on_flagged
        if risk_threshold is not UNSET:
            field_dict["risk_threshold"] = risk_threshold

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_on_flagged(data: object) -> ToolReviewRuleOnFlaggedType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                on_flagged_type_0 = ToolReviewRuleOnFlaggedType0(data)

                return on_flagged_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ToolReviewRuleOnFlaggedType0 | Unset | None, data)

        on_flagged = _parse_on_flagged(d.pop("on_flagged", UNSET))

        def _parse_risk_threshold(data: object) -> ToolRiskLevel | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                risk_threshold_type_0 = ToolRiskLevel(data)

                return risk_threshold_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ToolRiskLevel | Unset | None, data)

        risk_threshold = _parse_risk_threshold(d.pop("risk_threshold", UNSET))

        tool_review_rule = cls(
            on_flagged=on_flagged,
            risk_threshold=risk_threshold,
        )

        return tool_review_rule
