from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.configuration_draft_review_mode import ConfigurationDraftReviewMode
from ..models.configuration_draft_review_source_selector import ConfigurationDraftReviewSourceSelector
from ..models.configuration_draft_review_status import ConfigurationDraftReviewStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_config_output import AgentConfigOutput
    from ..models.configuration_application_receipt import ConfigurationApplicationReceipt
    from ..models.configuration_difference import ConfigurationDifference
    from ..models.configuration_revision_view import ConfigurationRevisionView
    from ..models.configuration_validation import ConfigurationValidation
    from ..models.creation_metadata import CreationMetadata


T = TypeVar("T", bound="ConfigurationDraftReview")


@_attrs_define(repr=False)
class ConfigurationDraftReview:
    """
    Attributes:
        base (ConfigurationRevisionView | None):
        base_agent_revision_id (None | str):
        base_to_candidate (list[ConfigurationDifference]):
        base_to_current_target (list[ConfigurationDifference]):
        config (AgentConfigOutput | None):
        content_digest (str):
        created_at (datetime.datetime):
        current_target (ConfigurationRevisionView | None):
        current_target_to_candidate (list[ConfigurationDifference]):
        id (str):
        mode (ConfigurationDraftReviewMode):
        organization_id (str):
        owner_user_id (str):
        session_id (str):
        source (ConfigurationRevisionView | None):
        source_agent_revision_id (None | str):
        source_selector (ConfigurationDraftReviewSourceSelector):
        source_to_candidate (list[ConfigurationDifference]):
        status (ConfigurationDraftReviewStatus):
        target_agent_id (None | str):
        target_conflict (bool):
        thread_id (str):
        updated_at (datetime.datetime):
        version (int):
        workspace_id (str):
        application_receipt (ConfigurationApplicationReceipt | None | Unset):
        base_agent_version (int | None | Unset):
        creation_metadata (CreationMetadata | None | Unset):
        evidence_refs (list[str] | Unset):
        latest_validation (ConfigurationValidation | None | Unset):
        predecessor_draft_id (None | str | Unset):
        source_agent_revision_version (int | None | Unset):
        terminal_reason (None | str | Unset):
    """

    base: ConfigurationRevisionView | None
    base_agent_revision_id: str | None
    base_to_candidate: list[ConfigurationDifference]
    base_to_current_target: list[ConfigurationDifference]
    config: AgentConfigOutput | None
    content_digest: str
    created_at: datetime.datetime
    current_target: ConfigurationRevisionView | None
    current_target_to_candidate: list[ConfigurationDifference]
    id: str
    mode: ConfigurationDraftReviewMode
    organization_id: str
    owner_user_id: str
    session_id: str
    source: ConfigurationRevisionView | None
    source_agent_revision_id: str | None
    source_selector: ConfigurationDraftReviewSourceSelector
    source_to_candidate: list[ConfigurationDifference]
    status: ConfigurationDraftReviewStatus
    target_agent_id: str | None
    target_conflict: bool
    thread_id: str
    updated_at: datetime.datetime
    version: int
    workspace_id: str
    application_receipt: ConfigurationApplicationReceipt | Unset | None = UNSET
    base_agent_version: int | Unset | None = UNSET
    creation_metadata: CreationMetadata | Unset | None = UNSET
    evidence_refs: list[str] | Unset = UNSET
    latest_validation: ConfigurationValidation | Unset | None = UNSET
    predecessor_draft_id: str | Unset | None = UNSET
    source_agent_revision_version: int | Unset | None = UNSET
    terminal_reason: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_config_output import AgentConfigOutput
        from ..models.configuration_application_receipt import ConfigurationApplicationReceipt
        from ..models.configuration_revision_view import ConfigurationRevisionView
        from ..models.configuration_validation import ConfigurationValidation
        from ..models.creation_metadata import CreationMetadata

        base: dict[str, Any] | None
        if isinstance(self.base, ConfigurationRevisionView):
            base = self.base.to_dict()
        else:
            base = self.base

        base_agent_revision_id: str | None
        base_agent_revision_id = self.base_agent_revision_id

        base_to_candidate = []
        for base_to_candidate_item_data in self.base_to_candidate:
            base_to_candidate_item = base_to_candidate_item_data.to_dict()
            base_to_candidate.append(base_to_candidate_item)

        base_to_current_target = []
        for base_to_current_target_item_data in self.base_to_current_target:
            base_to_current_target_item = base_to_current_target_item_data.to_dict()
            base_to_current_target.append(base_to_current_target_item)

        config: dict[str, Any] | None
        if isinstance(self.config, AgentConfigOutput):
            config = self.config.to_dict()
        else:
            config = self.config

        content_digest = self.content_digest

        created_at = self.created_at.isoformat()

        current_target: dict[str, Any] | None
        if isinstance(self.current_target, ConfigurationRevisionView):
            current_target = self.current_target.to_dict()
        else:
            current_target = self.current_target

        current_target_to_candidate = []
        for current_target_to_candidate_item_data in self.current_target_to_candidate:
            current_target_to_candidate_item = current_target_to_candidate_item_data.to_dict()
            current_target_to_candidate.append(current_target_to_candidate_item)

        id = self.id

        mode = self.mode.value

        organization_id = self.organization_id

        owner_user_id = self.owner_user_id

        session_id = self.session_id

        source: dict[str, Any] | None
        if isinstance(self.source, ConfigurationRevisionView):
            source = self.source.to_dict()
        else:
            source = self.source

        source_agent_revision_id: str | None
        source_agent_revision_id = self.source_agent_revision_id

        source_selector = self.source_selector.value

        source_to_candidate = []
        for source_to_candidate_item_data in self.source_to_candidate:
            source_to_candidate_item = source_to_candidate_item_data.to_dict()
            source_to_candidate.append(source_to_candidate_item)

        status = self.status.value

        target_agent_id: str | None
        target_agent_id = self.target_agent_id

        target_conflict = self.target_conflict

        thread_id = self.thread_id

        updated_at = self.updated_at.isoformat()

        version = self.version

        workspace_id = self.workspace_id

        application_receipt: dict[str, Any] | Unset | None
        if isinstance(self.application_receipt, Unset):
            application_receipt = UNSET
        elif isinstance(self.application_receipt, ConfigurationApplicationReceipt):
            application_receipt = self.application_receipt.to_dict()
        else:
            application_receipt = self.application_receipt

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

        predecessor_draft_id: str | Unset | None
        if isinstance(self.predecessor_draft_id, Unset):
            predecessor_draft_id = UNSET
        else:
            predecessor_draft_id = self.predecessor_draft_id

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
                "base": base,
                "base_agent_revision_id": base_agent_revision_id,
                "base_to_candidate": base_to_candidate,
                "base_to_current_target": base_to_current_target,
                "config": config,
                "content_digest": content_digest,
                "created_at": created_at,
                "current_target": current_target,
                "current_target_to_candidate": current_target_to_candidate,
                "id": id,
                "mode": mode,
                "organization_id": organization_id,
                "owner_user_id": owner_user_id,
                "session_id": session_id,
                "source": source,
                "source_agent_revision_id": source_agent_revision_id,
                "source_selector": source_selector,
                "source_to_candidate": source_to_candidate,
                "status": status,
                "target_agent_id": target_agent_id,
                "target_conflict": target_conflict,
                "thread_id": thread_id,
                "updated_at": updated_at,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if application_receipt is not UNSET:
            field_dict["application_receipt"] = application_receipt
        if base_agent_version is not UNSET:
            field_dict["base_agent_version"] = base_agent_version
        if creation_metadata is not UNSET:
            field_dict["creation_metadata"] = creation_metadata
        if evidence_refs is not UNSET:
            field_dict["evidence_refs"] = evidence_refs
        if latest_validation is not UNSET:
            field_dict["latest_validation"] = latest_validation
        if predecessor_draft_id is not UNSET:
            field_dict["predecessor_draft_id"] = predecessor_draft_id
        if source_agent_revision_version is not UNSET:
            field_dict["source_agent_revision_version"] = source_agent_revision_version
        if terminal_reason is not UNSET:
            field_dict["terminal_reason"] = terminal_reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_config_output import AgentConfigOutput
        from ..models.configuration_application_receipt import ConfigurationApplicationReceipt
        from ..models.configuration_difference import ConfigurationDifference
        from ..models.configuration_revision_view import ConfigurationRevisionView
        from ..models.configuration_validation import ConfigurationValidation
        from ..models.creation_metadata import CreationMetadata

        d = dict(src_dict)

        def _parse_base(data: object) -> ConfigurationRevisionView | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                base_type_0 = ConfigurationRevisionView.from_dict(data)

                return base_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConfigurationRevisionView | None, data)

        base = _parse_base(d.pop("base"))

        def _parse_base_agent_revision_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        base_agent_revision_id = _parse_base_agent_revision_id(d.pop("base_agent_revision_id"))

        base_to_candidate = []
        _base_to_candidate = d.pop("base_to_candidate")
        for base_to_candidate_item_data in _base_to_candidate:
            base_to_candidate_item = ConfigurationDifference.from_dict(base_to_candidate_item_data)

            base_to_candidate.append(base_to_candidate_item)

        base_to_current_target = []
        _base_to_current_target = d.pop("base_to_current_target")
        for base_to_current_target_item_data in _base_to_current_target:
            base_to_current_target_item = ConfigurationDifference.from_dict(base_to_current_target_item_data)

            base_to_current_target.append(base_to_current_target_item)

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

        def _parse_current_target(data: object) -> ConfigurationRevisionView | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                current_target_type_0 = ConfigurationRevisionView.from_dict(data)

                return current_target_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConfigurationRevisionView | None, data)

        current_target = _parse_current_target(d.pop("current_target"))

        current_target_to_candidate = []
        _current_target_to_candidate = d.pop("current_target_to_candidate")
        for current_target_to_candidate_item_data in _current_target_to_candidate:
            current_target_to_candidate_item = ConfigurationDifference.from_dict(current_target_to_candidate_item_data)

            current_target_to_candidate.append(current_target_to_candidate_item)

        id = d.pop("id")

        mode = ConfigurationDraftReviewMode(d.pop("mode"))

        organization_id = d.pop("organization_id")

        owner_user_id = d.pop("owner_user_id")

        session_id = d.pop("session_id")

        def _parse_source(data: object) -> ConfigurationRevisionView | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_type_0 = ConfigurationRevisionView.from_dict(data)

                return source_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConfigurationRevisionView | None, data)

        source = _parse_source(d.pop("source"))

        def _parse_source_agent_revision_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        source_agent_revision_id = _parse_source_agent_revision_id(d.pop("source_agent_revision_id"))

        source_selector = ConfigurationDraftReviewSourceSelector(d.pop("source_selector"))

        source_to_candidate = []
        _source_to_candidate = d.pop("source_to_candidate")
        for source_to_candidate_item_data in _source_to_candidate:
            source_to_candidate_item = ConfigurationDifference.from_dict(source_to_candidate_item_data)

            source_to_candidate.append(source_to_candidate_item)

        status = ConfigurationDraftReviewStatus(d.pop("status"))

        def _parse_target_agent_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        target_agent_id = _parse_target_agent_id(d.pop("target_agent_id"))

        target_conflict = d.pop("target_conflict")

        thread_id = d.pop("thread_id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        def _parse_application_receipt(data: object) -> ConfigurationApplicationReceipt | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                application_receipt_type_0 = ConfigurationApplicationReceipt.from_dict(data)

                return application_receipt_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConfigurationApplicationReceipt | Unset | None, data)

        application_receipt = _parse_application_receipt(d.pop("application_receipt", UNSET))

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

        def _parse_predecessor_draft_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        predecessor_draft_id = _parse_predecessor_draft_id(d.pop("predecessor_draft_id", UNSET))

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

        configuration_draft_review = cls(
            base=base,
            base_agent_revision_id=base_agent_revision_id,
            base_to_candidate=base_to_candidate,
            base_to_current_target=base_to_current_target,
            config=config,
            content_digest=content_digest,
            created_at=created_at,
            current_target=current_target,
            current_target_to_candidate=current_target_to_candidate,
            id=id,
            mode=mode,
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            session_id=session_id,
            source=source,
            source_agent_revision_id=source_agent_revision_id,
            source_selector=source_selector,
            source_to_candidate=source_to_candidate,
            status=status,
            target_agent_id=target_agent_id,
            target_conflict=target_conflict,
            thread_id=thread_id,
            updated_at=updated_at,
            version=version,
            workspace_id=workspace_id,
            application_receipt=application_receipt,
            base_agent_version=base_agent_version,
            creation_metadata=creation_metadata,
            evidence_refs=evidence_refs,
            latest_validation=latest_validation,
            predecessor_draft_id=predecessor_draft_id,
            source_agent_revision_version=source_agent_revision_version,
            terminal_reason=terminal_reason,
        )

        return configuration_draft_review
