"""Versioned Bot configuration owned by the application, not installation identity."""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.database.metadata import Base
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_workspace
from a13n_service.iam.audit import security_audit_record
from a13n_service.ids import new_object_id
from a13n_service.memory.resources import require_document_support, require_provider
from a13n_service.memory.service import MemoryService, failure
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now

from .domain import MemorySettings


class AccountSettingsRecord(Base):
    __tablename__ = "bot_account_settings"
    __table_args__ = (CheckConstraint("version >= 1", name="version_positive"),)
    account_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("application_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer)
    provider_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("memory_providers.id", ondelete="RESTRICT"))
    use_memory: Mapped[bool] = mapped_column(Boolean)
    save_on_request: Mapped[bool] = mapped_column(Boolean)
    auto_organize: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    timezone: Mapped[str] = mapped_column(String(128))

    def memory(self) -> MemorySettings | None:
        return (
            None
            if self.provider_id is None
            else MemorySettings(
                provider_id=self.provider_id,
                use_memory=self.use_memory,
                save_on_request=self.save_on_request,
                auto_organize=self.auto_organize,
                timezone=self.timezone,
            )
        )


class AccountMemorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str
    version: int = Field(ge=0)
    memory: MemorySettings | None


class ReplaceMemorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_version: int = Field(ge=0)
    memory: MemorySettings | None


async def read_settings(session: AsyncSession, account_id: str) -> AccountMemorySettings:
    row = await session.get(AccountSettingsRecord, account_id)
    return AccountMemorySettings(
        account_id=account_id, version=0 if row is None else row.version, memory=None if row is None else row.memory()
    )


async def settings_version(session: AsyncSession, account_id: str) -> int:
    return (await read_settings(session, account_id)).version


async def _account(session: AsyncSession, actor: AuthenticatedActor, account_id: str, *, write: bool) -> AccountRecord:
    query = select(AccountRecord).where(AccountRecord.id == account_id)
    account = await session.scalar(query.with_for_update() if write else query)
    if account is None or account.deleted_at is not None or account.provider_key not in {"slack", "lark"}:
        raise failure("account_not_found", "Bot installation not found.", ErrorCategory.not_found)
    await authorize_workspace(
        session,
        actor=actor,
        workspace_id=account.workspace_id,
        action=WorkspaceAction.bot_memory_share if write else WorkspaceAction.bot_memory_read,
    )
    return account


async def get_settings(service: MemoryService, actor: AuthenticatedActor, account_id: str) -> AccountMemorySettings:
    async with short_session(service.authorizer.sessions) as session:
        await _account(session, actor, account_id, write=False)
        return await read_settings(session, account_id)


async def replace_settings(
    service: MemoryService, actor: AuthenticatedActor, account_id: str, body: ReplaceMemorySettings
) -> AccountMemorySettings:
    async with transaction(service.authorizer.sessions) as session:
        account = await _account(session, actor, account_id, write=True)
        previous = await read_settings(session, account_id)
        if previous.version != body.expected_version:
            raise failure(
                "version_conflict", "Memory settings changed. Reload before saving.", ErrorCategory.stale_version
            )
        selected = body.memory
        if selected is not None:
            await authorize_workspace(
                session, actor=actor, workspace_id=account.workspace_id, action=WorkspaceAction.memory_provider_read
            )
            provider = await require_provider(
                session,
                organization_id=account.organization_id,
                workspace_id=account.workspace_id,
                provider_id=selected.provider_id,
                eligible=True,
                catalog=service.catalog,
            )
            require_document_support(provider.type, service.catalog)
            if selected.auto_organize and (
                provider.type != "filesystem" or not selected.use_memory or not selected.save_on_request
            ):
                raise failure(
                    "memory_organization_unsupported",
                    "Automatic organization requires File-based storage with reading and saving enabled.",
                    ErrorCategory.invalid_request,
                )
        row = await session.get(AccountSettingsRecord, account_id)
        if row is None:
            row = AccountSettingsRecord(account_id=account_id)
            session.add(row)
        row.version = previous.version + 1
        row.provider_id = selected.provider_id if selected else None
        row.use_memory = selected.use_memory if selected else False
        row.save_on_request = selected.save_on_request if selected else False
        row.timezone = selected.timezone if selected else "UTC"
        row.auto_organize = selected.auto_organize if selected else False
        session.add(
            security_audit_record(
                audit_id=new_object_id("aud"),
                actor=actor,
                organization_id=account.organization_id,
                workspace_id=account.workspace_id,
                action="bot_memory.settings_replace",
                resource_type="bot_memory",
                resource_id=account_id,
                outcome="success",
                occurred_at=utc_now(),
                details={"version": row.version},
            )
        )
        await session.flush()
        return await read_settings(session, account_id)
