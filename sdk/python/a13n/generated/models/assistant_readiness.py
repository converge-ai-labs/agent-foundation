from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.assistant_readiness_reason_code import AssistantReadinessReasonCode
from ..models.assistant_readiness_setup_actions_item import AssistantReadinessSetupActionsItem
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.selected_assistant_model import SelectedAssistantModel


T = TypeVar("T", bound="AssistantReadiness")


@_attrs_define(repr=False)
class AssistantReadiness:
    """
    Attributes:
        ready (bool):
        reason_code (AssistantReadinessReasonCode):
        setup_actions (list[AssistantReadinessSetupActionsItem]):
        setup_url (str):
        selected_model (None | SelectedAssistantModel | Unset):
    """

    ready: bool
    reason_code: AssistantReadinessReasonCode
    setup_actions: list[AssistantReadinessSetupActionsItem]
    setup_url: str
    selected_model: SelectedAssistantModel | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.selected_assistant_model import SelectedAssistantModel

        ready = self.ready

        reason_code = self.reason_code.value

        setup_actions = []
        for setup_actions_item_data in self.setup_actions:
            setup_actions_item = setup_actions_item_data.value
            setup_actions.append(setup_actions_item)

        setup_url = self.setup_url

        selected_model: dict[str, Any] | Unset | None
        if isinstance(self.selected_model, Unset):
            selected_model = UNSET
        elif isinstance(self.selected_model, SelectedAssistantModel):
            selected_model = self.selected_model.to_dict()
        else:
            selected_model = self.selected_model

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "ready": ready,
                "reason_code": reason_code,
                "setup_actions": setup_actions,
                "setup_url": setup_url,
            }
        )
        if selected_model is not UNSET:
            field_dict["selected_model"] = selected_model

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.selected_assistant_model import SelectedAssistantModel

        d = dict(src_dict)
        ready = d.pop("ready")

        reason_code = AssistantReadinessReasonCode(d.pop("reason_code"))

        setup_actions = []
        _setup_actions = d.pop("setup_actions")
        for setup_actions_item_data in _setup_actions:
            setup_actions_item = AssistantReadinessSetupActionsItem(setup_actions_item_data)

            setup_actions.append(setup_actions_item)

        setup_url = d.pop("setup_url")

        def _parse_selected_model(data: object) -> SelectedAssistantModel | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                selected_model_type_0 = SelectedAssistantModel.from_dict(data)

                return selected_model_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(SelectedAssistantModel | Unset | None, data)

        selected_model = _parse_selected_model(d.pop("selected_model", UNSET))

        assistant_readiness = cls(
            ready=ready,
            reason_code=reason_code,
            setup_actions=setup_actions,
            setup_url=setup_url,
            selected_model=selected_model,
        )

        return assistant_readiness
