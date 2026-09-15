from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_declarations import ModelDeclarations
    from ..models.update_model_request_settings_type_0 import UpdateModelRequestSettingsType0


T = TypeVar("T", bound="UpdateModelRequest")


@_attrs_define(repr=False)
class UpdateModelRequest:
    """
    Attributes:
        base_model (None | str | Unset):
        declarations (ModelDeclarations | None | Unset):
        description (None | str | Unset):
        enabled (bool | None | Unset):
        model_api (None | str | Unset):
        name (None | str | Unset):
        settings (None | Unset | UpdateModelRequestSettingsType0):
        upstream_model (None | str | Unset):
    """

    base_model: str | Unset | None = UNSET
    declarations: ModelDeclarations | Unset | None = UNSET
    description: str | Unset | None = UNSET
    enabled: bool | Unset | None = UNSET
    model_api: str | Unset | None = UNSET
    name: str | Unset | None = UNSET
    settings: Unset | UpdateModelRequestSettingsType0 | None = UNSET
    upstream_model: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.model_declarations import ModelDeclarations
        from ..models.update_model_request_settings_type_0 import UpdateModelRequestSettingsType0

        base_model: str | Unset | None
        if isinstance(self.base_model, Unset):
            base_model = UNSET
        else:
            base_model = self.base_model

        declarations: dict[str, Any] | Unset | None
        if isinstance(self.declarations, Unset):
            declarations = UNSET
        elif isinstance(self.declarations, ModelDeclarations):
            declarations = self.declarations.to_dict()
        else:
            declarations = self.declarations

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        enabled: bool | Unset | None
        if isinstance(self.enabled, Unset):
            enabled = UNSET
        else:
            enabled = self.enabled

        model_api: str | Unset | None
        if isinstance(self.model_api, Unset):
            model_api = UNSET
        else:
            model_api = self.model_api

        name: str | Unset | None
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        settings: dict[str, Any] | Unset | None
        if isinstance(self.settings, Unset):
            settings = UNSET
        elif isinstance(self.settings, UpdateModelRequestSettingsType0):
            settings = self.settings.to_dict()
        else:
            settings = self.settings

        upstream_model: str | Unset | None
        if isinstance(self.upstream_model, Unset):
            upstream_model = UNSET
        else:
            upstream_model = self.upstream_model

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if base_model is not UNSET:
            field_dict["base_model"] = base_model
        if declarations is not UNSET:
            field_dict["declarations"] = declarations
        if description is not UNSET:
            field_dict["description"] = description
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if model_api is not UNSET:
            field_dict["model_api"] = model_api
        if name is not UNSET:
            field_dict["name"] = name
        if settings is not UNSET:
            field_dict["settings"] = settings
        if upstream_model is not UNSET:
            field_dict["upstream_model"] = upstream_model

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_declarations import ModelDeclarations
        from ..models.update_model_request_settings_type_0 import UpdateModelRequestSettingsType0

        d = dict(src_dict)

        def _parse_base_model(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        base_model = _parse_base_model(d.pop("base_model", UNSET))

        def _parse_declarations(data: object) -> ModelDeclarations | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                declarations_type_0 = ModelDeclarations.from_dict(data)

                return declarations_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ModelDeclarations | Unset | None, data)

        declarations = _parse_declarations(d.pop("declarations", UNSET))

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_enabled(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | Unset | None, data)

        enabled = _parse_enabled(d.pop("enabled", UNSET))

        def _parse_model_api(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        model_api = _parse_model_api(d.pop("model_api", UNSET))

        def _parse_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_settings(data: object) -> Unset | UpdateModelRequestSettingsType0 | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                settings_type_0 = UpdateModelRequestSettingsType0.from_dict(data)

                return settings_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Unset | UpdateModelRequestSettingsType0 | None, data)

        settings = _parse_settings(d.pop("settings", UNSET))

        def _parse_upstream_model(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        upstream_model = _parse_upstream_model(d.pop("upstream_model", UNSET))

        update_model_request = cls(
            base_model=base_model,
            declarations=declarations,
            description=description,
            enabled=enabled,
            model_api=model_api,
            name=name,
            settings=settings,
            upstream_model=upstream_model,
        )

        return update_model_request
