from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.agent_reviewer_on_error import AgentReviewerOnError
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_reviewer_model_settings_type_0 import AgentReviewerModelSettingsType0


T = TypeVar("T", bound="AgentReviewer")


@_attrs_define(repr=False)
class AgentReviewer:
    """Reviewer selected by immutable managed Model ID, never a provider route.

    Attributes:
        model (str):
        instruction (None | str | Unset):
        model_settings (AgentReviewerModelSettingsType0 | None | Unset):
        on_error (AgentReviewerOnError | Unset):
        shell_instruction (None | str | Unset):
        timeout_seconds (float | Unset):
    """

    model: str
    instruction: str | Unset | None = UNSET
    model_settings: AgentReviewerModelSettingsType0 | Unset | None = UNSET
    on_error: AgentReviewerOnError | Unset = UNSET
    shell_instruction: str | Unset | None = UNSET
    timeout_seconds: float | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_reviewer_model_settings_type_0 import AgentReviewerModelSettingsType0

        model = self.model

        instruction: str | Unset | None
        if isinstance(self.instruction, Unset):
            instruction = UNSET
        else:
            instruction = self.instruction

        model_settings: dict[str, Any] | Unset | None
        if isinstance(self.model_settings, Unset):
            model_settings = UNSET
        elif isinstance(self.model_settings, AgentReviewerModelSettingsType0):
            model_settings = self.model_settings.to_dict()
        else:
            model_settings = self.model_settings

        on_error: str | Unset = UNSET
        if not isinstance(self.on_error, Unset):
            on_error = self.on_error.value

        shell_instruction: str | Unset | None
        if isinstance(self.shell_instruction, Unset):
            shell_instruction = UNSET
        else:
            shell_instruction = self.shell_instruction

        timeout_seconds = self.timeout_seconds

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "model": model,
            }
        )
        if instruction is not UNSET:
            field_dict["instruction"] = instruction
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings
        if on_error is not UNSET:
            field_dict["on_error"] = on_error
        if shell_instruction is not UNSET:
            field_dict["shell_instruction"] = shell_instruction
        if timeout_seconds is not UNSET:
            field_dict["timeout_seconds"] = timeout_seconds

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_reviewer_model_settings_type_0 import AgentReviewerModelSettingsType0

        d = dict(src_dict)
        model = d.pop("model")

        def _parse_instruction(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        instruction = _parse_instruction(d.pop("instruction", UNSET))

        def _parse_model_settings(data: object) -> AgentReviewerModelSettingsType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                model_settings_type_0 = AgentReviewerModelSettingsType0.from_dict(data)

                return model_settings_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentReviewerModelSettingsType0 | Unset | None, data)

        model_settings = _parse_model_settings(d.pop("model_settings", UNSET))

        _on_error = d.pop("on_error", UNSET)
        on_error: AgentReviewerOnError | Unset
        if isinstance(_on_error, Unset):
            on_error = UNSET
        else:
            on_error = AgentReviewerOnError(_on_error)

        def _parse_shell_instruction(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        shell_instruction = _parse_shell_instruction(d.pop("shell_instruction", UNSET))

        timeout_seconds = d.pop("timeout_seconds", UNSET)

        agent_reviewer = cls(
            model=model,
            instruction=instruction,
            model_settings=model_settings,
            on_error=on_error,
            shell_instruction=shell_instruction,
            timeout_seconds=timeout_seconds,
        )

        return agent_reviewer
