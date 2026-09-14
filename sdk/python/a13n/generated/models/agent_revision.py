from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_config_output import AgentConfigOutput
    from ..models.connection_tool_selection import ConnectionToolSelection
    from ..models.principal_ref import PrincipalRef
    from ..models.resolved_agent_model import ResolvedAgentModel
    from ..models.resolved_skill_binding import ResolvedSkillBinding
    from ..models.resolved_subagent_edge import ResolvedSubagentEdge
    from ..models.system_actor_ref import SystemActorRef


T = TypeVar("T", bound="AgentRevision")


@_attrs_define(repr=False)
class AgentRevision:
    """
    Attributes:
        agent_id (str):
        config (AgentConfigOutput):
        config_digest (str):
        content_digest (str):
        created_at (datetime.datetime):
        created_by (PrincipalRef | SystemActorRef):
        id (str):
        organization_id (str):
        resolved_model (ResolvedAgentModel):
        resolved_skills (list[ResolvedSkillBinding]):
        resolved_subagents (list[ResolvedSubagentEdge]):
        source_revision_id (None | str):
        version (int):
        workspace_id (str):
        connection_tools (list[ConnectionToolSelection] | Unset):
    """

    agent_id: str
    config: AgentConfigOutput
    config_digest: str
    content_digest: str
    created_at: datetime.datetime
    created_by: PrincipalRef | SystemActorRef
    id: str
    organization_id: str
    resolved_model: ResolvedAgentModel
    resolved_skills: list[ResolvedSkillBinding]
    resolved_subagents: list[ResolvedSubagentEdge]
    source_revision_id: str | None
    version: int
    workspace_id: str
    connection_tools: list[ConnectionToolSelection] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.principal_ref import PrincipalRef

        agent_id = self.agent_id

        config = self.config.to_dict()

        config_digest = self.config_digest

        content_digest = self.content_digest

        created_at = self.created_at.isoformat()

        created_by: dict[str, Any]
        if isinstance(self.created_by, PrincipalRef):
            created_by = self.created_by.to_dict()
        else:
            created_by = self.created_by.to_dict()

        id = self.id

        organization_id = self.organization_id

        resolved_model = self.resolved_model.to_dict()

        resolved_skills = []
        for resolved_skills_item_data in self.resolved_skills:
            resolved_skills_item = resolved_skills_item_data.to_dict()
            resolved_skills.append(resolved_skills_item)

        resolved_subagents = []
        for resolved_subagents_item_data in self.resolved_subagents:
            resolved_subagents_item = resolved_subagents_item_data.to_dict()
            resolved_subagents.append(resolved_subagents_item)

        source_revision_id: str | None
        source_revision_id = self.source_revision_id

        version = self.version

        workspace_id = self.workspace_id

        connection_tools: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.connection_tools, Unset):
            connection_tools = []
            for connection_tools_item_data in self.connection_tools:
                connection_tools_item = connection_tools_item_data.to_dict()
                connection_tools.append(connection_tools_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent_id": agent_id,
                "config": config,
                "config_digest": config_digest,
                "content_digest": content_digest,
                "created_at": created_at,
                "created_by": created_by,
                "id": id,
                "organization_id": organization_id,
                "resolved_model": resolved_model,
                "resolved_skills": resolved_skills,
                "resolved_subagents": resolved_subagents,
                "source_revision_id": source_revision_id,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if connection_tools is not UNSET:
            field_dict["connection_tools"] = connection_tools

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_config_output import AgentConfigOutput
        from ..models.connection_tool_selection import ConnectionToolSelection
        from ..models.principal_ref import PrincipalRef
        from ..models.resolved_agent_model import ResolvedAgentModel
        from ..models.resolved_skill_binding import ResolvedSkillBinding
        from ..models.resolved_subagent_edge import ResolvedSubagentEdge
        from ..models.system_actor_ref import SystemActorRef

        d = dict(src_dict)
        agent_id = d.pop("agent_id")

        config = AgentConfigOutput.from_dict(d.pop("config"))

        config_digest = d.pop("config_digest")

        content_digest = d.pop("content_digest")

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

        id = d.pop("id")

        organization_id = d.pop("organization_id")

        resolved_model = ResolvedAgentModel.from_dict(d.pop("resolved_model"))

        resolved_skills = []
        _resolved_skills = d.pop("resolved_skills")
        for resolved_skills_item_data in _resolved_skills:
            resolved_skills_item = ResolvedSkillBinding.from_dict(resolved_skills_item_data)

            resolved_skills.append(resolved_skills_item)

        resolved_subagents = []
        _resolved_subagents = d.pop("resolved_subagents")
        for resolved_subagents_item_data in _resolved_subagents:
            resolved_subagents_item = ResolvedSubagentEdge.from_dict(resolved_subagents_item_data)

            resolved_subagents.append(resolved_subagents_item)

        def _parse_source_revision_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        source_revision_id = _parse_source_revision_id(d.pop("source_revision_id"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        _connection_tools = d.pop("connection_tools", UNSET)
        connection_tools: list[ConnectionToolSelection] | Unset = UNSET
        if _connection_tools is not UNSET:
            connection_tools = []
            for connection_tools_item_data in _connection_tools:
                connection_tools_item = ConnectionToolSelection.from_dict(connection_tools_item_data)

                connection_tools.append(connection_tools_item)

        agent_revision = cls(
            agent_id=agent_id,
            config=config,
            config_digest=config_digest,
            content_digest=content_digest,
            created_at=created_at,
            created_by=created_by,
            id=id,
            organization_id=organization_id,
            resolved_model=resolved_model,
            resolved_skills=resolved_skills,
            resolved_subagents=resolved_subagents,
            source_revision_id=source_revision_id,
            version=version,
            workspace_id=workspace_id,
            connection_tools=connection_tools,
        )

        return agent_revision
