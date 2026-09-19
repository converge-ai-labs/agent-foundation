"""Record one progress message atomically with inbound Run acceptance."""

import secrets
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.ingress.admission_domain import PreparedIngressBatch
from a13n_service.interactions.control_domain import RunAcceptanceReceipt, SteerReceipt

from .models import ProgressRecord


async def accept_progress(
    session: AsyncSession, batch: PreparedIngressBatch, receipt: RunAcceptanceReceipt | SteerReceipt, now: datetime
) -> None:
    config = batch.configuration
    if (
        config.provider_key not in {"lark", "slack"}
        or f"{config.provider_key}.reply" not in config.native_actions
        or isinstance(receipt, SteerReceipt)
    ):
        return
    context = config.provider_context
    slack = config.provider_key == "slack"
    message_id = context.get("root_thread_ts" if slack else "message_id")
    chat_id = context.get("channel_id" if slack else "chat_id")
    direct = context.get("conversation_kind") == "im" if slack else context.get("chat_type") == "p2p"
    structured = batch.agent_input.structured_content
    events = structured.get("events", []) if isinstance(structured, dict) else []
    quiet = False
    # Whole-chat listening can intentionally stay silent unless explicitly addressed.
    if config.provider_policy.get("interaction_mode") == "chat" and not direct:
        if not isinstance(events, list) or not any(
            isinstance(event, dict)
            and isinstance(event_context := event.get("context"), dict)
            and event_context.get("mentioned") is True
            for event in events
        ):
            quiet = True
    requesters: set[str] = set()
    if isinstance(events, list):
        for event in events:
            actor = event.get("actor") if isinstance(event, dict) else None
            identifier = actor.get("user_id" if slack else "open_id") if isinstance(actor, dict) else None
            if isinstance(identifier, str) and 0 < len(identifier) <= 256:
                requesters.add(identifier)
    if not requesters or not isinstance(message_id, str) or not isinstance(chat_id, str):
        return
    session.add(
        ProgressRecord(
            run_id=receipt.run_id,
            account_id=config.account_id,
            account_version=config.account_version,
            provider_key=config.provider_key,
            conversation_id=chat_id,
            source_message_id=message_id,
            reply_in_thread=(
                config.provider_policy.get("reply_mode", "thread") == "thread"
                or (config.provider_policy.get("reply_mode") == "auto" and not direct)
            )
            if slack
            else not direct and config.provider_policy.get("reply_mode", "thread") != "main",
            requester_ids=sorted(requesters),
            action_token=secrets.token_urlsafe(32),
            done=quiet,
            available_at=now,
            created_at=now,
        )
    )
