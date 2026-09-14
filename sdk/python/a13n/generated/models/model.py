from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_declarations import ModelDeclarations
    from ..models.model_settings import ModelSettings
    from ..models.principal_ref import PrincipalRef


T = TypeVar("T", bound="Model")


@_attrs_define(repr=False)
class Model:
    """
    Attributes:
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        description (None | str):
        enabled (bool):
        id (str):
        key (str):
        model_api (str):
        name (str):
        organization_id (str):
        provider_id (str):
        updated_at (datetime.datetime):
        updated_by (PrincipalRef):
        upstream_model (str):
        workspace_id (None | str):
        declarations (ModelDeclarations | Unset): Harness-facing facts and authoring choices declared for one saved
            Model.
        settings (ModelSettings | Unset):
    """

    created_at: datetime.datetime
    created_by: PrincipalRef
    description: str | None
    enabled: bool
    id: str
    key: str
    model_api: str
    name: str
    organization_id: str
    provider_id: str
    updated_at: datetime.datetime
    updated_by: PrincipalRef
    upstream_model: str
    workspace_id: str | None
    declarations: ModelDeclarations | Unset = UNSET
    settings: ModelSettings | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        description: str | None
        description = self.description

        enabled = self.enabled

        id = self.id

        key = self.key

        model_api = self.model_api

        name = self.name

        organization_id = self.organization_id

        provider_id = self.provider_id

        updated_at = self.updated_at.isoformat()

        updated_by = self.updated_by.to_dict()

        upstream_model = self.upstream_model

        workspace_id: str | None
        workspace_id = self.workspace_id

        declarations: dict[str, Any] | Unset = UNSET
        if not isinstance(self.declarations, Unset):
            declarations = self.declarations.to_dict()

        settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.settings, Unset):
            settings = self.settings.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "created_at": created_at,
                "created_by": created_by,
                "description": description,
                "enabled": enabled,
                "id": id,
                "key": key,
                "model_api": model_api,
                "name": name,
                "organization_id": organization_id,
                "provider_id": provider_id,
                "updated_at": updated_at,
                "updated_by": updated_by,
                "upstream_model": upstream_model,
                "workspace_id": workspace_id,
            }
        )
        if declarations is not UNSET:
            field_dict["declarations"] = declarations
        if settings is not UNSET:
            field_dict["settings"] = settings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_declarations import ModelDeclarations
        from ..models.model_settings import ModelSettings
        from ..models.principal_ref import PrincipalRef

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        def _parse_description(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        description = _parse_description(d.pop("description"))

        enabled = d.pop("enabled")

        id = d.pop("id")

        key = d.pop("key")

        model_api = d.pop("model_api")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        provider_id = d.pop("provider_id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        updated_by = PrincipalRef.from_dict(d.pop("updated_by"))

        upstream_model = d.pop("upstream_model")

        def _parse_workspace_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        workspace_id = _parse_workspace_id(d.pop("workspace_id"))

        _declarations = d.pop("declarations", UNSET)
        declarations: ModelDeclarations | Unset
        if isinstance(_declarations, Unset):
            declarations = UNSET
        else:
            declarations = ModelDeclarations.from_dict(_declarations)

        _settings = d.pop("settings", UNSET)
        settings: ModelSettings | Unset
        if isinstance(_settings, Unset):
            settings = UNSET
        else:
            settings = ModelSettings.from_dict(_settings)

        model = cls(
            created_at=created_at,
            created_by=created_by,
            description=description,
            enabled=enabled,
            id=id,
            key=key,
            model_api=model_api,
            name=name,
            organization_id=organization_id,
            provider_id=provider_id,
            updated_at=updated_at,
            updated_by=updated_by,
            upstream_model=upstream_model,
            workspace_id=workspace_id,
            declarations=declarations,
            settings=settings,
        )

        return model
