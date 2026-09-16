from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.agent_input import AgentInput


T = TypeVar("T", bound="ConfigurationInputRequest")


@_attrs_define(repr=False)
class ConfigurationInputRequest:
    """
    Attributes:
        expected_thread_version (int):
        input_ (AgentInput): Submitted or retained versioned ordinary Agent input.
    """

    expected_thread_version: int
    input_: AgentInput

    def to_dict(self) -> dict[str, Any]:
        expected_thread_version = self.expected_thread_version

        input_ = self.input_.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_thread_version": expected_thread_version,
                "input": input_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_input import AgentInput

        d = dict(src_dict)
        expected_thread_version = d.pop("expected_thread_version")

        input_ = AgentInput.from_dict(d.pop("input"))

        configuration_input_request = cls(
            expected_thread_version=expected_thread_version,
            input_=input_,
        )

        return configuration_input_request
