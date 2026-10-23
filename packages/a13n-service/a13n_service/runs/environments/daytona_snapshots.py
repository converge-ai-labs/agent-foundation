"""Durable Daytona snapshot preparation, isolated by account, credentials and frozen manifest."""

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.daytona.shared import DaytonaConnectionConfiguration
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.errors import failure
from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, advisory_lock, lock, now, transaction
from a13n_service.providers.environment.daytona_build import DaytonaDesktopPreparation, DaytonaSnapshotAPI
from a13n_service.resources.providers.service import ResolvedProvider
from a13n_service.runs.environments.adapters import credential_version
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime

CLAIM_SECONDS = 100


class DaytonaDesktopSnapshotRow(Stamped, Base):
    __tablename__ = "daytona_desktop_snapshots"
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
    snapshot_id: Mapped[str | None] = mapped_column(String(128))
    lease: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _capability(runtime: Runtime) -> DaytonaDesktopPreparation:
    capability = runtime.registry.get("environment", "daytona").daytona_desktop_preparation
    if capability is None:
        raise failure("daytona", "desktop_build_unsupported", Category.UNSUPPORTED)
    return capability


def _builder(runtime: Runtime, provider: ResolvedProvider) -> DaytonaSnapshotAPI:
    return _capability(runtime).connect(
        DaytonaConnectionConfiguration.model_validate(provider.config),
        TokenCredential.model_validate(provider.reveal_credential(runtime.keys)),
    )


def _backend(runtime: Runtime, provider: ResolvedProvider) -> str:
    return DaytonaConnectionConfiguration.model_validate(provider.config).model_dump_json()


async def bind(runtime: Runtime, session: AsyncSession, environment: EnvironmentRow, provider: ResolvedProvider) -> str:
    version = credential_version(provider)
    if version is None:
        raise failure("daytona", "desktop_build_credential_missing", Category.INVALID)
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
    await advisory_lock(session, "daytona-desktop-snapshot", scope)
    row = await session.scalar(select(DaytonaDesktopSnapshotRow).where(DaytonaDesktopSnapshotRow.scope == scope))
    if row is not None and row.phase == "failed":
        row.scope = hashlib.sha256((scope + row.id).encode()).hexdigest()
        await session.flush()
        row = None
    if row is None:
        row = DaytonaDesktopSnapshotRow(
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


async def _claim(runtime: Runtime, key: str, provider: ResolvedProvider) -> Claim | None:
    async with transaction(runtime.storage) as session:
        row = await lock(session, DaytonaDesktopSnapshotRow, key)
        if row is None or row.provider_id != provider.id or row.credential_version != credential_version(provider):
            raise failure("daytona", "desktop_build_credential_changed", Category.CONFLICT)
        if row.backend != _backend(runtime, provider):
            raise failure("daytona", "desktop_build_account_changed", Category.CONFLICT)
        current = await now(session)
        if row.lease_expires_at is not None and row.lease_expires_at > current:
            return None
        if row.phase == "failed":
            raise failure("daytona", "desktop_snapshot_build_failed", Category.CONFLICT)
        if row.phase == "new" and current >= row.deadline:
            row.phase = "failed"
            return None
        lease = secrets.token_hex(24)
        row.lease, row.lease_expires_at = lease, current + timedelta(seconds=CLAIM_SECONDS)
        phase = row.phase
        if phase == "new":
            row.phase = "submitting"
        return Claim(row.id, lease, phase, current >= row.deadline, row.manifest, row.snapshot_id)


async def _save(runtime: Runtime, claim: Claim, phase: str, snapshot_id: str | None) -> bool:
    async with transaction(runtime.storage) as session:
        row = await lock(session, DaytonaDesktopSnapshotRow, claim.id)
        if row is None or row.lease != claim.lease:
            return False
        row.phase, row.snapshot_id = phase, snapshot_id
        row.lease = row.lease_expires_at = None
        return True


async def prepare(runtime: Runtime, key: str, provider: ResolvedProvider) -> str | None:
    """Submit once, reconcile ambiguous outcomes, and delete only validated failed builds."""
    await runtime.registry.check_environment_endpoint("daytona", provider.config, runtime.endpoint_policy)
    claim = await _claim(runtime, key, provider)
    if claim is None:
        await anyio.sleep(1)
        return None
    api = _builder(runtime, provider)
    try:
        if claim.phase == "new":
            snapshot = await api.create(key, claim.manifest)
            await _save(runtime, claim, "building", snapshot.id)
            return None
        snapshot = await api.find(key, claim.manifest, claim.snapshot_id)
        if snapshot is None:
            if claim.phase == "submitting":
                # A timeout followed by absence is not proof that POST had no effect. Never resubmit.
                await _save(runtime, claim, "submitting", None)
                if claim.expired:
                    raise failure("daytona", "desktop_snapshot_submission_unresolved", Category.CONFLICT)
                await anyio.sleep(1)
                return None
            await _save(runtime, claim, "failed", claim.snapshot_id)
            if claim.phase == "cleanup":
                return None
            raise failure("daytona", "desktop_snapshot_missing", Category.MISSING)
        if (
            claim.phase == "cleanup"
            or snapshot.state in {"error", "build_failed"}
            or (claim.expired and claim.phase != "ready")
        ):
            if claim.phase != "cleanup":
                await _save(runtime, claim, "cleanup", snapshot.id)
                return None
            await api.delete(key, claim.manifest, snapshot.id)
            await _save(runtime, claim, "failed", snapshot.id)
            raise failure("daytona", "desktop_snapshot_build_failed", Category.CONFLICT)
        if snapshot.state == "active":
            if await _save(runtime, claim, "ready", snapshot.id):
                return snapshot.id
        elif snapshot.state in {"pending", "building", "pulling", "snapshotting"}:
            await _save(runtime, claim, "building", snapshot.id)
            await anyio.sleep(1)
        else:
            # An inactive or externally changed target needs explicit attention, not silent repair.
            await _save(runtime, claim, "failed", snapshot.id)
            raise failure("daytona", "desktop_snapshot_unavailable", Category.CONFLICT)
        return None
    except EnvironmentProviderError as error:
        if claim.phase == "new" and error.certainty == Certainty.KNOWN and error.category == Category.UNAVAILABLE:
            await _save(runtime, claim, "new", None)
        if claim.phase == "new" and error.category in {
            Category.INVALID,
            Category.DENIED,
            Category.MISSING,
            Category.CONFLICT,
        }:
            await _save(runtime, claim, "failed", None)
        raise
    finally:
        with anyio.CancelScope(shield=True), anyio.move_on_after(5):
            await api.close()
