from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_input import AgentInput
    from ..models.source_selection import SourceSelection


T = TypeVar("T", bound="ConfigurationInputRequest")


@_attrs_define(repr=False)
class ConfigurationInputRequest:
    """
    Attributes:
        expected_thread_version (int):
        input_ (AgentInput): Submitted or retained versioned ordinary Agent input.
        source (None | SourceSelection | Unset):
    """

    expected_thread_version: int
    input_: AgentInput
    source: SourceSelection | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.source_selection import SourceSelection

        expected_thread_version = self.expected_thread_version

        input_ = self.input_.to_dict()

        source: dict[str, Any] | Unset | None
        if isinstance(self.source, Unset):
            source = UNSET
        elif isinstance(self.source, SourceSelection):
            source = self.source.to_dict()
        else:
            source = self.source

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_thread_version": expected_thread_version,
                "input": input_,
            }
        )
        if source is not UNSET:
            field_dict["source"] = source

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_input import AgentInput
        from ..models.source_selection import SourceSelection

        d = dict(src_dict)
        expected_thread_version = d.pop("expected_thread_version")

        input_ = AgentInput.from_dict(d.pop("input"))

        def _parse_source(data: object) -> SourceSelection | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_type_0 = SourceSelection.from_dict(data)

                return source_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(SourceSelection | Unset | None, data)

        source = _parse_source(d.pop("source", UNSET))

        configuration_input_request = cls(
            expected_thread_version=expected_thread_version,
            input_=input_,
            source=source,
        )

        return configuration_input_request
