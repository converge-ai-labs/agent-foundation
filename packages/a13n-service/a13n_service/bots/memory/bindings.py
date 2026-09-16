"""Immutable application bindings retained alongside canonical accepted Runs."""

from pydantic import ValidationError
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.database.metadata import Base
from a13n_service.interactions.errors import RunAcceptanceError
from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.memory.behaviors import RunMemorySelectionRecord
from a13n_service.memory.models import MemoryProviderRecord

from .binding import BotMemoryBinding
from .models import ScopeRecord

BEHAVIOR_KEY = "bot_conversation"
SCHEMA_VERSION = 1


class RunMemoryBindingRecord(Base):
    __tablename__ = "bot_run_memory_bindings"
    __table_args__ = (
        CheckConstraint(
            "NOT (use_memory OR save_on_request) OR "
            "(provider_id IS NOT NULL AND scope_id IS NOT NULL AND scope_version IS NOT NULL AND scope_version >= 1)",
            name="enabled_complete",
        ),
    )
    run_id: Mapped[str] = mapped_column(String(72), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(72), ForeignKey("application_accounts.id", ondelete="RESTRICT"))
    external_conversation_id: Mapped[str] = mapped_column(String(512))
    provider_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("memory_providers.id", ondelete="RESTRICT"))
    scope_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("bot_memory_scopes.id", ondelete="RESTRICT"))
    scope_version: Mapped[int | None] = mapped_column(Integer)
    use_memory: Mapped[bool] = mapped_column(Boolean)
    save_on_request: Mapped[bool] = mapped_column(Boolean)

    def binding(self) -> BotMemoryBinding:
        return BotMemoryBinding(
            account_id=self.account_id,
            external_conversation_id=self.external_conversation_id,
            provider_id=self.provider_id,
            scope_id=self.scope_id,
            scope_version=self.scope_version,
            use_memory=self.use_memory,
            save_on_request=self.save_on_request,
        )


async def require_binding(session: AsyncSession, run_id: str) -> BotMemoryBinding:
    row = await session.get(RunMemoryBindingRecord, run_id)
    if row is None:
        raise RunAcceptanceError("memory_binding_missing", "Accepted conversation binding is missing")
    try:
        binding = row.binding()
    except ValidationError as error:
        raise RunAcceptanceError("memory_binding_invalid", "Accepted conversation binding is invalid") from error
    run = await session.get(RunRecord, run_id)
    owner = await session.get(SessionRecord, run.session_id) if run else None
    account = await session.get(AccountRecord, binding.account_id)
    if (
        owner is None
        or account is None
        or (account.organization_id, account.workspace_id) != (owner.organization_id, owner.workspace_id)
    ):
        raise RunAcceptanceError("memory_binding_invalid", "Conversation binding has invalid ownership")
    if binding.provider_id is not None:
        provider = await session.get(MemoryProviderRecord, binding.provider_id)
        if provider is None or (provider.organization_id, provider.workspace_id) != (
            owner.organization_id,
            owner.workspace_id,
        ):
            raise RunAcceptanceError("memory_binding_invalid", "Conversation provider has invalid ownership")
    if binding.scope_id is not None:
        scope = await session.get(ScopeRecord, binding.scope_id)
        if scope is None or (
            scope.account_id,
            scope.provider_id,
            scope.external_conversation_id,
            scope.organization_id,
            scope.workspace_id,
        ) != (
            account.id,
            binding.provider_id,
            binding.external_conversation_id,
            owner.organization_id,
            owner.workspace_id,
        ):
            raise RunAcceptanceError("memory_binding_invalid", "Conversation scope has invalid ownership")
    return binding


async def bind(session: AsyncSession, run_id: str, binding: BotMemoryBinding) -> None:
    if await session.get(RunMemorySelectionRecord, run_id) is not None:
        raise RunAcceptanceError("memory_binding_conflict", "Accepted memory selection cannot be replaced")
    session.add(RunMemoryBindingRecord(run_id=run_id, **binding.model_dump()))
    session.add(
        RunMemorySelectionRecord(run_id=run_id, behavior_key=BEHAVIOR_KEY, binding_schema_version=SCHEMA_VERSION)
    )
    await session.flush()
