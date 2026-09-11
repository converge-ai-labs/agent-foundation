"""Authenticated, read-only durable evidence for test-owned Thread inboxes."""

from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.models import SessionRecord, ThreadRecord
from a13n_service.request_runtime import get_process_runtime
from a13n_service.storage import short_session
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select


def inbox_router(config, authenticate):
    router = APIRouter(prefix="/__live__")

    authentication = Depends(authenticate)

    @router.get("/threads/{thread_id}/inbox")
    async def inbox(thread_id: str, request: Request, actor=authentication):
        if actor.workspace_id != config["workspace_id"]:
            raise HTTPException(403, "Fixture evidence belongs to the primary test Workspace")
        runtime = get_process_runtime(request)
        assert runtime is not None
        async with short_session(runtime.shared.storage.sessions) as database:
            owned = await database.scalar(
                select(ThreadRecord.id)
                .join(SessionRecord, ThreadRecord.session_id == SessionRecord.id)
                .where(
                    ThreadRecord.id == thread_id,
                    ThreadRecord.organization_id == config["organization_id"],
                    SessionRecord.workspace_id == config["workspace_id"],
                )
            )
            if owned is None:
                raise HTTPException(404, "Test Thread not found")
            rows = await database.scalars(
                select(ThreadInboxRecord)
                .where(
                    ThreadInboxRecord.organization_id == config["organization_id"],
                    ThreadInboxRecord.thread_id == thread_id,
                )
                .order_by(ThreadInboxRecord.delivery_sequence)
            )
            return {"items": [row.to_resource().model_dump(mode="json") for row in rows]}

    return router
