from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_model_settings import AgentModelSettings
    from ..models.harness_model_characteristics import HarnessModelCharacteristics


T = TypeVar("T", bound="AgentModel")


@_attrs_define(repr=False)
class AgentModel:
    """
    Attributes:
        model_key (str):
        characteristics (HarnessModelCharacteristics | Unset): Resolved Harness characteristics of the active Agent
            model.
        settings (AgentModelSettings | Unset):
    """

    model_key: str
    characteristics: HarnessModelCharacteristics | Unset = UNSET
    settings: AgentModelSettings | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        model_key = self.model_key

        characteristics: dict[str, Any] | Unset = UNSET
        if not isinstance(self.characteristics, Unset):
            characteristics = self.characteristics.to_dict()

        settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.settings, Unset):
            settings = self.settings.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "model_key": model_key,
            }
        )
        if characteristics is not UNSET:
            field_dict["characteristics"] = characteristics
        if settings is not UNSET:
            field_dict["settings"] = settings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_model_settings import AgentModelSettings
        from ..models.harness_model_characteristics import HarnessModelCharacteristics

        d = dict(src_dict)
        model_key = d.pop("model_key")

        _characteristics = d.pop("characteristics", UNSET)
        characteristics: HarnessModelCharacteristics | Unset
        if isinstance(_characteristics, Unset):
            characteristics = UNSET
        else:
            characteristics = HarnessModelCharacteristics.from_dict(_characteristics)

        _settings = d.pop("settings", UNSET)
        settings: AgentModelSettings | Unset
        if isinstance(_settings, Unset):
            settings = UNSET
        else:
            settings = AgentModelSettings.from_dict(_settings)

        agent_model = cls(
            model_key=model_key,
            characteristics=characteristics,
            settings=settings,
        )

        return agent_model
