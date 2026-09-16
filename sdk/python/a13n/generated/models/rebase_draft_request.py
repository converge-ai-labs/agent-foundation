from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.agent_config_input import AgentConfigInput


T = TypeVar("T", bound="RebaseDraftRequest")


@_attrs_define(repr=False)
class RebaseDraftRequest:
    """
    Attributes:
        config (AgentConfigInput):
        expected_target_version (int):
        expected_version (int):
    """

    config: AgentConfigInput
    expected_target_version: int
    expected_version: int

    def to_dict(self) -> dict[str, Any]:
        config = self.config.to_dict()

        expected_target_version = self.expected_target_version

        expected_version = self.expected_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "config": config,
                "expected_target_version": expected_target_version,
                "expected_version": expected_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_config_input import AgentConfigInput

        d = dict(src_dict)
        config = AgentConfigInput.from_dict(d.pop("config"))

        expected_target_version = d.pop("expected_target_version")

        expected_version = d.pop("expected_version")

        rebase_draft_request = cls(
            config=config,
            expected_target_version=expected_target_version,
            expected_version=expected_version,
        )

        return rebase_draft_request
