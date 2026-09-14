from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.tool_setup_destination import ToolSetupDestination


T = TypeVar("T", bound="ToolsetCandidateError")


@_attrs_define(repr=False)
class ToolsetCandidateError:
    """
    Attributes:
        code (str):
        path (str):
        setup_destination (None | ToolSetupDestination | Unset):
    """

    code: str
    path: str
    setup_destination: ToolSetupDestination | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.tool_setup_destination import ToolSetupDestination

        code = self.code

        path = self.path

        setup_destination: dict[str, Any] | Unset | None
        if isinstance(self.setup_destination, Unset):
            setup_destination = UNSET
        elif isinstance(self.setup_destination, ToolSetupDestination):
            setup_destination = self.setup_destination.to_dict()
        else:
            setup_destination = self.setup_destination

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "code": code,
                "path": path,
            }
        )
        if setup_destination is not UNSET:
            field_dict["setup_destination"] = setup_destination

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.tool_setup_destination import ToolSetupDestination

        d = dict(src_dict)
        code = d.pop("code")

        path = d.pop("path")

        def _parse_setup_destination(data: object) -> ToolSetupDestination | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                setup_destination_type_0 = ToolSetupDestination.from_dict(data)

                return setup_destination_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ToolSetupDestination | Unset | None, data)

        setup_destination = _parse_setup_destination(d.pop("setup_destination", UNSET))

        toolset_candidate_error = cls(
            code=code,
            path=path,
            setup_destination=setup_destination,
        )

        return toolset_candidate_error
