from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..models.agent_source import AgentSource
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_labels import AgentLabels
    from ..models.principal_ref import PrincipalRef
    from ..models.system_actor_ref import SystemActorRef


T = TypeVar("T", bound="Agent")


@_attrs_define(repr=False)
class Agent:
    """
    Attributes:
        archived_at (datetime.datetime | None):
        created_at (datetime.datetime):
        created_by (PrincipalRef | SystemActorRef):
        current_revision_id (str):
        description (None | str):
        duplicated_from_agent_id (None | str):
        duplicated_from_revision_id (None | str):
        enabled (bool):
        id (str):
        key (str):
        name (str):
        organization_id (str):
        source (AgentSource):
        updated_at (datetime.datetime):
        updated_by (PrincipalRef | SystemActorRef):
        version (int):
        workspace_id (str):
        default_environment_template_id (None | str | Unset):
        image_url (None | str | Unset):
        labels (AgentLabels | Unset):
        system_purpose (Literal['configuration_assistant'] | None | Unset):
    """

    archived_at: datetime.datetime | None
    created_at: datetime.datetime
    created_by: PrincipalRef | SystemActorRef
    current_revision_id: str
    description: str | None
    duplicated_from_agent_id: str | None
    duplicated_from_revision_id: str | None
    enabled: bool
    id: str
    key: str
    name: str
    organization_id: str
    source: AgentSource
    updated_at: datetime.datetime
    updated_by: PrincipalRef | SystemActorRef
    version: int
    workspace_id: str
    default_environment_template_id: str | Unset | None = UNSET
    image_url: str | Unset | None = UNSET
    labels: AgentLabels | Unset = UNSET
    system_purpose: Literal["configuration_assistant"] | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.principal_ref import PrincipalRef

        archived_at: str | None
        if isinstance(self.archived_at, datetime.datetime):
            archived_at = self.archived_at.isoformat()
        else:
            archived_at = self.archived_at

        created_at = self.created_at.isoformat()

        created_by: dict[str, Any]
        if isinstance(self.created_by, PrincipalRef):
            created_by = self.created_by.to_dict()
        else:
            created_by = self.created_by.to_dict()

        current_revision_id = self.current_revision_id

        description: str | None
        description = self.description

        duplicated_from_agent_id: str | None
        duplicated_from_agent_id = self.duplicated_from_agent_id

        duplicated_from_revision_id: str | None
        duplicated_from_revision_id = self.duplicated_from_revision_id

        enabled = self.enabled

        id = self.id

        key = self.key

        name = self.name

        organization_id = self.organization_id

        source = self.source.value

        updated_at = self.updated_at.isoformat()

        updated_by: dict[str, Any]
        if isinstance(self.updated_by, PrincipalRef):
            updated_by = self.updated_by.to_dict()
        else:
            updated_by = self.updated_by.to_dict()

        version = self.version

        workspace_id = self.workspace_id

        default_environment_template_id: str | Unset | None
        if isinstance(self.default_environment_template_id, Unset):
            default_environment_template_id = UNSET
        else:
            default_environment_template_id = self.default_environment_template_id

        image_url: str | Unset | None
        if isinstance(self.image_url, Unset):
            image_url = UNSET
        else:
            image_url = self.image_url

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        system_purpose: Literal["configuration_assistant"] | Unset | None
        if isinstance(self.system_purpose, Unset):
            system_purpose = UNSET
        else:
            system_purpose = self.system_purpose

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "archived_at": archived_at,
                "created_at": created_at,
                "created_by": created_by,
                "current_revision_id": current_revision_id,
                "description": description,
                "duplicated_from_agent_id": duplicated_from_agent_id,
                "duplicated_from_revision_id": duplicated_from_revision_id,
                "enabled": enabled,
                "id": id,
                "key": key,
                "name": name,
                "organization_id": organization_id,
                "source": source,
                "updated_at": updated_at,
                "updated_by": updated_by,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if default_environment_template_id is not UNSET:
            field_dict["default_environment_template_id"] = default_environment_template_id
        if image_url is not UNSET:
            field_dict["image_url"] = image_url
        if labels is not UNSET:
            field_dict["labels"] = labels
        if system_purpose is not UNSET:
            field_dict["system_purpose"] = system_purpose

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_labels import AgentLabels
        from ..models.principal_ref import PrincipalRef
        from ..models.system_actor_ref import SystemActorRef

        d = dict(src_dict)

        def _parse_archived_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                archived_at_type_0 = datetime.datetime.fromisoformat(data)

                return archived_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        archived_at = _parse_archived_at(d.pop("archived_at"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_created_by(data: object) -> PrincipalRef | SystemActorRef:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                componentsschemas_actor_ref_type_0 = PrincipalRef.from_dict(data)

                return componentsschemas_actor_ref_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            componentsschemas_actor_ref_type_1 = SystemActorRef.from_dict(data)

            return componentsschemas_actor_ref_type_1

        created_by = _parse_created_by(d.pop("created_by"))

        current_revision_id = d.pop("current_revision_id")

        def _parse_description(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        description = _parse_description(d.pop("description"))

        def _parse_duplicated_from_agent_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        duplicated_from_agent_id = _parse_duplicated_from_agent_id(d.pop("duplicated_from_agent_id"))

        def _parse_duplicated_from_revision_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        duplicated_from_revision_id = _parse_duplicated_from_revision_id(d.pop("duplicated_from_revision_id"))

        enabled = d.pop("enabled")

        id = d.pop("id")

        key = d.pop("key")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        source = AgentSource(d.pop("source"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        def _parse_updated_by(data: object) -> PrincipalRef | SystemActorRef:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                componentsschemas_actor_ref_type_0 = PrincipalRef.from_dict(data)

                return componentsschemas_actor_ref_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            componentsschemas_actor_ref_type_1 = SystemActorRef.from_dict(data)

            return componentsschemas_actor_ref_type_1

        updated_by = _parse_updated_by(d.pop("updated_by"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        def _parse_default_environment_template_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        default_environment_template_id = _parse_default_environment_template_id(
            d.pop("default_environment_template_id", UNSET)
        )

        def _parse_image_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        image_url = _parse_image_url(d.pop("image_url", UNSET))

        _labels = d.pop("labels", UNSET)
        labels: AgentLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = AgentLabels.from_dict(_labels)

        def _parse_system_purpose(data: object) -> Literal["configuration_assistant"] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            system_purpose_type_0 = cast(Literal["configuration_assistant"], data)
            if system_purpose_type_0 != "configuration_assistant":
                raise ValueError(
                    f"system_purpose_type_0 must match const 'configuration_assistant', got '{system_purpose_type_0}'"
                )
            return system_purpose_type_0
            return cast(Literal["configuration_assistant"] | Unset | None, data)

        system_purpose = _parse_system_purpose(d.pop("system_purpose", UNSET))

        agent = cls(
            archived_at=archived_at,
            created_at=created_at,
            created_by=created_by,
            current_revision_id=current_revision_id,
            description=description,
            duplicated_from_agent_id=duplicated_from_agent_id,
            duplicated_from_revision_id=duplicated_from_revision_id,
            enabled=enabled,
            id=id,
            key=key,
            name=name,
            organization_id=organization_id,
            source=source,
            updated_at=updated_at,
            updated_by=updated_by,
            version=version,
            workspace_id=workspace_id,
            default_environment_template_id=default_environment_template_id,
            image_url=image_url,
            labels=labels,
            system_purpose=system_purpose,
        )

        return agent
