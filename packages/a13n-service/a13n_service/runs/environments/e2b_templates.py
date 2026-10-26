"""Durable, credential-scoped E2B desktop preparation; no sandbox is built from a mutable alias."""

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.e2b.configuration import E2BCredential
from a13n_harness.providers.environment.native.errors import failure
from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, advisory_lock, lock, now, transaction
from a13n_service.providers.environment.e2b_build import Build, E2BDesktopPreparation, E2BTemplateAPI
from a13n_service.resources.providers.service import ResolvedProvider
from a13n_service.runs.environments.adapters import credential_version
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime


class E2BDesktopTemplateRow(Stamped, Base):
    __tablename__ = "e2b_desktop_templates"
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
    domain: Mapped[str] = mapped_column(String(253))
    manifest: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    phase: Mapped[str] = mapped_column(String(32))
    template_id: Mapped[str | None] = mapped_column(String(128))
    build_id: Mapped[str | None] = mapped_column(String(128))
    lease: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _capability(runtime: Runtime) -> E2BDesktopPreparation:
    capability = runtime.registry.get("environment", "e2b").e2b_desktop_preparation
    if capability is None:
        raise failure("e2b", "desktop_build_unsupported", Category.UNSUPPORTED)
    return capability


def _builder(runtime: Runtime, domain: str, token: str) -> E2BTemplateAPI:
    return _capability(runtime).connect(domain, token)


def _revision(runtime: Runtime) -> str:
    return _capability(runtime).revision()


async def bind(runtime: Runtime, session: AsyncSession, environment: EnvironmentRow, provider: ResolvedProvider) -> str:
    """Pin the preparation before any network call, in the environment's first-claim transaction."""
    version = credential_version(provider)
    if version is None:
        raise failure("e2b", "desktop_build_credential_missing", Category.INVALID)
    domain = str(provider.config.get("domain", "e2b.dev"))
    scope = hashlib.sha256(
        json.dumps([environment.workspace_id, provider.id, domain, version, _revision(runtime)]).encode()
    ).hexdigest()
    await advisory_lock(session, "e2b-desktop-template", scope)
    row = await session.scalar(select(E2BDesktopTemplateRow).where(E2BDesktopTemplateRow.scope == scope))
    if row is not None and row.phase == "failed":
        # A new environment may retry a definitively failed preparation. Existing references retain the failure.
        row.scope = hashlib.sha256((scope + row.id).encode()).hexdigest()
        await session.flush()
        row = None
    if row is None:
        row = E2BDesktopTemplateRow(
            id="a13n-desktop-" + secrets.token_hex(16),
            workspace_id=environment.workspace_id,
            provider_id=provider.id,
            scope=scope,
            credential_version=version,
            domain=domain,
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
    domain: str
    manifest: dict[str, JsonValue]
    build: Build | None
    expired: bool


async def _claim(runtime: Runtime, key: str, provider: ResolvedProvider) -> Claim | None:
    async with transaction(runtime.storage) as session:
        row = await lock(session, E2BDesktopTemplateRow, key)
        if row is None or row.provider_id != provider.id or row.credential_version != credential_version(provider):
            raise failure("e2b", "desktop_build_credential_changed", Category.CONFLICT)
        if row.domain != str(provider.config.get("domain", "e2b.dev")):
            raise failure("e2b", "desktop_build_account_changed", Category.CONFLICT)
        current = await now(session)
        if row.lease_expires_at is not None and row.lease_expires_at > current:
            return None
        if row.phase == "failed":
            raise failure("e2b", "desktop_template_build_failed", Category.CONFLICT)
        if row.phase == "new" and current >= row.deadline:
            row.phase = "failed"
            # Commit the failure; report it on the next dispatch without allocating after the deadline.
            return None
        phase = row.phase
        # Write intent before dispatch. An expired owner reconciles, never repeats reserve/submit.
        row.phase = {"new": "reserving", "reserved": "submitting"}.get(phase, phase)
        lease = secrets.token_hex(24)
        row.lease = lease
        row.lease_expires_at = current + timedelta(seconds=100)
        build = None
        if row.template_id is not None and row.build_id is not None:
            build = Build(templateID=row.template_id, buildID=row.build_id)
        return Claim(row.id, lease, phase, row.domain, row.manifest, build, current >= row.deadline)


async def _save(runtime: Runtime, claim: Claim, phase: str, build: Build | None) -> bool:
    async with transaction(runtime.storage) as session:
        row = await lock(session, E2BDesktopTemplateRow, claim.id)
        if row is None or row.lease != claim.lease:
            return False
        row.phase = phase
        if build is not None:
            row.template_id, row.build_id = build.template_id, build.build_id
        row.lease = row.lease_expires_at = None
        return True


async def prepare(runtime: Runtime, key: str, provider: ResolvedProvider) -> str | None:
    """Advance one bounded build phase. None means preparing; maintenance will poll again.

    Successful templates are intentional reusable account assets. Failed builds are deleted only after
    private alias/build identity checks. Ambiguous reserve/submit outcomes retain their durable identity.
    """
    await runtime.registry.check_environment_endpoint("e2b", provider.config, runtime.endpoint_policy)
    claim = await _claim(runtime, key, provider)
    if claim is None:
        # Agent readiness can immediately redispatch pending work; do not spin against another build owner.
        await anyio.sleep(1)
        return None
    credential = E2BCredential.model_validate(provider.reveal_credential(runtime.keys))
    api = _builder(runtime, claim.domain, credential.api_key.get_secret_value())
    phase, build = claim.phase, claim.build
    try:
        if phase == "new":
            build = await api.reserve(key)
            await _save(runtime, claim, "reserved", build)
        elif phase == "reserving":
            template = await api.find(key)
            if template is not None:
                build = Build(templateID=template.template_id, buildID=template.builds[0].build_id)
                await _save(runtime, claim, "reserved", build)
            else:
                # Absence immediately after a timeout does not prove the request had no effect.
                await _save(runtime, claim, "reserving", None)
                if claim.expired:
                    raise failure("e2b", "desktop_template_reservation_unresolved", Category.CONFLICT)
        elif phase == "reserved":
            assert build is not None
            if claim.expired:
                await _save(runtime, claim, "cleanup", build)
            else:
                await api.submit(build, claim.manifest)
                await _save(runtime, claim, "building", build)
        elif phase in {"submitting", "building", "ready", "cleanup"}:
            assert build is not None
            template = await api.inspect(build.template_id, key)
            if template is None:
                await _save(runtime, claim, "failed", build)
                raise failure("e2b", "desktop_template_missing", Category.MISSING)
            if template.builds[0].build_id != build.build_id:
                raise failure("e2b", "desktop_template_build_changed", Category.CONFLICT)
            status = await api.status(build)
            if status.status == "ready" and phase != "cleanup":
                if await _save(runtime, claim, "ready", build):
                    return build.template_id
            elif status.status == "error" or claim.expired or phase == "cleanup":
                # Keep cleanup intent if deletion times out. No secret or arbitrary upstream logs are stored.
                await _save(runtime, claim, "cleanup", build)
                # Reclaim rather than deleting after releasing the lease to another worker.
                cleanup = await _claim(runtime, key, provider)
                if cleanup is not None:
                    await api.delete(build.template_id, key)
                    await _save(runtime, cleanup, "failed", build)
                raise failure("e2b", "desktop_template_build_failed", Category.CONFLICT)
            else:
                await _save(runtime, claim, phase, build)
        return None
    except EnvironmentProviderError as error:
        if error.certainty == Certainty.KNOWN and phase in {"new", "reserved"}:
            # A definite rejection must not later adopt an alias that already belonged to somebody else.
            next_phase = "failed" if phase == "new" else "cleanup"
            if error.category == Category.UNAVAILABLE:
                next_phase = phase
            await _save(runtime, claim, next_phase, build)
        raise
    finally:
        with anyio.CancelScope(shield=True), anyio.move_on_after(5):
            await api.close()
