"""Durable Vercel snapshot preparation, isolated by account, credentials and frozen manifest."""

import hashlib
import json
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import anyio
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.vercel.shared import VercelConnectionConfiguration
from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, advisory_lock, lock, now, transaction
from a13n_service.providers.environment.vercel_build import BuildTarget, VercelDesktopPreparation, VercelSnapshotAPI
from a13n_service.resources.providers.service import ResolvedProvider
from a13n_service.runs.environments.adapters import credential_version
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime

CLAIM_SECONDS = 100


class VercelDesktopSnapshotRow(Stamped, Base):
    __tablename__ = "vercel_desktop_snapshots"
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
    builder: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    snapshot_id: Mapped[str | None] = mapped_column(String(128))
    lease: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _capability(runtime: Runtime) -> VercelDesktopPreparation:
    capability = runtime.registry.get("environment", "vercel").vercel_desktop_preparation
    if capability is None:
        raise failure("vercel", "desktop_build_unsupported", Category.UNSUPPORTED)
    return capability


def _builder(runtime: Runtime, provider: ResolvedProvider) -> VercelSnapshotAPI:
    return _capability(runtime).connect(
        VercelConnectionConfiguration.model_validate(provider.config),
        TokenCredential.model_validate(provider.reveal_credential(runtime.keys)),
    )


def _backend(runtime: Runtime, provider: ResolvedProvider) -> str:
    return VercelConnectionConfiguration.model_validate(provider.config).model_dump_json()


async def bind(runtime: Runtime, session: AsyncSession, environment: EnvironmentRow, provider: ResolvedProvider) -> str:
    version = credential_version(provider)
    if version is None:
        raise failure("vercel", "desktop_build_credential_missing", Category.INVALID)
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
    await advisory_lock(session, "vercel-desktop-snapshot", scope)
    row = await session.scalar(select(VercelDesktopSnapshotRow).where(VercelDesktopSnapshotRow.scope == scope))
    if row is not None and row.phase == "failed":
        row.scope = hashlib.sha256((scope + row.id).encode()).hexdigest()
        await session.flush()
        row = None
    if row is None:
        row = VercelDesktopSnapshotRow(
            id="a13n-desktop-" + secrets.token_hex(16),
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
    phase: str
    expired: bool
    manifest: dict[str, JsonValue]
    snapshot_id: str | None
    builder: BuildTarget | None


async def _claim(runtime: Runtime, key: str, provider: ResolvedProvider) -> Claim | None:
    async with transaction(runtime.storage) as session:
        row = await lock(session, VercelDesktopSnapshotRow, key)
        if row is None or row.provider_id != provider.id or row.credential_version != credential_version(provider):
            raise failure("vercel", "desktop_build_credential_changed", Category.CONFLICT)
        if row.backend != _backend(runtime, provider):
            raise failure("vercel", "desktop_build_account_changed", Category.CONFLICT)
        current = await now(session)
        if row.lease_expires_at is not None and row.lease_expires_at > current:
            return None
        if row.phase == "failed":
            raise failure("vercel", "desktop_snapshot_build_failed", Category.CONFLICT)
        if row.phase == "new" and current >= row.deadline:
            row.phase = "failed"
            return None
        lease = secrets.token_hex(24)
        row.lease, row.lease_expires_at = (
            lease,
            current + timedelta(seconds=max(CLAIM_SECONDS, runtime.settings.environments.operation_seconds + 10)),
        )
        phase = row.phase
        if phase == "new":
            row.phase = "allocating"
        return Claim(
            row.id,
            lease,
            phase,
            current >= row.deadline,
            row.manifest,
            row.snapshot_id,
            BuildTarget.model_validate(row.builder) if row.builder else None,
        )


async def _save(runtime: Runtime, claim: Claim, phase: str, *, release: bool = True) -> bool:
    async with transaction(runtime.storage) as session:
        row = await lock(session, VercelDesktopSnapshotRow, claim.id)
        if row is None or row.lease != claim.lease:
            return False
        row.phase, row.snapshot_id = phase, claim.snapshot_id
        row.builder = claim.builder.model_dump(mode="json") if claim.builder else None
        if release:
            row.lease = row.lease_expires_at = None
        return True


async def prepare(runtime: Runtime, key: str, provider: ResolvedProvider, *, cancel: bool = False) -> str | None:
    """One bounded build step, with durable allocation/snapshot intent before external mutations."""
    await runtime.registry.check_environment_endpoint("vercel", provider.config, runtime.endpoint_policy)
    claim = await _claim(runtime, key, provider)
    if claim is None:
        await anyio.sleep(1)
        return None
    api = _builder(runtime, provider)
    try:
        if claim.phase == "new":
            if cancel:
                await _save(runtime, claim, "failed")
                return None
            target = await api.create(key, claim.manifest)
            claim = replace(claim, builder=target)
            await _save(runtime, claim, "installing")
            return None
        if claim.phase == "allocating":
            target = await api.find_builder(key, claim.manifest, None)
            if target is None:
                # Absence after a lost create response never authorizes a second allocation.
                await _save(runtime, claim, "allocating")
                if claim.expired:
                    raise failure("vercel", "desktop_builder_submission_unresolved", Category.CONFLICT)
                return None
            claim = replace(claim, builder=target)
            await _save(runtime, claim, "cleanup" if cancel or claim.expired else "installing")
            return None
        assert claim.builder is not None
        if claim.phase == "ready":
            snapshot = await api.find_snapshot(key, claim.builder, claim.snapshot_id)
            if snapshot is None:
                await _save(runtime, claim, "failed")
                raise failure("vercel", "desktop_snapshot_missing", Category.MISSING)
            if snapshot.state != "created":
                await _save(runtime, claim, "cleanup")
                return None
            if await _save(runtime, claim, "ready"):
                return snapshot.id
            return None
        if claim.phase == "installing":
            if cancel or claim.expired:
                await _save(runtime, claim, "cleanup")
                return None
            status = await api.install(key, claim.manifest, claim.builder)
            if status == "failed":
                await _save(runtime, claim, "cleanup")
                return None
            if status != "ready":
                await _save(runtime, claim, "installing")
                await anyio.sleep(1)
                return None
            if not await _save(runtime, claim, "snapshotting", release=False):
                return None
            # Any interruption from here is reconciled by source session, never resubmitted.
            claim = replace(claim, phase="snapshotting")
            assert claim.builder is not None
            snapshot = await api.snapshot(key, claim.manifest, claim.builder)
            claim = replace(claim, snapshot_id=snapshot.id)
            await _save(runtime, claim, "cleanup_ready" if snapshot.state == "created" else "cleanup")
            return None
        if claim.phase == "snapshotting":
            snapshot = await api.find_snapshot(key, claim.builder, claim.snapshot_id)
            if snapshot is None:
                await _save(runtime, claim, "snapshotting")
                if claim.expired:
                    raise failure("vercel", "desktop_snapshot_submission_unresolved", Category.CONFLICT)
                return None
            claim = replace(claim, snapshot_id=snapshot.id)
            await _save(runtime, claim, "cleanup_ready" if snapshot.state == "created" and not cancel else "cleanup")
            return None
        if claim.phase in {"cleanup", "cleanup_ready"}:
            if claim.phase == "cleanup" and claim.snapshot_id:
                await api.delete_snapshot(key, claim.builder, claim.snapshot_id)
            await api.delete_builder(key, claim.manifest, claim.builder)
            success = claim.phase == "cleanup_ready"
            await _save(runtime, claim, "ready" if success else "failed")
            if not success and not cancel:
                raise failure("vercel", "desktop_snapshot_build_failed", Category.CONFLICT)
            return None
        raise failure("vercel", "desktop_build_state_invalid", Category.CONFLICT)
    except EnvironmentProviderError as error:
        if claim.phase == "new" and error.certainty == Certainty.KNOWN:
            if error.category == Category.UNAVAILABLE:
                await _save(runtime, claim, "new")
            elif error.category in {Category.INVALID, Category.DENIED, Category.MISSING}:
                await _save(runtime, claim, "failed")
        # Post-allocation errors retain the identity for reconciliation/cleanup on the next claim.
        raise
    finally:
        with anyio.CancelScope(shield=True), anyio.move_on_after(5):
            await api.close()


async def cancel(runtime: Runtime, key: str, provider: ResolvedProvider, environment_id: str) -> bool:
    """Release an abandoned builder only when no other creating instance depends on it."""
    async with transaction(runtime.storage) as session:
        row = await lock(session, VercelDesktopSnapshotRow, key)
        if row is None or row.phase in {"ready", "failed"}:
            return True
        others = await session.scalar(
            select(EnvironmentRow.id)
            .where(
                EnvironmentRow.id != environment_id,
                EnvironmentRow.workspace_id == row.workspace_id,
                EnvironmentRow.status == "creating",
                EnvironmentRow.handle["vercel_desktop_snapshot"].astext == key,
            )
            .limit(1)
        )
        if others is not None:
            return True
    await prepare(runtime, key, provider, cancel=True)
    return False
