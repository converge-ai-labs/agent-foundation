"""Accepted configuration generation persistence and detached catalog queries."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import func, select

from converge_agent_ui.errors import ConfigurationError, StoreIntegrityError
from converge_agent_ui.storage import LocalStore, ObjectKind, ObjectRef, short_session, transaction
from converge_agent_ui.storage.models import (
    ConfigurationDiagnosticRecord,
    ConfigurationGenerationRecord,
    CurrentConfigurationRecord,
    GenerationResourceRecord,
    ResourceRevisionRecord,
    SkillPackageReferenceRecord,
)

from .loader import CatalogCandidate
from .models import (
    ConfigurationGeneration,
    ConfigurationSettings,
    ResourceKind,
    ResourceRevision,
    ResourceRevisionRef,
    canonical_digest,
)


class ConfigurationDiagnostic(BaseModel):
    """Bounded path-free reload evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    diagnostic_id: int = Field(gt=0)
    code: str = Field(min_length=1, max_length=64)
    detail: str = Field(min_length=1, max_length=255)
    recorded_at: datetime


class CatalogRepository:
    """Feature-owned persistence over the shared SQLite/object foundations."""

    def __init__(self, store: LocalStore) -> None:
        self._store = store

    async def current_generation(self) -> ConfigurationGeneration | None:
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            selected = await session.get(CurrentConfigurationRecord, 1)
            if selected is None:
                return None
            record = await session.get(ConfigurationGenerationRecord, selected.generation_id)
            if record is None:
                raise StoreIntegrityError(
                    "The selected configuration generation is missing.",
                    code="configuration_generation_missing",
                )
            rows = tuple(
                (
                    await session.execute(
                        select(GenerationResourceRecord)
                        .where(GenerationResourceRecord.generation_id == record.generation_id)
                        .order_by(GenerationResourceRecord.ordinal)
                    )
                ).scalars()
            )
        try:
            return ConfigurationGeneration(
                generation_id=record.generation_id,
                accepted_at=_as_utc(record.accepted_at),
                process_settings_digest=record.process_settings_digest,
                resources=tuple(
                    ResourceRevisionRef(
                        kind=ResourceKind(row.resource_kind),
                        resource_id=row.resource_id,
                        content_digest=row.content_digest,
                    )
                    for row in rows
                ),
                catalog_digest=record.catalog_digest,
                restart_required=record.restart_required,
            )
        except (TypeError, ValueError) as exc:
            raise StoreIntegrityError(
                "The selected configuration generation index is invalid.",
                code="configuration_generation_invalid",
            ) from exc

    async def generation_settings(self, generation_id: str) -> ConfigurationSettings:
        """Recover the exact accepted process settings associated with one generation."""

        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            record = await session.get(ConfigurationGenerationRecord, generation_id)
        if record is None:
            raise StoreIntegrityError(
                "The selected configuration generation is missing.",
                code="configuration_generation_missing",
            )
        try:
            settings = ConfigurationSettings.model_validate_json(
                record.process_settings_json,
                strict=True,
            )
        except ValueError as exc:
            raise StoreIntegrityError(
                "The accepted process settings are invalid.",
                code="configuration_settings_invalid",
            ) from exc
        if canonical_digest(settings.model_dump(mode="json")) != record.process_settings_digest:
            raise StoreIntegrityError(
                "The accepted process settings do not match their generation digest.",
                code="configuration_settings_mismatch",
            )
        return settings

    async def verify_current(self) -> ConfigurationGeneration | None:
        """Read every selected immutable revision and managed package exactly once."""

        generation = await self.current_generation()
        if generation is None:
            return None
        for reference in generation.resources:
            await self.resource(reference)
            if reference.kind is ResourceKind.skill:
                package = await self.skill_package_reference(reference)
                await self._store.read_object(package)
        return generation

    async def retained_generations(self, *, limit: int = 100) -> tuple[ConfigurationGeneration, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("generation limit must be between 1 and 1000")
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            ids = tuple(
                (
                    await session.execute(
                        select(ConfigurationGenerationRecord.generation_id)
                        .order_by(ConfigurationGenerationRecord.sequence.desc())
                        .limit(limit)
                    )
                ).scalars()
            )
        generations: list[ConfigurationGeneration] = []
        for generation_id in ids:
            generation = await self._generation(generation_id)
            generations.append(generation)
        return tuple(generations)

    async def resource(self, reference: ResourceRevisionRef) -> ResourceRevision:
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            record = (
                await session.execute(
                    select(ResourceRevisionRecord).where(
                        ResourceRevisionRecord.resource_kind == reference.kind.value,
                        ResourceRevisionRecord.resource_id == reference.resource_id,
                        ResourceRevisionRecord.content_digest == reference.content_digest,
                    )
                )
            ).scalar_one_or_none()
        if record is None:
            raise ConfigurationError(
                "The requested resource revision is not retained.",
                code="resource_revision_missing",
            )
        envelope = await self._store.read_object(
            ObjectRef(
                object_kind=ObjectKind.resource_revision,
                object_schema_version="1",
                logical_digest=record.object_digest,
            )
        )
        try:
            revision = ResourceRevision.model_validate(envelope.payload, strict=True)
        except ValueError as exc:
            raise StoreIntegrityError(
                "A selected resource revision object is invalid.",
                code="resource_revision_invalid",
            ) from exc
        if revision.ref != reference:
            raise StoreIntegrityError(
                "A selected resource revision object does not match its index.",
                code="resource_revision_mismatch",
            )
        return revision

    async def skill_package(self, reference: ResourceRevisionRef) -> JsonValue:
        package = await self.skill_package_reference(reference)
        return (await self._store.read_object(package)).payload

    async def skill_package_reference(self, reference: ResourceRevisionRef) -> ObjectRef:
        if reference.kind is not ResourceKind.skill:
            raise ValueError("skill package lookup requires a Skill resource revision")
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            row = (
                await session.execute(
                    select(SkillPackageReferenceRecord).where(
                        SkillPackageReferenceRecord.resource_id == reference.resource_id,
                        SkillPackageReferenceRecord.content_digest == reference.content_digest,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            raise StoreIntegrityError(
                "A selected Skill revision has no retained package object.",
                code="skill_package_missing",
            )
        return ObjectRef(
            object_kind=ObjectKind.skill_package,
            object_schema_version="1",
            logical_digest=row.object_digest,
        )

    async def accept(
        self,
        candidate: CatalogCandidate,
        *,
        activated_restart_settings_digest: str,
    ) -> ConfigurationGeneration:
        """Publish immutable objects, then select one complete generation transactionally."""

        current = await self.current_generation()
        restart_required = candidate.restart_settings_digest != activated_restart_settings_digest
        if (
            current is not None
            and current.catalog_digest == candidate.catalog_digest
            and current.restart_required == restart_required
        ):
            return current

        existing = await self._existing_revisions(candidate)
        revision_objects: dict[tuple[str, str, str], ObjectRef] = {}
        for revision in candidate.revisions:
            key = _revision_key(revision.ref)
            known = existing.get(key)
            if known is not None:
                revision_objects[key] = ObjectRef(
                    object_kind=ObjectKind.resource_revision,
                    object_schema_version="1",
                    logical_digest=known,
                )
                continue
            revision_objects[key] = await self._store.publish_object(
                object_kind=ObjectKind.resource_revision,
                object_schema_version="1",
                payload=revision.model_dump(mode="json"),
            )

        existing_packages = await self._existing_skill_packages(candidate)
        package_objects: dict[tuple[str, str, str], ObjectRef] = {}
        for package in candidate.skill_packages:
            key = _revision_key(package.revision)
            known = existing_packages.get(key)
            if known is not None:
                package_objects[key] = ObjectRef(
                    object_kind=ObjectKind.skill_package,
                    object_schema_version="1",
                    logical_digest=known,
                )
                continue
            package_objects[key] = await self._store.publish_object(
                object_kind=ObjectKind.skill_package,
                object_schema_version="1",
                payload=package.payload,
            )

        accepted_at = datetime.now(UTC)
        async with transaction(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            sequence = (
                int(
                    (
                        await session.execute(
                            select(func.coalesce(func.max(ConfigurationGenerationRecord.sequence), 0))
                        )
                    ).scalar_one()
                )
                + 1
            )
            generation_id = f"config-{sequence}"
            session.add(
                ConfigurationGenerationRecord(
                    generation_id=generation_id,
                    sequence=sequence,
                    accepted_at=accepted_at,
                    process_settings_digest=candidate.settings_digest,
                    process_settings_json=candidate.settings.model_dump_json(),
                    catalog_digest=candidate.catalog_digest,
                    restart_required=restart_required,
                )
            )
            for revision in candidate.revisions:
                key = _revision_key(revision.ref)
                record = (
                    await session.execute(
                        select(ResourceRevisionRecord).where(
                            ResourceRevisionRecord.resource_kind == key[0],
                            ResourceRevisionRecord.resource_id == key[1],
                            ResourceRevisionRecord.content_digest == key[2],
                        )
                    )
                ).scalar_one_or_none()
                if record is None:
                    session.add(
                        ResourceRevisionRecord(
                            resource_kind=key[0],
                            resource_id=key[1],
                            content_digest=key[2],
                            object_digest=revision_objects[key].logical_digest,
                            created_at=accepted_at,
                        )
                    )
            await session.flush()
            for ordinal, revision in enumerate(candidate.revisions):
                key = _revision_key(revision.ref)
                session.add(
                    GenerationResourceRecord(
                        generation_id=generation_id,
                        ordinal=ordinal,
                        resource_kind=key[0],
                        resource_id=key[1],
                        content_digest=key[2],
                    )
                )
            for package in candidate.skill_packages:
                key = _revision_key(package.revision)
                existing_package = (
                    await session.execute(
                        select(SkillPackageReferenceRecord).where(
                            SkillPackageReferenceRecord.resource_kind == key[0],
                            SkillPackageReferenceRecord.resource_id == key[1],
                            SkillPackageReferenceRecord.content_digest == key[2],
                        )
                    )
                ).scalar_one_or_none()
                if existing_package is None:
                    session.add(
                        SkillPackageReferenceRecord(
                            resource_kind=key[0],
                            resource_id=key[1],
                            content_digest=key[2],
                            object_digest=package_objects[key].logical_digest,
                        )
                    )
            selected = await session.get(CurrentConfigurationRecord, 1)
            if selected is None:
                session.add(CurrentConfigurationRecord(singleton_id=1, generation_id=generation_id))
            else:
                selected.generation_id = generation_id

        generation = await self.current_generation()
        if generation is None or generation.catalog_digest != candidate.catalog_digest:
            raise StoreIntegrityError(
                "The accepted configuration generation was not selected.",
                code="configuration_generation_selection_failed",
            )
        return generation

    async def record_rejection(self, error: ConfigurationError) -> None:
        detail = str(error).strip()[:255] or "Configuration candidate rejected."
        async with transaction(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            session.add(
                ConfigurationDiagnosticRecord(
                    process_generation=self._store.process_generation,
                    code=error.code[:64],
                    detail=detail,
                    recorded_at=datetime.now(UTC),
                )
            )

    async def diagnostics(self, *, limit: int = 100) -> tuple[ConfigurationDiagnostic, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("diagnostic limit must be between 1 and 1000")
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            rows = tuple(
                (
                    await session.execute(
                        select(ConfigurationDiagnosticRecord)
                        .order_by(ConfigurationDiagnosticRecord.diagnostic_id.desc())
                        .limit(limit)
                    )
                ).scalars()
            )
        return tuple(
            ConfigurationDiagnostic(
                diagnostic_id=row.diagnostic_id,
                code=row.code,
                detail=row.detail,
                recorded_at=row.recorded_at,
            )
            for row in rows
        )

    async def _generation(self, generation_id: str) -> ConfigurationGeneration:
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            record = await session.get(ConfigurationGenerationRecord, generation_id)
            if record is None:
                raise StoreIntegrityError(
                    "A retained configuration generation is missing.",
                    code="configuration_generation_missing",
                )
            rows = tuple(
                (
                    await session.execute(
                        select(GenerationResourceRecord)
                        .where(GenerationResourceRecord.generation_id == generation_id)
                        .order_by(GenerationResourceRecord.ordinal)
                    )
                ).scalars()
            )
        return ConfigurationGeneration(
            generation_id=record.generation_id,
            accepted_at=_as_utc(record.accepted_at),
            process_settings_digest=record.process_settings_digest,
            resources=tuple(
                ResourceRevisionRef(
                    kind=ResourceKind(row.resource_kind),
                    resource_id=row.resource_id,
                    content_digest=row.content_digest,
                )
                for row in rows
            ),
            catalog_digest=record.catalog_digest,
            restart_required=record.restart_required,
        )

    async def _existing_revisions(self, candidate: CatalogCandidate) -> dict[tuple[str, str, str], str]:
        keys = {_revision_key(item.ref) for item in candidate.revisions}
        if not keys:
            return {}
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            rows = tuple((await session.execute(select(ResourceRevisionRecord))).scalars())
        return {
            (row.resource_kind, row.resource_id, row.content_digest): row.object_digest
            for row in rows
            if (row.resource_kind, row.resource_id, row.content_digest) in keys
        }

    async def _existing_skill_packages(self, candidate: CatalogCandidate) -> dict[tuple[str, str, str], str]:
        keys = {_revision_key(item.revision) for item in candidate.skill_packages}
        if not keys:
            return {}
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            rows = tuple((await session.execute(select(SkillPackageReferenceRecord))).scalars())
        return {
            (row.resource_kind, row.resource_id, row.content_digest): row.object_digest
            for row in rows
            if (row.resource_kind, row.resource_id, row.content_digest) in keys
        }


def _revision_key(reference: ResourceRevisionRef) -> tuple[str, str, str]:
    return reference.kind.value, reference.resource_id, reference.content_digest


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["CatalogRepository", "ConfigurationDiagnostic"]
