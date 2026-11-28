"""Event-to-Inbox handoff: current authority, Event lock, native admission, one commit."""

from datetime import timedelta

from a13n_logging import get_logger
from sqlalchemy import select, update

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.resources.connections.access import ConnectorConnection, resolve_connection
from a13n_service.runs import submit
from a13n_service.runs.connector_events import metrics
from a13n_service.runs.connector_events.tables import EventRow
from a13n_service.runs.connector_events.triggers.schemas import EventThreadTarget as Target
from a13n_service.runs.connector_events.triggers.service import account
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import NewThread
from a13n_service.tenancy.authorize import WorkspaceScope

logger = get_logger(__name__)


async def dispatch(runtime: Runtime) -> None:
    async with short_session(runtime.storage) as session:
        ids = list(
            await session.scalars(
                select(EventRow.id)
                .where(EventRow.state == "pending", EventRow.due_at <= await now(session))
                .order_by(EventRow.due_at, EventRow.id)
                .limit(runtime.settings.connector_events.batch)
            )
        )
    for event_id in ids:
        await deliver(runtime, event_id)


async def deliver(runtime: Runtime, event_id: str) -> None:
    async with short_session(runtime.storage) as session:
        event = await session.get(EventRow, event_id)
        if event is None or event.state != "pending" or event.snapshot is None:
            return
        snapshot, workspace_id, version = dict(event.snapshot), event.workspace_id, event.version
        attempts = event.attempts
    try:
        target = Target.model_validate(snapshot["target"])
        async with transaction(runtime.storage) as session:
            admission = await submit.prepare_submission(session, runtime, workspace_id, target.principal_id)
            event = await session.scalar(select(EventRow).where(EventRow.id == event_id).with_for_update())
            if event is None or event.state != "pending" or event.due_at > await now(session):
                return
            actor = submit.submission_actor(session, runtime, admission, workspace_id)
            if actor.kind != "service_account":
                raise conflict("connector_event", event.id, "service_account_required")
            scope = WorkspaceScope(event.organization_id, event.workspace_id)
            connection = await resolve_connection(session, actor, scope, snapshot["connection_id"], verb="run")
            if not isinstance(connection, ConnectorConnection):
                raise conflict("connector_event", event.id, "connection_changed")
            bound = account(runtime, connection)
            if (connection.provider.id, bound.account_ref, bound.correlation) != (
                event.provider_id,
                event.account_id,
                event.user_id,
            ):
                raise conflict("connector_event", event.id, "connection_changed")
            body = NewThread.model_validate(
                {
                    "agent_id": target.agent_id,
                    "agent_revision_id": target.agent_revision_id,
                    "payload": {
                        "content": [
                            {"type": "text", "text": target.instruction},
                            {
                                "type": "json",
                                "value": {
                                    "event_id": event.id,
                                    "event_type": event.event_type,
                                    "occurred_at": event.occurred_at.isoformat(),
                                    "data": event.payload,
                                },
                            },
                        ]
                    },
                }
            )
            receipt = await submit.create_thread_in_transaction(
                session, runtime, admission, workspace_id, body, request_key=f"connector-event:{event.id}"
            )
            event.state, event.failure = "submitted", None
            event.thread_id, event.inbox_id = receipt.thread.id, receipt.entry.id
            await session.flush()
            elapsed = (await now(session) - event.created_at).total_seconds()
        metrics.deliveries.add(1, {"state": "submitted"})
        metrics.latency.record(elapsed)
        logger.info("Connector event submitted to Inbox", extra={"event_id": event_id, "thread_id": receipt.thread.id})
    except Exception as error:
        transient = (
            not isinstance(error, ServiceError)
            or error.code in {"unavailable", "rate_limited"}
            or (error.code == "conflict" and error.details.get("reason") == "initial_run_not_accepted")
        )
        reason = error.code if isinstance(error, ServiceError) else "delivery_failed"
        attempts += 1
        state = "pending" if transient and attempts < runtime.settings.connector_events.max_attempts else "failed"
        async with transaction(runtime.storage) as session:
            await session.execute(
                update(EventRow)
                .where(EventRow.id == event_id, EventRow.state == "pending", EventRow.version == version)
                .values(
                    state=state,
                    failure=reason,
                    attempts=attempts,
                    due_at=await now(session) + timedelta(seconds=min(300, 2 ** min(attempts, 8))),
                )
            )
        metrics.deliveries.add(1, {"state": state})
        logger.warning(
            "Connector event delivery failed", extra={"event_id": event_id, "reason": reason, "state": state}
        )
