"""Opt-in built-in compaction and read-only checkpoint evidence."""

from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.request_runtime import get_process_runtime
from a13n_service.storage import short_session
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic_ai.messages import ModelMessagesTypeAdapter
from sqlalchemy import select

from .long_session_model import history_evidence


def install_compaction():
    """Select the real built-in Capability only in this opt-in test Worker."""
    from a13n_harness.capabilities import CompactionCapability
    from a13n_service.agents.reconstruction import AgentReconstructor
    from a13n_service.interactions import worker_preparation

    class CompactionReconstructor(AgentReconstructor):
        def _provided_capabilities(self, node):
            return (*super()._provided_capabilities(node), CompactionCapability())

    worker_preparation.AgentReconstructor = CompactionReconstructor


def measurements_router(config, authenticate):
    router = APIRouter(prefix="/__live__/session")
    authentication = Depends(authenticate)

    @router.get("/runs/{run_id}")
    async def measurements(run_id: str, request: Request, actor=authentication):
        if actor.workspace_id != config["workspace_id"]:
            raise HTTPException(403, "Measurements belong to the primary test Workspace")
        runtime = get_process_runtime(request)
        assert runtime is not None
        storage = runtime.shared.storage
        async with short_session(storage.sessions) as database:
            row = await database.scalar(
                select(RunRecord)
                .join(SessionRecord, RunRecord.session_id == SessionRecord.id)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.organization_id == config["organization_id"],
                    SessionRecord.workspace_id == config["workspace_id"],
                )
            )
            if row is None:
                raise HTTPException(404, "Test Run not found")
            run = row.to_resource()
        stored = await RunStateStore(storage.objects).read_run(run)
        return checkpoint_evidence(stored.envelope, len(stored.body), attempts_started=run.attempts_started)

    return router


def checkpoint_evidence(state, size, *, attempts_started):
    history = state.harness.message_history
    evidence = history_evidence(ModelMessagesTypeAdapter.dump_json(list(history)).decode())
    return {
        "state_bytes": size,
        "messages": len(history),
        "attempts_started": attempts_started,
        "compacted": any((message.metadata or {}).get("a13n.context") == "compaction" for message in history),
        **evidence,
    }
