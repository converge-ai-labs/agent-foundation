"""Durable Modal image preparation, isolated by account, credentials and frozen manifest."""

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration, ModalCredential
from a13n_harness.providers.environment.native.errors import failure
from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, advisory_lock, lock, now, transaction
from a13n_service.providers.environment.modal_build import ModalDesktopPreparation, ModalImageAPI
from a13n_service.resources.providers.service import ResolvedProvider
from a13n_service.runs.environments.adapters import credential_version
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime

BUILD_SECONDS = 85


class ModalDesktopImageRow(Stamped, Base):
    __tablename__ = "modal_desktop_images"
    __table_args__ = (
        UniqueConstraint("scope"),
        ForeignKeyConstraint(
            ["workspace_id", "provider_id"], ["environment_providers.workspace_id", "environment_providers.id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(72))
    provider_id: Mapped[str] = mapped_column(String(72))
    scope: Mapped[str] = mapped_column(String(64))
    credential_version: Mapped[str] = mapped_column(String(256))
    backend: Mapped[str] = mapped_column(String(1024))
    manifest: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    phase: Mapped[str] = mapped_column(String(32))
    image_id: Mapped[str | None] = mapped_column(String(128))
    lease: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _capability(runtime: Runtime) -> ModalDesktopPreparation:
    capability = runtime.registry.get("environment", "modal").modal_desktop_preparation
    if capability is None:
        raise failure("modal", "desktop_build_unsupported", Category.UNSUPPORTED)
    return capability


def _builder(runtime: Runtime, provider: ResolvedProvider) -> ModalImageAPI:
    return _capability(runtime).connect(
        ModalConnectionConfiguration.model_validate(provider.config),
        ModalCredential.model_validate(provider.reveal_credential(runtime.keys)),
    )


def _backend(runtime: Runtime, provider: ResolvedProvider) -> str:
    return _capability(runtime).locator(ModalConnectionConfiguration.model_validate(provider.config))


async def bind(runtime: Runtime, session: AsyncSession, environment: EnvironmentRow, provider: ResolvedProvider) -> str:
    version = credential_version(provider)
    if version is None:
        raise failure("modal", "desktop_build_credential_missing", Category.INVALID)
    backend = _backend(runtime, provider)
    scope = hashlib.sha256(
        json.dumps(
            [
                environment.workspace_id,
                provider.id,
                backend,
                version,
                _capability(runtime).revision(),
            ]
        ).encode()
    ).hexdigest()
    await advisory_lock(session, "modal-desktop-image", scope)
    row = await session.scalar(select(ModalDesktopImageRow).where(ModalDesktopImageRow.scope == scope))
    if row is not None and row.phase == "failed":
        row.scope = hashlib.sha256((scope + row.id).encode()).hexdigest()
        await session.flush()
        row = None
    if row is None:
        row = ModalDesktopImageRow(
            id="mdi_" + secrets.token_hex(16),
            workspace_id=environment.workspace_id,
            provider_id=provider.id,
            scope=scope,
            credential_version=version,
            backend=backend,
            manifest=_capability(runtime).specification(),
            phase="new",
            deadline=await now(session) + timedelta(minutes=20),
        )
        session.add(row)
        await session.flush()
    return row.id


@dataclass(frozen=True)
class Claim:
    id: str
    lease: str
    scope: str
    manifest: dict[str, JsonValue]
    image_id: str | None


async def _claim(runtime: Runtime, key: str, provider: ResolvedProvider) -> Claim | None:
    async with transaction(runtime.storage) as session:
        row = await lock(session, ModalDesktopImageRow, key)
        if row is None or row.provider_id != provider.id or row.credential_version != credential_version(provider):
            raise failure("modal", "desktop_build_credential_changed", Category.CONFLICT)
        if row.backend != _backend(runtime, provider):
            raise failure("modal", "desktop_build_account_changed", Category.CONFLICT)
        current = await now(session)
        if row.lease_expires_at is not None and row.lease_expires_at > current:
            return None
        if row.phase == "failed":
            raise failure("modal", "desktop_image_build_failed", Category.CONFLICT)
        if row.phase != "ready" and current >= row.deadline:
            row.phase = "failed"
            return None
        lease = secrets.token_hex(24)
        row.lease, row.lease_expires_at = lease, current + timedelta(seconds=BUILD_SECONDS + 15)
        if row.phase != "ready":
            row.phase = "building"
        return Claim(row.id, lease, row.scope, row.manifest, row.image_id)


async def _save(runtime: Runtime, claim: Claim, phase: str, image_id: str | None) -> bool:
    async with transaction(runtime.storage) as session:
        row = await lock(session, ModalDesktopImageRow, claim.id)
        if row is None or row.lease != claim.lease:
            return False
        row.phase, row.image_id = phase, image_id
        row.lease = row.lease_expires_at = None
        return True


async def prepare(runtime: Runtime, key: str, provider: ResolvedProvider) -> str | None:
    await runtime.registry.check_environment_endpoint("modal", provider.config, runtime.endpoint_policy)
    claim = await _claim(runtime, key, provider)
    if claim is None:
        await anyio.sleep(1)
        return None
    api = _builder(runtime, provider)
    try:
        image_id = None
        # ImageGetOrCreate is content-addressed: cancellation/restart rejoins identical layers.
        # Never use force_build, mutable aliases, credential-bearing layers or an ephemeral sandbox.
        with anyio.move_on_after(BUILD_SECONDS):
            image_id = await api.build(claim.manifest, claim.scope, claim.image_id)
        if image_id is None:
            await _save(runtime, claim, "ready" if claim.image_id else "building", claim.image_id)
            return None
        if await _save(runtime, claim, "ready", image_id):
            return image_id
        return None
    except EnvironmentProviderError as error:
        definitive = error.category in {Category.INVALID, Category.DENIED, Category.MISSING, Category.CONFLICT}
        await _save(
            runtime, claim, "failed" if definitive else "ready" if claim.image_id else "building", claim.image_id
        )
        raise
    finally:
        # Native content-addressed layers and the reusable App are deliberate account assets, not ours to delete.
        # Existing sandbox ownership/cleanup rules take over only after an immutable image has been pinned.
        with anyio.CancelScope(shield=True), anyio.move_on_after(5):
            await api.close()
