"""Short, generation-fenced transactions for application-owned MCP OAuth accounts."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from a13n_harness_ui.mcp_oauth.models import OAuthAccount, OAuthBinding, OAuthBundle, OAuthError

from .database import DatabaseSessions, short_session, transaction
from .models import McpOAuthRecord


class McpOAuthRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def bind(self, owner: str, configuration_digest: str, *, replace_configuration: bool = False) -> OAuthBinding:
        async with transaction(self._sessions) as session:
            record = await session.scalar(select(McpOAuthRecord).where(McpOAuthRecord.owner == owner))
            if record is None:
                record = McpOAuthRecord(
                    binding_id=f"mcpa_{uuid4().hex}",
                    owner=owner,
                    configuration_digest=configuration_digest,
                    generation=uuid4().hex,
                    credential_version=0,
                    refresh_pending=False,
                    bundle_json=None,
                )
                session.add(record)
            elif record.configuration_digest != configuration_digest:
                if not replace_configuration:
                    raise OAuthError(
                        "The MCP configuration changed; start a new operation.", code="mcp_oauth_account_changed"
                    )
                record.configuration_digest = configuration_digest
                _retire(record)
            return OAuthBinding(binding_id=record.binding_id, generation=record.generation)

    async def get(self, binding: OAuthBinding) -> OAuthAccount:
        async with short_session(self._sessions) as session:
            record = await session.get(McpOAuthRecord, binding.binding_id)
            _require_generation(record, binding)
            assert record is not None
            return OAuthAccount(
                binding=binding,
                owner=record.owner,
                configuration_digest=record.configuration_digest,
                credential_version=record.credential_version,
                refresh_pending=record.refresh_pending,
                bundle=OAuthBundle.model_validate_json(record.bundle_json) if record.bundle_json else None,
            )

    async def replace(self, binding: OAuthBinding) -> OAuthBinding:
        """Explicit reconnect retires the old account before waiting for consent."""
        async with transaction(self._sessions) as session:
            record = await session.get(McpOAuthRecord, binding.binding_id)
            _require_generation(record, binding)
            assert record is not None
            _retire(record)
            return OAuthBinding(binding_id=record.binding_id, generation=record.generation)

    async def save(self, binding: OAuthBinding, bundle: OAuthBundle, *, expected_version: int) -> None:
        async with transaction(self._sessions) as session:
            record = await session.get(McpOAuthRecord, binding.binding_id)
            _require_generation(record, binding)
            assert record is not None
            _require_version(record, expected_version)
            record.bundle_json = bundle.model_dump_json()
            record.credential_version += 1
            record.refresh_pending = False

    async def begin_refresh(self, binding: OAuthBinding, *, expected_version: int) -> None:
        """Commit uncertainty BEFORE sending a possibly one-use refresh credential."""
        async with transaction(self._sessions) as session:
            record = await session.get(McpOAuthRecord, binding.binding_id)
            _require_generation(record, binding)
            assert record is not None
            _require_version(record, expected_version)
            if record.refresh_pending:
                raise OAuthError(
                    "The previous token refresh has an unknown outcome; reconnect.", code="mcp_oauth_reconnect"
                )
            record.refresh_pending = True

    async def remove(self, owner: str) -> None:
        async with transaction(self._sessions) as session:
            record = await session.scalar(select(McpOAuthRecord).where(McpOAuthRecord.owner == owner))
            if record is not None:
                _retire(record)
                record.configuration_digest = ""

    async def reconcile(self, prefix: str, owners: set[str]) -> None:
        """Remove accounts only after the owning configuration generation was accepted."""
        async with transaction(self._sessions) as session:
            records = (await session.scalars(select(McpOAuthRecord))).all()
            for record in records:
                if record.owner.startswith(prefix) and record.owner not in owners:
                    _retire(record)
                    record.configuration_digest = ""


def _retire(record: McpOAuthRecord) -> None:
    record.generation = uuid4().hex
    record.credential_version = 0
    record.refresh_pending = False
    record.bundle_json = None


def _require_generation(record: McpOAuthRecord | None, binding: OAuthBinding) -> None:
    if record is None or record.generation != binding.generation:
        raise OAuthError(
            "This MCP account was removed or replaced; start a new operation.", code="mcp_oauth_account_changed"
        )


def _require_version(record: McpOAuthRecord, expected: int) -> None:
    if record.credential_version != expected:
        raise OAuthError("The MCP credentials changed; retry the operation.", code="mcp_oauth_credential_conflict")
