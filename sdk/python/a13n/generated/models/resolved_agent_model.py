from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.agent_model_characteristics import AgentModelCharacteristics
    from ..models.resolved_agent_model_settings import ResolvedAgentModelSettings


T = TypeVar("T", bound="ResolvedAgentModel")


@_attrs_define(repr=False)
class ResolvedAgentModel:
    """
    Attributes:
        characteristics (AgentModelCharacteristics): Agent-owned context policy layered over Model declarations.
        model_id (str):
        model_key (str):
        settings (ResolvedAgentModelSettings):
    """

    characteristics: AgentModelCharacteristics
    model_id: str
    model_key: str
    settings: ResolvedAgentModelSettings

    def to_dict(self) -> dict[str, Any]:
        characteristics = self.characteristics.to_dict()

        model_id = self.model_id

        model_key = self.model_key

        settings = self.settings.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "characteristics": characteristics,
                "model_id": model_id,
                "model_key": model_key,
                "settings": settings,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_model_characteristics import AgentModelCharacteristics
        from ..models.resolved_agent_model_settings import ResolvedAgentModelSettings

        d = dict(src_dict)
        characteristics = AgentModelCharacteristics.from_dict(d.pop("characteristics"))

        model_id = d.pop("model_id")

        model_key = d.pop("model_key")

        settings = ResolvedAgentModelSettings.from_dict(d.pop("settings"))

        resolved_agent_model = cls(
            characteristics=characteristics,
            model_id=model_id,
            model_key=model_key,
            settings=settings,
        )

        return resolved_agent_model
