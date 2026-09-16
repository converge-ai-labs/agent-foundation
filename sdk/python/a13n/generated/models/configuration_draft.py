from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.configuration_draft_mode import ConfigurationDraftMode
from ..models.configuration_draft_source_selector import ConfigurationDraftSourceSelector
from ..models.configuration_draft_status import ConfigurationDraftStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_config_output import AgentConfigOutput
    from ..models.configuration_validation import ConfigurationValidation
    from ..models.creation_metadata import CreationMetadata


T = TypeVar("T", bound="ConfigurationDraft")


@_attrs_define(repr=False)
class ConfigurationDraft:
    """
    Attributes:
        base_agent_revision_id (None | str):
        config (AgentConfigOutput | None):
        content_digest (str):
        created_at (datetime.datetime):
        id (str):
        mode (ConfigurationDraftMode):
        organization_id (str):
        session_id (str):
        source_agent_revision_id (None | str):
        source_selector (ConfigurationDraftSourceSelector):
        status (ConfigurationDraftStatus):
        target_agent_id (None | str):
        updated_at (datetime.datetime):
        version (int):
        workspace_id (str):
        base_agent_version (int | None | Unset):
        creation_metadata (CreationMetadata | None | Unset):
        evidence_refs (list[str] | Unset):
        latest_validation (ConfigurationValidation | None | Unset):
        source_agent_revision_version (int | None | Unset):
        terminal_reason (None | str | Unset):
    """

    base_agent_revision_id: str | None
    config: AgentConfigOutput | None
    content_digest: str
    created_at: datetime.datetime
    id: str
    mode: ConfigurationDraftMode
    organization_id: str
    session_id: str
    source_agent_revision_id: str | None
    source_selector: ConfigurationDraftSourceSelector
    status: ConfigurationDraftStatus
    target_agent_id: str | None
    updated_at: datetime.datetime
    version: int
    workspace_id: str
    base_agent_version: int | Unset | None = UNSET
    creation_metadata: CreationMetadata | Unset | None = UNSET
    evidence_refs: list[str] | Unset = UNSET
    latest_validation: ConfigurationValidation | Unset | None = UNSET
    source_agent_revision_version: int | Unset | None = UNSET
    terminal_reason: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_config_output import AgentConfigOutput
        from ..models.configuration_validation import ConfigurationValidation
        from ..models.creation_metadata import CreationMetadata

        base_agent_revision_id: str | None
        base_agent_revision_id = self.base_agent_revision_id

        config: dict[str, Any] | None
        if isinstance(self.config, AgentConfigOutput):
            config = self.config.to_dict()
        else:
            config = self.config

        content_digest = self.content_digest

        created_at = self.created_at.isoformat()

        id = self.id

        mode = self.mode.value

        organization_id = self.organization_id

        session_id = self.session_id

        source_agent_revision_id: str | None
        source_agent_revision_id = self.source_agent_revision_id

        source_selector = self.source_selector.value

        status = self.status.value

        target_agent_id: str | None
        target_agent_id = self.target_agent_id

        updated_at = self.updated_at.isoformat()

        version = self.version

        workspace_id = self.workspace_id

        base_agent_version: int | Unset | None
        if isinstance(self.base_agent_version, Unset):
            base_agent_version = UNSET
        else:
            base_agent_version = self.base_agent_version

        creation_metadata: dict[str, Any] | Unset | None
        if isinstance(self.creation_metadata, Unset):
            creation_metadata = UNSET
        elif isinstance(self.creation_metadata, CreationMetadata):
            creation_metadata = self.creation_metadata.to_dict()
        else:
            creation_metadata = self.creation_metadata

        evidence_refs: list[str] | Unset = UNSET
        if not isinstance(self.evidence_refs, Unset):
            evidence_refs = self.evidence_refs

        latest_validation: dict[str, Any] | Unset | None
        if isinstance(self.latest_validation, Unset):
            latest_validation = UNSET
        elif isinstance(self.latest_validation, ConfigurationValidation):
            latest_validation = self.latest_validation.to_dict()
        else:
            latest_validation = self.latest_validation

        source_agent_revision_version: int | Unset | None
        if isinstance(self.source_agent_revision_version, Unset):
            source_agent_revision_version = UNSET
        else:
            source_agent_revision_version = self.source_agent_revision_version

        terminal_reason: str | Unset | None
        if isinstance(self.terminal_reason, Unset):
            terminal_reason = UNSET
        else:
            terminal_reason = self.terminal_reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "base_agent_revision_id": base_agent_revision_id,
                "config": config,
                "content_digest": content_digest,
                "created_at": created_at,
                "id": id,
                "mode": mode,
                "organization_id": organization_id,
                "session_id": session_id,
                "source_agent_revision_id": source_agent_revision_id,
                "source_selector": source_selector,
                "status": status,
                "target_agent_id": target_agent_id,
                "updated_at": updated_at,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if base_agent_version is not UNSET:
            field_dict["base_agent_version"] = base_agent_version
        if creation_metadata is not UNSET:
            field_dict["creation_metadata"] = creation_metadata
        if evidence_refs is not UNSET:
            field_dict["evidence_refs"] = evidence_refs
        if latest_validation is not UNSET:
            field_dict["latest_validation"] = latest_validation
        if source_agent_revision_version is not UNSET:
            field_dict["source_agent_revision_version"] = source_agent_revision_version
        if terminal_reason is not UNSET:
            field_dict["terminal_reason"] = terminal_reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_config_output import AgentConfigOutput
        from ..models.configuration_validation import ConfigurationValidation
        from ..models.creation_metadata import CreationMetadata

        d = dict(src_dict)

        def _parse_base_agent_revision_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        base_agent_revision_id = _parse_base_agent_revision_id(d.pop("base_agent_revision_id"))

        def _parse_config(data: object) -> AgentConfigOutput | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = AgentConfigOutput.from_dict(data)

                return config_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentConfigOutput | None, data)

        config = _parse_config(d.pop("config"))

        content_digest = d.pop("content_digest")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        mode = ConfigurationDraftMode(d.pop("mode"))

        organization_id = d.pop("organization_id")

        session_id = d.pop("session_id")

        def _parse_source_agent_revision_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        source_agent_revision_id = _parse_source_agent_revision_id(d.pop("source_agent_revision_id"))

        source_selector = ConfigurationDraftSourceSelector(d.pop("source_selector"))

        status = ConfigurationDraftStatus(d.pop("status"))

        def _parse_target_agent_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        target_agent_id = _parse_target_agent_id(d.pop("target_agent_id"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        def _parse_base_agent_version(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        base_agent_version = _parse_base_agent_version(d.pop("base_agent_version", UNSET))

        def _parse_creation_metadata(data: object) -> CreationMetadata | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                creation_metadata_type_0 = CreationMetadata.from_dict(data)

                return creation_metadata_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CreationMetadata | Unset | None, data)

        creation_metadata = _parse_creation_metadata(d.pop("creation_metadata", UNSET))

        evidence_refs = cast(list[str], d.pop("evidence_refs", UNSET))

        def _parse_latest_validation(data: object) -> ConfigurationValidation | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                latest_validation_type_0 = ConfigurationValidation.from_dict(data)

                return latest_validation_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConfigurationValidation | Unset | None, data)

        latest_validation = _parse_latest_validation(d.pop("latest_validation", UNSET))

        def _parse_source_agent_revision_version(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        source_agent_revision_version = _parse_source_agent_revision_version(
            d.pop("source_agent_revision_version", UNSET)
        )

        def _parse_terminal_reason(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        terminal_reason = _parse_terminal_reason(d.pop("terminal_reason", UNSET))

        configuration_draft = cls(
            base_agent_revision_id=base_agent_revision_id,
            config=config,
            content_digest=content_digest,
            created_at=created_at,
            id=id,
            mode=mode,
            organization_id=organization_id,
            session_id=session_id,
            source_agent_revision_id=source_agent_revision_id,
            source_selector=source_selector,
            status=status,
            target_agent_id=target_agent_id,
            updated_at=updated_at,
            version=version,
            workspace_id=workspace_id,
            base_agent_version=base_agent_version,
            creation_metadata=creation_metadata,
            evidence_refs=evidence_refs,
            latest_validation=latest_validation,
            source_agent_revision_version=source_agent_revision_version,
            terminal_reason=terminal_reason,
        )

        return configuration_draft
