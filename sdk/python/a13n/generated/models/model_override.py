from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_model_characteristics import AgentModelCharacteristics
    from ..models.model_override_settings_type_0 import ModelOverrideSettingsType0


T = TypeVar("T", bound="ModelOverride")


@_attrs_define(repr=False)
class ModelOverride:
    """
    Attributes:
        characteristics (AgentModelCharacteristics | None | Unset):
        model_key (None | str | Unset):
        settings (ModelOverrideSettingsType0 | None | Unset):
    """

    characteristics: AgentModelCharacteristics | Unset | None = UNSET
    model_key: str | Unset | None = UNSET
    settings: ModelOverrideSettingsType0 | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_model_characteristics import AgentModelCharacteristics
        from ..models.model_override_settings_type_0 import ModelOverrideSettingsType0

        characteristics: dict[str, Any] | Unset | None
        if isinstance(self.characteristics, Unset):
            characteristics = UNSET
        elif isinstance(self.characteristics, AgentModelCharacteristics):
            characteristics = self.characteristics.to_dict()
        else:
            characteristics = self.characteristics

        model_key: str | Unset | None
        if isinstance(self.model_key, Unset):
            model_key = UNSET
        else:
            model_key = self.model_key

        settings: dict[str, Any] | Unset | None
        if isinstance(self.settings, Unset):
            settings = UNSET
        elif isinstance(self.settings, ModelOverrideSettingsType0):
            settings = self.settings.to_dict()
        else:
            settings = self.settings

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if characteristics is not UNSET:
            field_dict["characteristics"] = characteristics
        if model_key is not UNSET:
            field_dict["model_key"] = model_key
        if settings is not UNSET:
            field_dict["settings"] = settings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_model_characteristics import AgentModelCharacteristics
        from ..models.model_override_settings_type_0 import ModelOverrideSettingsType0

        d = dict(src_dict)

        def _parse_characteristics(data: object) -> AgentModelCharacteristics | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                characteristics_type_0 = AgentModelCharacteristics.from_dict(data)

                return characteristics_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentModelCharacteristics | Unset | None, data)

        characteristics = _parse_characteristics(d.pop("characteristics", UNSET))

        def _parse_model_key(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        model_key = _parse_model_key(d.pop("model_key", UNSET))

        def _parse_settings(data: object) -> ModelOverrideSettingsType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                settings_type_0 = ModelOverrideSettingsType0.from_dict(data)

                return settings_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ModelOverrideSettingsType0 | Unset | None, data)

        settings = _parse_settings(d.pop("settings", UNSET))

        model_override = cls(
            characteristics=characteristics,
            model_key=model_key,
            settings=settings,
        )

        return model_override
