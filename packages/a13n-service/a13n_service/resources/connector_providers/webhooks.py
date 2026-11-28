"""Fenced setup and recovery of a Provider's single shared event callback."""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import or_, select

from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.tools.discovery import unavailable
from a13n_service.resources.connector_providers.events import open_events
from a13n_service.resources.providers.service import read_provider, resolve_provider
from a13n_service.resources.providers.tables import ConnectorProviderRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.resources.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


class Webhook(BaseModel):
    provider_id: str
    url: str | None
    remote_id: str | None
    configured: bool
    secret_version: int
    operation: str | None
    failure: str | None
    retryable: bool
    version: int


def callback_url(runtime: Runtime, provider_id: str) -> str:
    base = runtime.settings.connector_events.public_url
    if base is None:
        raise conflict("connector_provider", provider_id, "event_public_url_not_configured")
    return f"{base.rstrip('/')}/api/v1/connector-webhooks/{provider_id}"


def location(row: ConnectorProviderRow) -> SecretLocation:
    return SecretLocation(row.organization_id, "connector_providers", "event_secret", row.id)


def secret(runtime: Runtime, row: ConnectorProviderRow) -> str:
    if row.event_secret is None:
        raise conflict(row.KIND, row.id, "event_webhook_not_configured")
    return runtime.keys.reveal(Envelope.model_validate(row.event_secret), location(row)).decode()


def retryable(row: ConnectorProviderRow, runtime: Runtime, current: datetime) -> bool:
    operation = row.event_operation
    return bool(
        operation
        and operation.get("attempts", 1) >= runtime.settings.connector_events.max_attempts
        and ((row.event_config or {}).get("failure") or datetime.fromisoformat(operation["deadline"]) <= current)
    )


def view(row: ConnectorProviderRow, runtime: Runtime) -> Webhook:
    config, operation = row.event_config or {}, row.event_operation or {}
    return Webhook(
        provider_id=row.id,
        url=callback_url(runtime, row.id) if runtime.settings.connector_events.public_url else None,
        remote_id=row.event_webhook_id,
        configured=row.event_secret is not None,
        secret_version=config.get("secret_version", 0),
        operation=operation.get("kind"),
        failure=config.get("failure"),
        retryable=retryable(row, runtime, datetime.now(UTC)),
        version=row.version,
    )


async def get(runtime: Runtime, actor: Principal, workspace_id: str, provider_id: str) -> Webhook:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, ConnectorProviderRow, scope, provider_id, "read")
        return view(row, runtime)


async def configure(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    *,
    if_match: str | None,
    rotate: bool = False,
) -> Webhook:
    url = callback_url(runtime, provider_id)
    async with transaction(runtime.storage) as session:
        # Shared signing configuration and service-account selection require administrative authority.
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        await resolve_provider(session, actor, ConnectorProviderRow, scope, provider_id)
        row = await find_row(session, actor, ConnectorProviderRow, scope, provider_id, "admin", lock=True)
        require_match(if_match, row.id, row.version)
        if row.event_operation is not None:
            if not retryable(row, runtime, await now(session)):
                raise conflict(row.KIND, row.id, "operation_in_progress")
            # Unknown rotation outcomes are recovered by reading the current key.
            rotate = False
        operation_id = new_object_id("ceop")
        current = await now(session)
        row.event_config = {key: value for key, value in (row.event_config or {}).items() if key != "failure"}
        row.event_operation = {
            "id": operation_id,
            "attempts": 1,
            "kind": "rotate" if rotate else "configure",
            "url": url,
            "deadline": (current + timedelta(seconds=2 * runtime.settings.providers.tool_call_seconds)).isoformat(),
        }
        audit_row(session, actor, row, "event_webhook.configure")
    await perform(runtime, provider_id, operation_id, rotate=rotate)
    return await get(runtime, actor, workspace_id, provider_id)


async def perform(runtime: Runtime, provider_id: str, operation_id: str, *, rotate: bool) -> None:
    async with short_session(runtime.storage) as session:
        row = await session.get(ConnectorProviderRow, provider_id)
        if row is None or not row.event_operation or row.event_operation["id"] != operation_id:
            return
        operation, version, existing_id = dict(row.event_operation), row.version, row.event_webhook_id
        provider = await read_provider(session, ConnectorProviderRow, provider_id)
    try:
        async with open_events(runtime, provider) as adapter:
            result = await adapter.configure_webhook(operation["url"], existing_id, rotate=rotate)
    except Exception as error:
        # A timeout may have changed the upstream secret. Recovery reads it instead of rotating again.
        async with transaction(runtime.storage) as session:
            row = await session.scalar(
                select(ConnectorProviderRow).where(ConnectorProviderRow.id == provider_id).with_for_update()
            )
            if row is not None and row.event_operation and row.event_operation["id"] == operation_id:
                row.event_config = {**(row.event_config or {}), "failure": "upstream_operation_unconfirmed"}
        raise unavailable(f"connector:{provider.type}", error) from None
    async with transaction(runtime.storage) as session:
        row = await session.scalar(
            select(ConnectorProviderRow).where(ConnectorProviderRow.id == provider_id).with_for_update()
        )
        if row is None or not row.event_operation or row.event_operation["id"] != operation_id:
            raise conflict("connector_provider", provider_id, "superseded")
        if row.version != version:
            # Do not publish a result fetched through replaced provider credentials.
            raise conflict(row.KIND, row.id, "provider_changed")
        row.event_webhook_id = result.id
        row.event_secret = runtime.keys.protect(result.secret.get_secret_value().encode(), location(row)).model_dump(
            mode="json"
        )
        row.event_config = {"url": result.url, "secret_version": (row.event_config or {}).get("secret_version", 0) + 1}
        row.event_operation = None
        await session.flush()


async def recover(runtime: Runtime) -> None:
    async with short_session(runtime.storage) as session:
        rows = (
            await session.execute(
                select(ConnectorProviderRow.id, ConnectorProviderRow.event_operation)
                .where(
                    ConnectorProviderRow.event_operation.is_not(None),
                    or_(
                        ConnectorProviderRow.event_operation["attempts"].as_integer()
                        < runtime.settings.connector_events.max_attempts,
                        ConnectorProviderRow.event_config["failure"].as_string().is_(None),
                    ),
                )
                .order_by(ConnectorProviderRow.event_operation["deadline"].as_string())
                .limit(runtime.settings.connector_events.batch)
            )
        ).all()
    for provider_id, operation in rows:
        if datetime.fromisoformat(operation["deadline"]) > datetime.now(UTC):
            continue
        async with transaction(runtime.storage) as session:
            row = await session.scalar(
                select(ConnectorProviderRow).where(ConnectorProviderRow.id == provider_id).with_for_update()
            )
            if row is None or row.event_operation != operation:
                continue
            if operation.get("attempts", 1) >= runtime.settings.connector_events.max_attempts:
                row.event_config = {**(row.event_config or {}), "failure": "upstream_operation_unconfirmed"}
                row.event_operation = {**operation, "id": new_object_id("ceop")}
                continue
            claim = new_object_id("ceop")
            row.event_config = {key: value for key, value in (row.event_config or {}).items() if key != "failure"}
            row.event_operation = {
                **operation,
                "id": claim,
                "attempts": operation.get("attempts", 1) + 1,
                "deadline": (
                    await now(session) + timedelta(seconds=2 * runtime.settings.providers.tool_call_seconds)
                ).isoformat(),
            }
        try:
            await perform(runtime, provider_id, claim, rotate=False)
        except Exception:
            # The persisted operation and safe failure remain visible; the next bounded sweep retries.
            continue
