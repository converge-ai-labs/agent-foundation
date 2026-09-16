"""Complete candidate resolution and behavior-relevant dependency observations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import ResolvedRevisionContent
from a13n_service.agents.resolution import PreparedRevisionResolution
from a13n_service.agents.toolsets import web_selection
from a13n_service.connectivity.connections.models import ConnectionRecord
from a13n_service.connectivity.connectors.models import ConnectorProviderRecord
from a13n_service.digests import digest_request
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentTemplateRevisionRecord
from a13n_service.memory.models import MemoryProviderRecord
from a13n_service.models.models import ModelProviderRecord
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord
from a13n_service.temporal import assume_utc
from a13n_service.web.domain import provider_selections
from a13n_service.web.models import WebProviderRecord


async def dependency_digest(
    session: AsyncSession,
    *,
    prepared: PreparedRevisionResolution,
    resolved: ResolvedRevisionContent,
) -> str:
    """Keep observations distinct from the immutable Agent authoring digest."""
    models = [prepared.model]
    if prepared.reviewer_model is not None:
        models.append(prepared.reviewer_model)
    model_observations = []
    for model in models:
        provider = await session.get(ModelProviderRecord, model.resource.provider_id)
        model_observations.append(
            {
                "model": model.resource.model_dump(mode="json"),
                "provider_updated_at": None if provider is None else assume_utc(provider.updated_at).isoformat(),
            }
        )
    skills = []
    for selected in resolved.resolved_skills:
        record = await session.get(SkillRecord, selected.skill_id)
        revision = (
            None
            if record is None
            else await session.scalar(
                select(SkillRevisionRecord).where(
                    SkillRevisionRecord.skill_id == selected.skill_id,
                    SkillRevisionRecord.version
                    == (selected.version if selected.version is not None else record.version),
                )
            )
        )
        skills.append(
            {"skill_id": selected.skill_id, "revision_digest": None if revision is None else revision.content_digest}
        )
    dependencies = []

    async def observe(record_type, resource_id: str) -> None:
        # Compare safe generations, never credential values or ciphertext hashes.
        row = (
            await session.execute(
                select(record_type.updated_at, record_type.enabled, record_type.credential_generation).where(
                    record_type.id == resource_id
                )
            )
        ).one_or_none()
        dependencies.append(
            {
                "kind": record_type.__tablename__,
                "id": resource_id,
                "updated_at": None if row is None else assume_utc(row.updated_at).isoformat(),
                "enabled": None if row is None else row.enabled,
                "credential_generation": None if row is None else row.credential_generation,
            }
        )

    for _, selection in provider_selections(web_selection(prepared.config.toolsets)):
        await observe(WebProviderRecord, selection.provider_id)
    if prepared.config.memory is not None:
        await observe(MemoryProviderRecord, prepared.config.memory.provider_id)
    for selection in prepared.config.subagents.values():
        if selection.environment.template_revision_id is not None:
            provider_id = await session.scalar(
                select(EnvironmentTemplateRevisionRecord.provider_id).where(
                    EnvironmentTemplateRevisionRecord.id == selection.environment.template_revision_id
                )
            )
            if provider_id is not None:
                await observe(EnvironmentProviderRecord, provider_id)
    connections = []
    for selection in prepared.connectivity.selections.connection_selections:
        row = (
            await session.execute(
                select(
                    ConnectionRecord.updated_at,
                    ConnectionRecord.version,
                    ConnectionRecord.authorization_generation,
                    ConnectionRecord.credential_generation,
                    ConnectionRecord.status,
                ).where(ConnectionRecord.id == selection.connection_id)
            )
        ).one_or_none()
        connections.append(
            {
                "selection": selection.model_dump(mode="json"),
                "updated_at": None if row is None else assume_utc(row.updated_at).isoformat(),
                "version": None if row is None else row.version,
                "authorization_generation": None if row is None else row.authorization_generation,
                "credential_generation": None if row is None else row.credential_generation,
                "status": None if row is None else row.status,
            }
        )
        if selection.connector_provider_id is not None:
            await observe(ConnectorProviderRecord, selection.connector_provider_id)
    return digest_request(
        {
            "resolved": resolved.model_dump(mode="json"),
            "models": model_observations,
            "skills": skills,
            "connections": connections,
            "dependencies": dependencies,
        }
    )
