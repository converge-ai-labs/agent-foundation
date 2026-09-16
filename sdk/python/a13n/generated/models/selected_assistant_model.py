from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.selected_assistant_model_settings import SelectedAssistantModelSettings


T = TypeVar("T", bound="SelectedAssistantModel")


@_attrs_define(repr=False)
class SelectedAssistantModel:
    """
    Attributes:
        model_id (str):
        model_key (str):
        selection_reason (str):
        settings (SelectedAssistantModelSettings):
    """

    model_id: str
    model_key: str
    selection_reason: str
    settings: SelectedAssistantModelSettings

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        model_key = self.model_key

        selection_reason = self.selection_reason

        settings = self.settings.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "model_id": model_id,
                "model_key": model_key,
                "selection_reason": selection_reason,
                "settings": settings,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.selected_assistant_model_settings import SelectedAssistantModelSettings

        d = dict(src_dict)
        model_id = d.pop("model_id")

        model_key = d.pop("model_key")

        selection_reason = d.pop("selection_reason")

        settings = SelectedAssistantModelSettings.from_dict(d.pop("settings"))

        selected_assistant_model = cls(
            model_id=model_id,
            model_key=model_key,
            selection_reason=selection_reason,
            settings=settings,
        )

        return selected_assistant_model
