"""Slack confirmation and management cards with fenced, bounded delivery."""

import json
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anyio
import httpx2
from a13n_harness.http import ProviderHttpError
from pydantic import JsonValue
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.connectors.management import decode_credentials
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.providers.slack.client import SlackNativeClient
from a13n_service.secrets import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import assume_utc, utc_now

from .domain import ProposeRoutine, RoutineDefinition
from .models import RoutineRecord

ACTION_PREFIX = "a13n.routine.v1."
OPERATIONS = frozenset({"confirm", "cancel", "pause", "resume", "delete"})


def render(row: RoutineRecord) -> JsonObject:
    proposal = ProposeRoutine.model_validate(row.proposal_json) if row.proposal_json else None
    definition = (
        proposal.definition
        if proposal and proposal.definition
        else (RoutineDefinition.model_validate(row.definition_json) if row.definition_json else None)
    )
    title = definition.title if definition else "Scheduled task"
    state = (
        {"save": "Confirm task", "pause": "Confirm pause", "resume": "Confirm resume", "delete": "Confirm deletion"}[
            proposal.operation
        ]
        if proposal
        else "Schedule finished · Run submitted"
        if row.state == "completed"
        else row.state.capitalize()
    )

    def display_time(value: datetime) -> str:
        zone = ZoneInfo(definition.schedule.timezone) if definition else ZoneInfo("UTC")
        return f"{assume_utc(value).astimezone(zone):%Y-%m-%d %H:%M} ({zone.key})"

    text = f"{title} — {state}"
    details = [definition.schedule.describe(), definition.prompt] if definition else []
    details.append(f"Destination: this channel · Owner: {row.owner_id}")
    if proposal and definition:
        next_at = definition.schedule.next_after(utc_now())
        details.append(
            f"Proposed next run: {display_time(next_at) if next_at else 'Time has expired; propose a new time'}"
        )
    elif row.next_run_at:
        details.append(f"Next run: {display_time(row.next_run_at)}")
    if proposal:
        details.append("This change takes effect only after confirmation. Confirmation expires after 24 hours.")
    if row.last_error:
        details.append(f"Needs attention: {row.last_error}")
    blocks: list[JsonValue] = [{"type": "header", "text": {"type": "plain_text", "text": title}}]
    blocks.append({"type": "context", "elements": [{"type": "plain_text", "text": state}]})
    for detail in details:
        for offset in range(0, len(detail), 2800):
            blocks.append({"type": "section", "text": {"type": "plain_text", "text": detail[offset : offset + 2800]}})
    operations = (
        [("confirm", "Confirm"), ("cancel", "Discard change")]
        if proposal
        else (
            [("pause", "Pause"), ("delete", "Delete")]
            if row.state == "active"
            else [("resume", "Resume"), ("delete", "Delete")]
            if row.state == "paused"
            else []
        )
    )
    actions: list[JsonValue] = []
    for operation, label in operations:
        button: JsonObject = {
            "type": "button",
            "action_id": ACTION_PREFIX + operation,
            "text": {"type": "plain_text", "text": label},
            "value": json.dumps({"routine_id": row.id, "token": row.action_token}),
        }
        if operation == "delete":
            button["confirm"] = {
                "title": {"type": "plain_text", "text": "Delete scheduled task?"},
                "text": {"type": "plain_text", "text": "Future runs stop. Already accepted runs are not cancelled."},
                "confirm": {"type": "plain_text", "text": "Delete"},
                "deny": {"type": "plain_text", "text": "Keep task"},
            }
        actions.append(button)
    if actions:
        blocks.append({"type": "actions", "elements": actions})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "plain_text",
                    "text": "To edit, @mention the bot with the task ID and your changes. Only the creator can confirm or manage it. "
                    f"Task: {row.id}",
                }
            ],
        }
    )
    return {"text": text, "blocks": blocks}


class RoutineCards:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], http: httpx2.AsyncClient, protector: SecretProtector
    ) -> None:
        self.sessions, self.http, self.protector = sessions, http, protector

    async def publish_one(self) -> bool:
        now = utc_now()
        lease = secrets.token_urlsafe(24)
        async with transaction(self.sessions) as session:
            row = await session.scalar(
                select(RoutineRecord)
                .where(
                    RoutineRecord.card_available_at <= now,
                    or_(RoutineRecord.card_lease_until.is_(None), RoutineRecord.card_lease_until <= now),
                )
                .order_by(RoutineRecord.card_available_at, RoutineRecord.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return False
            account = await session.get(AccountRecord, row.account_id)
            if (
                account is None
                or account.status != "active"
                or account.deleted_at is not None
                or account.version != row.account_version
            ):
                row.card_error = "account_unavailable"
                row.card_available_at = None
                return True
            if row.message_id is None and row.card_started_at is not None:
                # Slack has no durable postMessage deduplication guarantee. Do not duplicate an uncertain write.
                row.card_error = "delivery_outcome_unknown"
                row.card_available_at = None
                return True
            row.card_lease_token = lease
            row.card_lease_until = now + timedelta(seconds=60)
            row.card_started_at = now
            identifier, version, message_id = row.id, row.version, row.message_id
            payload = {**render(row), "channel": row.conversation_id, "unfurl_links": False, "unfurl_media": False}
            if message_id:
                payload["ts"] = message_id
            credentials = decode_credentials(account.credential_snapshot().decrypt(self.protector))
            token = credentials.get("bot_token")
            if not isinstance(token, str):
                row.card_error = "credentials_unavailable"
                row.card_available_at = None
                return True
        error = None
        definite_rejection = False
        try:
            with anyio.fail_after(20):
                message_id = await SlackNativeClient(self.http).publish_progress(
                    payload, bot_token=token, message_id=message_id
                )
        except (ProviderHttpError, httpx2.HTTPError, TimeoutError) as failure:
            error = "delivery_failed"
            definite_rejection = isinstance(failure, ProviderHttpError) and failure.code in {
                "provider_rejected",
                "rate_limited",
                "invalid_arguments",
                "endpoint_denied",
            }
        async with transaction(self.sessions) as session:
            row = await session.get(RoutineRecord, identifier, with_for_update=True)
            if row is None or row.card_lease_token != lease:
                return True
            row.card_lease_until = None
            row.card_lease_token = None
            row.card_error = error
            if error:
                row.card_attempts += 1
                if definite_rejection and row.message_id is None:
                    row.card_started_at = None
                row.card_available_at = (
                    utc_now() + timedelta(seconds=min(60, 2**row.card_attempts)) if row.card_attempts < 5 else None
                )
            else:
                row.message_id = message_id
                row.card_version = version
                row.card_attempts = 0
                row.card_available_at = None if row.version == version else utc_now()
        return True
