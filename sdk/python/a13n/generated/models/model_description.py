from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_description_parameter_support import ModelDescriptionParameterSupport
    from ..models.model_description_settings_schema import ModelDescriptionSettingsSchema
    from ..models.model_description_suggested_settings import ModelDescriptionSuggestedSettings
    from ..models.model_limits import ModelLimits
    from ..models.model_profile import ModelProfile


T = TypeVar("T", bound="ModelDescription")


@_attrs_define(repr=False)
class ModelDescription:
    """
    Attributes:
        settings_schema (ModelDescriptionSettingsSchema):
        suggested_model_api (str):
        upstream_model (str):
        display_name (None | str | Unset):
        limits (ModelLimits | Unset):
        parameter_support (ModelDescriptionParameterSupport | Unset):
        profile (ModelProfile | Unset): Read-only Provider capability information returned by discovery and description.
        suggested_settings (ModelDescriptionSuggestedSettings | Unset):
    """

    settings_schema: ModelDescriptionSettingsSchema
    suggested_model_api: str
    upstream_model: str
    display_name: str | Unset | None = UNSET
    limits: ModelLimits | Unset = UNSET
    parameter_support: ModelDescriptionParameterSupport | Unset = UNSET
    profile: ModelProfile | Unset = UNSET
    suggested_settings: ModelDescriptionSuggestedSettings | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        settings_schema = self.settings_schema.to_dict()

        suggested_model_api = self.suggested_model_api

        upstream_model = self.upstream_model

        display_name: str | Unset | None
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        limits: dict[str, Any] | Unset = UNSET
        if not isinstance(self.limits, Unset):
            limits = self.limits.to_dict()

        parameter_support: dict[str, Any] | Unset = UNSET
        if not isinstance(self.parameter_support, Unset):
            parameter_support = self.parameter_support.to_dict()

        profile: dict[str, Any] | Unset = UNSET
        if not isinstance(self.profile, Unset):
            profile = self.profile.to_dict()

        suggested_settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.suggested_settings, Unset):
            suggested_settings = self.suggested_settings.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "settings_schema": settings_schema,
                "suggested_model_api": suggested_model_api,
                "upstream_model": upstream_model,
            }
        )
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if limits is not UNSET:
            field_dict["limits"] = limits
        if parameter_support is not UNSET:
            field_dict["parameter_support"] = parameter_support
        if profile is not UNSET:
            field_dict["profile"] = profile
        if suggested_settings is not UNSET:
            field_dict["suggested_settings"] = suggested_settings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_description_parameter_support import ModelDescriptionParameterSupport
        from ..models.model_description_settings_schema import ModelDescriptionSettingsSchema
        from ..models.model_description_suggested_settings import ModelDescriptionSuggestedSettings
        from ..models.model_limits import ModelLimits
        from ..models.model_profile import ModelProfile

        d = dict(src_dict)
        settings_schema = ModelDescriptionSettingsSchema.from_dict(d.pop("settings_schema"))

        suggested_model_api = d.pop("suggested_model_api")

        upstream_model = d.pop("upstream_model")

        def _parse_display_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

        _limits = d.pop("limits", UNSET)
        limits: ModelLimits | Unset
        if isinstance(_limits, Unset):
            limits = UNSET
        else:
            limits = ModelLimits.from_dict(_limits)

        _parameter_support = d.pop("parameter_support", UNSET)
        parameter_support: ModelDescriptionParameterSupport | Unset
        if isinstance(_parameter_support, Unset):
            parameter_support = UNSET
        else:
            parameter_support = ModelDescriptionParameterSupport.from_dict(_parameter_support)

        _profile = d.pop("profile", UNSET)
        profile: ModelProfile | Unset
        if isinstance(_profile, Unset):
            profile = UNSET
        else:
            profile = ModelProfile.from_dict(_profile)

        _suggested_settings = d.pop("suggested_settings", UNSET)
        suggested_settings: ModelDescriptionSuggestedSettings | Unset
        if isinstance(_suggested_settings, Unset):
            suggested_settings = UNSET
        else:
            suggested_settings = ModelDescriptionSuggestedSettings.from_dict(_suggested_settings)

        model_description = cls(
            settings_schema=settings_schema,
            suggested_model_api=suggested_model_api,
            upstream_model=upstream_model,
            display_name=display_name,
            limits=limits,
            parameter_support=parameter_support,
            profile=profile,
            suggested_settings=suggested_settings,
        )

        return model_description
