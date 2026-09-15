from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_model_request_settings import CreateModelRequestSettings
    from ..models.model_declarations import ModelDeclarations


T = TypeVar("T", bound="CreateModelRequest")


@_attrs_define(repr=False)
class CreateModelRequest:
    """
    Attributes:
        key (str):
        name (str):
        provider_id (str):
        upstream_model (str):
        base_model (None | str | Unset):
        declarations (ModelDeclarations | Unset): Harness-facing facts and authoring choices declared for one saved
            Model.
        description (None | str | Unset):
        enabled (bool | Unset):
        model_api (None | str | Unset):
        settings (CreateModelRequestSettings | Unset):
    """

    key: str
    name: str
    provider_id: str
    upstream_model: str
    base_model: str | Unset | None = UNSET
    declarations: ModelDeclarations | Unset = UNSET
    description: str | Unset | None = UNSET
    enabled: bool | Unset = UNSET
    model_api: str | Unset | None = UNSET
    settings: CreateModelRequestSettings | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        name = self.name

        provider_id = self.provider_id

        upstream_model = self.upstream_model

        base_model: str | Unset | None
        if isinstance(self.base_model, Unset):
            base_model = UNSET
        else:
            base_model = self.base_model

        declarations: dict[str, Any] | Unset = UNSET
        if not isinstance(self.declarations, Unset):
            declarations = self.declarations.to_dict()

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        enabled = self.enabled

        model_api: str | Unset | None
        if isinstance(self.model_api, Unset):
            model_api = UNSET
        else:
            model_api = self.model_api

        settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.settings, Unset):
            settings = self.settings.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "key": key,
                "name": name,
                "provider_id": provider_id,
                "upstream_model": upstream_model,
            }
        )
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
        if settings is not UNSET:
            field_dict["settings"] = settings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_model_request_settings import CreateModelRequestSettings
        from ..models.model_declarations import ModelDeclarations

        d = dict(src_dict)
        key = d.pop("key")

        name = d.pop("name")

        provider_id = d.pop("provider_id")

        upstream_model = d.pop("upstream_model")

        def _parse_base_model(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        base_model = _parse_base_model(d.pop("base_model", UNSET))

        _declarations = d.pop("declarations", UNSET)
        declarations: ModelDeclarations | Unset
        if isinstance(_declarations, Unset):
            declarations = UNSET
        else:
            declarations = ModelDeclarations.from_dict(_declarations)

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        enabled = d.pop("enabled", UNSET)

        def _parse_model_api(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        model_api = _parse_model_api(d.pop("model_api", UNSET))

        _settings = d.pop("settings", UNSET)
        settings: CreateModelRequestSettings | Unset
        if isinstance(_settings, Unset):
            settings = UNSET
        else:
            settings = CreateModelRequestSettings.from_dict(_settings)

        create_model_request = cls(
            key=key,
            name=name,
            provider_id=provider_id,
            upstream_model=upstream_model,
            base_model=base_model,
            declarations=declarations,
            description=description,
            enabled=enabled,
            model_api=model_api,
            settings=settings,
        )

        return create_model_request
