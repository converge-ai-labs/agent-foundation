"""Bounded resource facts for configuration tools; no credential material is selected."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.models import AgentRecord
from a13n_service.collection_cursors import decode_collection_cursor, encode_collection_cursor
from a13n_service.connectivity.connections.models import ConnectionRecord
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentTemplateRecord
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent_scoped_collection, authorize_workspace
from a13n_service.iam.authorization import PrincipalPermissions
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.memory.models import MemoryProviderRecord
from a13n_service.models.domain import ModelDeclarations
from a13n_service.models.models import ModelProviderRecord, ModelRecord
from a13n_service.models.settings import settings_schema
from a13n_service.secrets.models import SecretRecord
from a13n_service.skills.models import SkillRecord
from a13n_service.storage import short_session
from a13n_service.web.models import WebProviderRecord

from .context import StrictModel
from .persistence import failure, not_found

ResourceKind = Literal[
    "model",
    "model_provider",
    "web_provider",
    "memory_provider",
    "agent",
    "connection",
    "skill",
    "secret",
    "environment_template",
    "environment_provider",
]


class ConfigurationResource(StrictModel):
    kind: ResourceKind
    id: str
    name: str = Field(max_length=256)
    key: str | None = None
    type: str | None = None
    version: int | None = None
    revision_id: str | None = None
    available: bool
    authorization_needed: bool = False
    owner_type: Literal["workspace", "user"] | None = None
    owner_id: str | None = None
    capabilities: tuple[str, ...] = ()
    parameter_schema: dict[str, JsonValue] | None = None


class ConfigurationResourcePage(StrictModel):
    items: tuple[ConfigurationResource, ...]
    next_cursor: str | None = None


_ACTIONS = {
    "model": WorkspaceAction.models_read,
    "model_provider": WorkspaceAction.models_read,
    "web_provider": WorkspaceAction.web_provider_read,
    "memory_provider": WorkspaceAction.memory_provider_read,
    "agent": WorkspaceAction.agent_read,
    "connection": WorkspaceAction.connection_read,
    "skill": WorkspaceAction.skill_read,
    "secret": WorkspaceAction.secrets_read,
    "environment_template": WorkspaceAction.environment_template_read,
    "environment_provider": WorkspaceAction.environment_provider_read,
}


class ConfigurationResources:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def search(
        self,
        *,
        actor: AuthenticatedActor,
        kind: ResourceKind,
        query: str = "",
        limit: int = 20,
        cursor: str | None = None,
        snapshot: PrincipalPermissions | None = None,
    ) -> ConfigurationResourcePage:
        if not 1 <= limit <= 50 or len(query) > 128:
            raise failure("configuration_resource_query_invalid", "Use a bounded resource search.")
        scope: dict[str, object] = {
            "workspace_id": actor.workspace_id,
            "owner": actor.principal.principal_id,
            "kind": kind,
            "query": query,
        }
        boundary = None
        if cursor is not None:
            try:
                decoded = decode_collection_cursor(cursor, scope=scope, kind="configuration_resources")
                boundary = decoded["id"]
                if not isinstance(boundary, str) or len(boundary) > 72:
                    raise ValueError("Invalid resource boundary")
            except (ValueError, KeyError, TypeError) as error:
                raise failure("invalid_cursor", "The resource cursor is invalid.") from error
        async with short_session(self._sessions) as session:
            statement, record, name = await resource_query(session, actor=actor, kind=kind, snapshot=snapshot)
            if query:
                statement = statement.where(or_(name.icontains(query, autoescape=True), record.id == query))
            if boundary is not None:
                statement = statement.where(record.id > boundary)
            rows = (await session.execute(statement.order_by(record.id).limit(limit + 1))).mappings().all()
        return ConfigurationResourcePage(
            items=tuple(project(kind, row) for row in rows[:limit]),
            next_cursor=encode_collection_cursor(
                {"id": rows[limit - 1]["id"]}, scope=scope, kind="configuration_resources"
            )
            if len(rows) > limit
            else None,
        )

    async def get(
        self,
        *,
        actor: AuthenticatedActor,
        kind: ResourceKind,
        resource_id: str,
        snapshot: PrincipalPermissions | None = None,
    ) -> ConfigurationResource:
        async with short_session(self._sessions) as session:
            statement, record, _ = await resource_query(session, actor=actor, kind=kind, snapshot=snapshot)
            row = (await session.execute(statement.where(record.id == resource_id))).mappings().one_or_none()
        if row is None:
            raise not_found()
        return project(kind, row)


async def resource_query(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    kind: ResourceKind,
    snapshot: PrincipalPermissions | None,
):
    if kind == "agent":
        scope = await authorize_agent_scoped_collection(
            session, actor=actor, workspace_id=actor.workspace_id, action=WorkspaceAction.agent_read
        )
        statement = select(
            AgentRecord.id,
            AgentRecord.name,
            AgentRecord.key,
            AgentRecord.version,
            AgentRecord.current_revision_id,
            AgentRecord.enabled,
            AgentRecord.archived_at,
        ).where(AgentRecord.workspace_id == actor.workspace_id, AgentRecord.system_purpose.is_(None))
        if scope.visible_agent_ids is not None:
            statement = statement.where(AgentRecord.id.in_(scope.visible_agent_ids))
        if snapshot is not None and WorkspaceAction.agent_read not in snapshot.workspace_actions:
            statement = statement.where(
                AgentRecord.id.in_(
                    [key for key, _ in snapshot.agent_actions if WorkspaceAction.agent_read in snapshot.for_agent(key)]
                )
            )
        return statement, AgentRecord, AgentRecord.name
    access = await authorize_workspace(
        session, actor=actor, workspace_id=actor.workspace_id, action=_ACTIONS[kind], snapshot=snapshot
    )
    match kind:
        case "model":
            record = ModelRecord
            columns = (record.id, record.name, record.key, record.model_api, record.enabled, record.declarations)
        case "model_provider":
            record = ModelProviderRecord
            columns = (record.id, record.name, record.type, record.enabled, record.credential_configured)
        case "web_provider":
            record = WebProviderRecord
            columns = (record.id, record.name, record.type, record.enabled, record.credential_generation)
        case "memory_provider":
            record = MemoryProviderRecord
            columns = (record.id, record.name, record.type, record.enabled, record.credential_generation)
        case "connection":
            record = ConnectionRecord
            columns = (record.id, record.name, record.kind, record.status, record.version)
        case "skill":
            record = SkillRecord
            columns = (record.id, record.name, record.key, record.version, record.current_revision_id)
        case "secret":
            record = SecretRecord
            columns = (
                record.id,
                record.key.label("name"),
                record.key,
                record.version,
                record.owner_type,
                record.owner_id,
            )
        case "environment_template":
            record = EnvironmentTemplateRecord
            columns = (record.id, record.name, record.version, record.current_revision_id, record.archived_at)
        case "environment_provider":
            record = EnvironmentProviderRecord
            columns = (record.id, record.name, record.type, record.enabled)
    statement = select(*columns).where(
        record.organization_id == access.organization_id, visible_workspace(record.workspace_id, actor.workspace_id)
    )
    for deletable in (ConnectionRecord, SkillRecord, SecretRecord):
        if record is deletable:
            statement = statement.where(deletable.deleted_at.is_(None))
    if kind == "secret":
        statement = statement.where(
            or_(
                and_(SecretRecord.owner_type == "workspace", SecretRecord.owner_id == actor.workspace_id),
                and_(SecretRecord.owner_type == "user", SecretRecord.owner_id == actor.principal.principal_id),
            )
        )
    return statement, record, columns[1]


def project(kind: ResourceKind, row) -> ConfigurationResource:
    """Never serialize an administrative DTO or arbitrary resource configuration."""
    common = {"kind": kind, "id": row["id"], "name": row["name"], "available": True}
    match kind:
        case "model":
            declarations = ModelDeclarations.model_validate(row["declarations"])
            return ConfigurationResource(
                **{**common, "available": row["enabled"]},
                key=row["key"],
                type=row["model_api"],
                capabilities=tuple(
                    sorted(
                        {
                            *declarations.capabilities,
                            *({"tools"} if declarations.supports_tools else set()),
                        }
                    )
                ),
                parameter_schema=safe_schema(settings_schema(row["model_api"])),
            )
        case "model_provider":
            return ConfigurationResource(
                **{**common, "available": row["enabled"]},
                type=row["type"],
                authorization_needed=not row["credential_configured"],
            )
        case "web_provider" | "memory_provider":
            return ConfigurationResource(
                **{**common, "available": row["enabled"]},
                type=row["type"],
                authorization_needed=row["credential_generation"] == 0,
            )
        case "agent":
            return ConfigurationResource(
                **{**common, "available": row["enabled"] and row["archived_at"] is None},
                key=row["key"],
                version=row["version"],
                revision_id=row["current_revision_id"],
            )
        case "connection":
            return ConfigurationResource(
                **{**common, "available": row["status"] == "ready"},
                type=row["kind"],
                version=row["version"],
                authorization_needed=row["status"] in {"action_required", "pending"},
            )
        case "skill":
            return ConfigurationResource(
                **common, key=row["key"], version=row["version"], revision_id=row["current_revision_id"]
            )
        case "secret":
            return ConfigurationResource(
                **common, key=row["key"], version=row["version"], owner_type=row["owner_type"], owner_id=row["owner_id"]
            )
        case "environment_template":
            return ConfigurationResource(
                **{**common, "available": row["archived_at"] is None},
                version=row["version"],
                revision_id=row["current_revision_id"],
            )
        case "environment_provider":
            return ConfigurationResource(**{**common, "available": row["enabled"]}, type=row["type"])


def safe_schema(value: object, *, depth: int = 0) -> dict[str, JsonValue]:
    """Project structural schema only; free text, examples and defaults carry no authority."""
    if not isinstance(value, dict) or depth > 8:
        return {}
    result: dict[str, JsonValue] = {}
    for key in ("type", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        selected = value.get(key)
        if key == "type":
            if selected in ("object", "array", "string", "number", "integer", "boolean", "null"):
                result[key] = selected
        elif isinstance(selected, (int, float)):
            result[key] = selected
    if isinstance(value.get("properties"), dict):
        properties = value["properties"]
        result["properties"] = {
            name: safe_schema(schema, depth=depth + 1)
            for name, schema in list(properties.items())[:128]
            if isinstance(name, str) and len(name) <= 128
        }
        if isinstance(value.get("required"), list):
            result["required"] = [name for name in value["required"][:128] if name in result["properties"]]
    if isinstance(value.get("items"), dict):
        result["items"] = safe_schema(value["items"], depth=depth + 1)
    if isinstance(value.get("additionalProperties"), bool):
        result["additionalProperties"] = value["additionalProperties"]
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(value.get(key), list):
            result[key] = [safe_schema(item, depth=depth + 1) for item in value[key][:16]]
    return result
